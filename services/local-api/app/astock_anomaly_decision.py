"""S4: map A-share anomalies to trade decisions and record them.

The S3a detector publishes :class:`AnomalyEvent` onto the shared bus; until
this module nothing downstream recorded a *decision*. A pure ``build_intent``
mapper turns an anomaly into a :class:`TradeIntent`, and a bus consumer records
one ``anomaly_decision`` entry per anomaly into the production hash-chain
ledger.

Two modes are legal:

* ``observe`` (default) — record only. No intent is built, no proposal exists.
* ``suggest`` — additionally build the intent and park it as a **pending
  proposal** awaiting a human verdict. Approving a proposal appends an
  ``anomaly_approval`` record to the same ledger with ``executes_order=False``:
  it records that a human confirmed the read, it does **not** place an order.
  Nothing in this module reaches an execution path.

``auto`` is a startup ``ValueError``, not a legal-but-inert enum value (a silent
enum would be a green-light-on-a-false-premise trap). It stays illegal until
the plan §5 preconditions land: A-share limit/T+1 rules in the production
``RiskEngine`` roster, a real-money check on the A-share execution path, and a
second security review. Until then ``auto`` would be an ungated order path.

The pending registry is deliberately **not** bound to an agent turn: anomalies
belong to a symbol, not to a chat session, and a turn is TTL-bound (300s) and
superseded by the user's next message. It lives on the consumer — in-memory by
design, so a restart drops every pending proposal (fail-closed, same stance as
the tool-approval gate's ``pending_approval``).

Layering: this lives in local-api (service layer) so it may see the risk
package's calendar/ledger types; market-data stays untouched.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from selene_space_types.agent import OrderType, TradeIntent
from selene_space_types.market import AnomalyEvent
from selene_space_types.trading import OrderSide

logger = logging.getLogger(__name__)

ANOMALY_MODE_ENV = "SELENE_ANOMALY_MODE"

# Pending proposals are bounded by (reachable symbols × 7 kinds) per session
# thanks to dedup, so this cap is a backstop against an unbounded symbol set,
# not a working limit. Eviction is oldest-first and logged — never silent.
_PROPOSAL_CAP = 500

# Fixed and documented: the anomaly is a structural signal (limit touch /
# N-day extreme), not a forecast, so v1 carries a modest constant confidence.
_INTENT_CONFIDENCE = 0.5

# China has no DST — fixed +08:00, same convention as the detector/wiring.
_CST_TZ = timezone(timedelta(hours=8))

# kind → decision side. ``None`` = record-only (no intent). Tune by editing
# this dict only; everything downstream reads it.
KIND_DECISION_MAP: dict[str, OrderSide | None] = {
    "limit_up": OrderSide.BUY,
    "limit_down_recovery": OrderSide.BUY,
    "n_day_high": OrderSide.BUY,
    "broken_limit_up": None,
    "limit_down": None,
    "n_day_low": None,
    "volume_surge": None,
}


_LEGAL_MODES = ("observe", "suggest")


def resolve_anomaly_mode(raw: str | None) -> str:
    """Parse ``SELENE_ANOMALY_MODE``: unset → observe; observe/suggest are legal.

    ``auto`` is rejected on purpose (see module docstring): it would be an
    ungated order path, and no real-money gate exists for A-shares yet.
    """
    mode = raw if raw is not None else "observe"
    if mode not in _LEGAL_MODES:
        raise ValueError(
            f"{ANOMALY_MODE_ENV} only accepts {_LEGAL_MODES!r} (got {raw!r}); "
            "'auto' needs the plan §5 sign-off first (A-share rules in the "
            "production RiskEngine roster + a real-money gate on the A-share "
            "execution path + a second security review)"
        )
    return mode


def build_intent(
    event: AnomalyEvent, *, capital_base: float, quantity_pct: float
) -> TradeIntent | None:
    """Map one anomaly to a :class:`TradeIntent`, or ``None`` when no action.

    Pure function: returns a new object, never mutates ``event``. Sizing is
    share-lot quantized — ``floor((capital_base * quantity_pct) / price / 100)
    * 100`` shares — so the notional lands within one lot of the budget.
    Record-only kinds, non-positive/missing prices and sub-lot capital all
    return ``None``.
    """
    side = KIND_DECISION_MAP.get(event.kind)
    if side is None:
        return None
    if event.price is None or event.price <= 0:
        return None
    budget = capital_base * quantity_pct
    quantity = math.floor(budget / event.price / 100) * 100
    if quantity <= 0:
        return None
    return TradeIntent(
        symbol=event.symbol,
        side=side,
        quantity=float(quantity),
        order_type=OrderType.LIMIT,
        price=event.price,
        reasoning=(
            f"A-share anomaly {event.kind} at {event.price} "
            f"(prev_close={event.prev_close}); proposed intent"
        ),
        confidence=_INTENT_CONFIDENCE,
        timestamp=event.timestamp,
    )


class _DecisionLedger(Protocol):
    """The slice of :class:`HashChainLedger` the consumer needs."""

    def append(self, payload: dict[str, Any]) -> Any: ...
    def read_chain(self) -> list[Any]: ...


@dataclass(frozen=True)
class PendingProposal:
    """One anomaly the suggest tier proposes acting on, awaiting a verdict.

    Frozen so a caller (or a test) cannot mutate a parked proposal in place;
    the registry replaces entries instead. The mapping from the source
    :class:`AnomalyEvent` is flattened into these scalar fields on purpose —
    the :class:`TradeIntent` itself is *not* retained: it has served its one
    purpose (kind → side, share-lot sizing) by the time the proposal exists,
    and a parked intent would read as an executable object this module never
    has. Approving the proposal never executes it.
    """

    dedup_id: str
    symbol: str
    kind: str
    session_date: str
    price: float
    side: str
    quantity: float
    created_at_ms: int

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe view for the read endpoint (enum → its wire value)."""
        return {
            "dedup_id": self.dedup_id,
            "symbol": self.symbol,
            "kind": self.kind,
            "session_date": self.session_date,
            "price": self.price,
            "side": self.side,
            "quantity": self.quantity,
            "created_at_ms": self.created_at_ms,
        }


class AnomalyDecisionConsumer:
    """Bus consumer: one deduped ledger record per anomaly.

    In ``observe`` every anomaly is recorded and nothing else happens. In
    ``suggest`` an anomaly whose kind maps to a side is *additionally* parked
    as a :class:`PendingProposal` awaiting a human verdict; see :meth:`decide`.

    Synchronous handler (the source publishes inside the event loop's task),
    self-contained failure semantics: the bus swallows handler exceptions, so
    ``handle`` catches its own and logs loudly instead. Dedup is per
    (CST session_date, symbol, kind); the current session's entries are
    replayed from the append-ordered ledger at construction, so a restart
    within the same session does not re-record.

    The pending registry is in-memory and **not** tied to an agent turn: an
    anomaly belongs to a symbol, not a chat session, and turns are TTL-bound
    and superseded by the next message. A restart drops every pending proposal
    — fail-closed, matching the tool-approval gate.
    """

    def __init__(
        self,
        ledger: _DecisionLedger,
        *,
        capital_base: float,
        quantity_pct: float,
        mode: str | None = None,
    ) -> None:
        self._ledger = ledger
        self._capital_base = capital_base
        self._quantity_pct = quantity_pct
        self._mode = resolve_anomaly_mode(mode)
        self._seen: set[str] = self._replay_current_session()
        # dedup_id → proposal, insertion-ordered so eviction is oldest-first.
        self._pending: dict[str, PendingProposal] = {}

    # -- read surface ------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode

    def list_pending(self) -> list[PendingProposal]:
        """A snapshot copy of pending proposals, oldest first."""
        return list(self._pending.values())

    def _evict_oldest(self) -> None:
        oldest = next(iter(self._pending), None)
        if oldest is None:
            return
        self._pending.pop(oldest, None)
        logger.warning(
            "anomaly proposal registry full (%d) — evicted oldest dedup_id=%s",
            _PROPOSAL_CAP,
            oldest,
        )

    def decide(self, dedup_id: str, *, approve: bool) -> str:
        """Resolve one pending proposal; returns the ledger ``result``.

        Recording is the whole effect: approval appends an ``anomaly_approval``
        entry with ``executes_order=False`` and does **not** reach any
        execution path. An unknown or already-resolved id is reported as
        ``no_pending_proposal`` rather than silently succeeding — a replayed
        click must not read as a second approval.

        The proposal is dropped only *after* the append returns. A failed
        write (``LedgerCorruptionError``, or an ``OSError`` from the fsync)
        propagates instead of being swallowed, and the proposal stays pending
        so the operator can retry: popping first would destroy the only
        handle on a proposal whose dedup_id is already in ``_seen``, and the
        detector never re-publishes a duplicate — the confirmation would be
        unretryable for the rest of the session.
        """
        proposal = self._pending.get(dedup_id)
        if proposal is None:
            return "no_pending_proposal"
        result = "approved" if approve else "rejected"
        self._ledger.append(
            {
                "stage": "anomaly_approval",
                "result": result,
                "dedup_id": dedup_id,
                "kind": proposal.kind,
                "symbol": proposal.symbol,
                "session_date": proposal.session_date,
                "side": proposal.side,
                "quantity": proposal.quantity,
                "price": proposal.price,
                # Machine-checkable honesty marker: this record confirms a
                # human read the anomaly; it did not place an order. Any future
                # auto tier must flip this to True *and* acquire the gates the
                # module docstring lists — an auditor greps for this field.
                "executes_order": False,
            }
        )
        # Only now is the proposal resolved: the append above is the single
        # point of failure, and it raises before this line if it fails. A
        # build_intent failure inside _park, by contrast, is *not* symmetric:
        # the decision record is already durable by then, so that path is
        # handled where it happens, not here.
        self._pending.pop(dedup_id, None)
        return result

    # -- bus handler ------------------------------------------------------

    def handle(self, event: AnomalyEvent) -> None:
        """Record one anomaly. Never raises — the bus would swallow it."""
        try:
            self._handle(event)
        except Exception:
            logger.exception(
                "anomaly decision consumer failed for kind=%s symbol=%s ts=%s",
                getattr(event, "kind", "?"),
                getattr(event, "symbol", "?"),
                getattr(event, "timestamp", "?"),
            )

    # -- internals ---------------------------------------------------------

    def _handle(self, event: AnomalyEvent) -> None:
        session_date = self._session_date(event.timestamp)
        dedup_id = self._dedup_id(session_date, event.symbol, event.kind)
        result = "duplicate_suppressed" if dedup_id in self._seen else "observed"
        if result == "observed":
            self._seen.add(dedup_id)
        # The decision itself is not persisted here in either mode — only the
        # fact that the anomaly was consumed. In suggest mode the mapped intent
        # is parked as a pending proposal (see below); the ledger entry that
        # matters for it is written when a human decides.
        self._ledger.append(
            {
                "stage": "anomaly_decision",
                "result": result,
                "dedup_id": dedup_id,
                "kind": event.kind,
                "symbol": event.symbol,
                "session_date": session_date,
            }
        )
        # Suggest tier: park the mapped intent awaiting a human verdict. Only
        # fresh (non-duplicate) anomalies become proposals — a suppressed
        # duplicate must not resurrect a proposal the user already decided on,
        # and record-only kinds (KIND_DECISION_MAP → None) or sub-lot capital
        # yield no intent and therefore no proposal.
        if self._mode == "suggest" and result == "observed":
            self._park(session_date, dedup_id, event)

    def _park(self, session_date: str, dedup_id: str, event: AnomalyEvent) -> None:
        intent = build_intent(
            event,
            capital_base=self._capital_base,
            quantity_pct=self._quantity_pct,
        )
        if intent is None:
            return
        if len(self._pending) >= _PROPOSAL_CAP:
            self._evict_oldest()
        self._pending[dedup_id] = PendingProposal(
            dedup_id=dedup_id,
            symbol=event.symbol,
            kind=event.kind,
            session_date=session_date,
            price=float(event.price),
            side=intent.side.value,
            quantity=intent.quantity,
            created_at_ms=event.timestamp,
        )

    @staticmethod
    def _session_date(timestamp_ms: int) -> str:
        # The event's own timestamp in Beijing time — not "today": a
        # just-crossed-midnight process must still key by the session the
        # anomaly happened in.
        return (
            datetime.fromtimestamp(timestamp_ms / 1000, tz=_CST_TZ).date().isoformat()
        )

    @staticmethod
    def _dedup_id(session_date: str, symbol: str, kind: str) -> str:
        return f"{session_date}|{symbol}|{kind}"

    def _replay_current_session(self) -> set[str]:
        # The ledger is append-ordered by seq, so current-session entries are
        # a suffix: scan from the tail and stop at the first entry predating
        # the session. (Note: HashChainLedger.append is O(n) per write —
        # lifecycle O(n²); rotation planning is ADR material, plan §3.5.)
        today = datetime.now(_CST_TZ).date().isoformat()
        seen: set[str] = set()
        for entry in reversed(self._ledger.read_chain()):
            # The shared ledger carries other stages' payloads (execution
            # decisions etc.) with shapes we don't own — treat every entry
            # defensively so a foreign record can never crash construction.
            payload = getattr(entry, "payload", None)
            if not isinstance(payload, dict):
                continue
            session_date = payload.get("session_date")
            if session_date is not None and session_date < today:
                break
            dedup_id = payload.get("dedup_id")
            if payload.get("stage") == "anomaly_decision" and dedup_id:
                seen.add(dedup_id)
        return seen

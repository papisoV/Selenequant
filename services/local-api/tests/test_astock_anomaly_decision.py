"""S4 coverage: AnomalyEvent → decision consumer (observe + suggest tiers).

The detector (S3a) publishes ``AnomalyEvent`` onto the shared bus, but nothing
downstream records a *decision* about it. This module pins the scope:

* ``build_intent`` is a pure kind→TradeIntent mapper (share-lot sizing with
  ``/price``; record-only kinds and sub-lot capital return ``None``);
* ``SELENE_ANOMALY_MODE`` accepts ``observe`` (default) and ``suggest``;
  ``auto`` — and any other value — is a startup ``ValueError``: no
  legal-but-inert enum, because ``auto`` would be an ungated order path;
* ``AnomalyDecisionConsumer`` appends one ``stage="anomaly_decision"`` record
  per event into the production hash-chain ledger, deduped per
  (session_date, symbol, kind) with CST session dates, replayed from the
  ledger on construction (cross-restart), and never raises out of ``handle``;
* in ``suggest`` mode a fresh anomaly whose kind maps to a side is *also*
  parked as a pending proposal; a verdict appends ``stage="anomaly_approval"``
  with ``executes_order=False`` and never reaches an execution path.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from selene_space_core.events.bus import EventBus
from selene_space_paper.audit_ledger import HashChainLedger
from selene_space_types.market import AnomalyEvent
from selene_space_types.trading import OrderSide

from app import astock_anomaly_decision as decision_module
from app.astock_anomaly_decision import (
    ANOMALY_MODE_ENV,
    AnomalyDecisionConsumer,
    build_intent,
    resolve_anomaly_mode,
)

# China has no DST — same fixed offset the detector and wiring use.
_CST = timezone(timedelta(hours=8))


def _cst_insession_ts(hour: int = 10, minute: int = 0) -> int:
    """A realistic in-session timestamp (ms) for *today* in Beijing time.

    Derived from ``now`` rather than a constant so the consumer's
    "current session" (computed from the wall clock at construction/replay)
    always matches the events under test, including across midnight runs.
    """
    now_cst = datetime.now(_CST)
    stamped = now_cst.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return int(stamped.timestamp() * 1000)


def _anomaly(kind: str = "limit_up", **overrides: Any) -> AnomalyEvent:
    base: dict[str, Any] = {
        "exchange": "astock",
        "symbol": "600519.SH",
        "timestamp": _cst_insession_ts(),
        "kind": kind,
        "price": 11.0,
        "prev_close": 10.0,
        "name": "贵州茅台",
    }
    base.update(overrides)
    return AnomalyEvent(**base)


class _RaisingAppendLedger(HashChainLedger):
    """A real ledger whose write path always fails (read path intact)."""

    def append(self, payload: dict[str, Any]) -> Any:
        raise RuntimeError("disk full")


class _FailNthAppendLedger(HashChainLedger):
    """A real ledger that fails only on the *n*-th append.

    The decision record (1st append) must land so a proposal is parked at all;
    the approval record (2nd append) is the one under test.
    """

    def __init__(self, path: Path, *, fail_on: int) -> None:
        super().__init__(path)
        self._fail_on = fail_on
        self._calls = 0

    def append(self, payload: dict[str, Any]) -> Any:
        self._calls += 1
        if self._calls == self._fail_on:
            raise RuntimeError("disk full")
        return super().append(payload)


# ---------------------------------------------------------------------------
# build_intent — the pure mapper
# ---------------------------------------------------------------------------


class TestBuildIntentKindMap:
    @pytest.mark.parametrize(
        ("kind", "expected_side"),
        [
            ("limit_up", OrderSide.BUY),
            ("limit_down_recovery", OrderSide.BUY),
            ("n_day_high", OrderSide.BUY),
            ("broken_limit_up", None),
            ("limit_down", None),
            ("n_day_low", None),
            ("volume_surge", None),
        ],
    )
    def test_kind_maps_to_side_or_none(self, kind: str, expected_side: str | None):
        intent = build_intent(_anomaly(kind), capital_base=100_000, quantity_pct=0.1)
        if expected_side is None:
            assert intent is None
        else:
            assert intent is not None
            assert intent.side == expected_side

    def test_returns_new_object_and_does_not_mutate_event(self):
        event = _anomaly("limit_up")
        before = event.model_dump()
        build_intent(event, capital_base=100_000, quantity_pct=0.1)
        assert event.model_dump() == before


class TestBuildIntentSizing:
    @pytest.mark.parametrize("price", [10.0, 27.35])
    def test_notional_matches_capital_budget_within_one_lot(self, price: float):
        capital_base, quantity_pct = 100_000, 0.1
        intent = build_intent(
            _anomaly("limit_up", price=price),
            capital_base=capital_base,
            quantity_pct=quantity_pct,
        )
        assert intent is not None
        # Shares, not lots: quantity * price lands within one 100-share lot
        # of the budget. Missing the /price in the formula overshoots by the
        # price factor and fails this immediately.
        assert intent.quantity * price == pytest.approx(
            capital_base * quantity_pct, abs=100 * price
        )

    def test_quantity_is_positive_lot_multiple(self):
        intent = build_intent(
            _anomaly("limit_up", price=27.35),
            capital_base=100_000,
            quantity_pct=0.1,
        )
        assert intent is not None
        assert intent.quantity > 0
        assert intent.quantity % 100 == 0

    def test_synthesizes_required_intent_fields(self):
        event = _anomaly("limit_up", price=11.0)
        intent = build_intent(event, capital_base=100_000, quantity_pct=0.1)
        assert intent is not None
        assert intent.symbol == event.symbol
        assert intent.order_type == "limit"
        assert intent.price == event.price
        assert intent.timestamp == event.timestamp
        assert intent.reasoning  # deterministic non-empty string
        assert (
            intent.reasoning
            == build_intent(event, capital_base=100_000, quantity_pct=0.1).reasoning
        )
        assert 0.0 < intent.confidence <= 1.0

    def test_price_none_returns_none(self):
        event = _anomaly("limit_up").model_copy(update={"price": None})
        assert build_intent(event, capital_base=100_000, quantity_pct=0.1) is None

    @pytest.mark.parametrize("price", [0.0, -3.5])
    def test_price_not_positive_returns_none(self, price: float):
        assert (
            build_intent(
                _anomaly("limit_up", price=price),
                capital_base=100_000,
                quantity_pct=0.1,
            )
            is None
        )

    def test_sub_lot_capital_returns_none(self):
        # 5000 * 0.1 / 100 per share = 5 shares → floors to 0 lots.
        assert (
            build_intent(
                _anomaly("limit_up", price=100.0),
                capital_base=5_000,
                quantity_pct=0.1,
            )
            is None
        )


# ---------------------------------------------------------------------------
# Mode parsing — observe is the only legal value
# ---------------------------------------------------------------------------


class TestModeParsing:
    def test_env_name(self):
        assert ANOMALY_MODE_ENV == "SELENE_ANOMALY_MODE"

    def test_unset_defaults_to_observe(self):
        assert resolve_anomaly_mode(None) == "observe"

    def test_observe_accepted(self):
        assert resolve_anomaly_mode("observe") == "observe"

    def test_suggest_accepted(self):
        # Suggest is now a legal mode (approval = record a human confirmation).
        assert resolve_anomaly_mode("suggest") == "suggest"

    @pytest.mark.parametrize("raw", ["auto", "banana"])
    def test_anything_else_raises(self, raw: str):
        # ``auto`` stays illegal on purpose: it would be an ungated A-share
        # order path until the plan §5 preconditions land.
        with pytest.raises(ValueError):
            resolve_anomaly_mode(raw)

    def test_consumer_rejects_auto_mode_at_construction(self):
        with pytest.raises(ValueError):
            AnomalyDecisionConsumer(
                ledger=HashChainLedger(Path("unused.jsonl")),
                capital_base=100_000,
                quantity_pct=0.1,
                mode="auto",
            )


# ---------------------------------------------------------------------------
# Consumer — dedup, replay, failure containment, ledger integrity
# ---------------------------------------------------------------------------


class TestAnomalyDecisionConsumer:
    def _consumer(
        self, ledger: HashChainLedger, **kwargs: Any
    ) -> AnomalyDecisionConsumer:
        return AnomalyDecisionConsumer(
            ledger=ledger,
            capital_base=100_000,
            quantity_pct=0.1,
            **kwargs,
        )

    def _records(self, ledger: HashChainLedger) -> list[dict[str, Any]]:
        return [
            entry.payload
            for entry in ledger.read_chain()
            if entry.payload.get("stage") == "anomaly_decision"
        ]

    def test_duplicate_suppressed_within_session(self, tmp_path: Path):
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger)
        event = _anomaly("limit_up")

        consumer.handle(event)
        consumer.handle(_anomaly("limit_up", timestamp=event.timestamp))

        results = [r["result"] for r in self._records(ledger)]
        assert results == ["observed", "duplicate_suppressed"]

    def test_different_kind_same_symbol_not_suppressed(self, tmp_path: Path):
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger)
        consumer.handle(_anomaly("limit_up"))
        consumer.handle(_anomaly("n_day_high"))
        assert [r["result"] for r in self._records(ledger)] == [
            "observed",
            "observed",
        ]

    def test_replay_tolerates_foreign_ledger_entries(self, tmp_path: Path):
        # The shared ledger carries other stages' payloads (execution
        # decisions etc.): odd shapes must be skipped, not crash construction,
        # and must not leak into the dedup set.
        path = tmp_path / "chain.jsonl"
        ledger = HashChainLedger(path)
        ledger.append({"stage": "live_decision", "reason": "crypto order"})  # foreign
        today = datetime.now(_CST).date().isoformat()
        # A same-day foreign entry that happens to carry dedup_id-shaped
        # fields but not our stage — must NOT seed the dedup set.
        ledger.append({"dedup_id": f"{today}|600519.SH|limit_up"})
        consumer = self._consumer(ledger)
        consumer.handle(_anomaly("limit_up"))
        results = [r["result"] for r in self._records(ledger)]
        assert results == ["observed"]

    def test_cross_restart_replay_suppresses(self, tmp_path: Path):
        path = tmp_path / "chain.jsonl"
        ledger = HashChainLedger(path)
        first = self._consumer(ledger)
        event = _anomaly("limit_up")
        first.handle(event)

        # A fresh process re-opens the same file: replay must seed the dedup
        # set from the persisted current-session entries.
        second = self._consumer(HashChainLedger(path))
        second.handle(_anomaly("limit_up", timestamp=event.timestamp))

        results = [r["result"] for r in self._records(HashChainLedger(path))]
        assert results == ["observed", "duplicate_suppressed"]

    def test_observe_mode_records_no_intent(self, tmp_path: Path):
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger)
        consumer.handle(_anomaly("limit_up"))
        record = self._records(ledger)[0]
        assert record["stage"] == "anomaly_decision"
        assert "intent" not in record
        assert "TradeIntent" not in str(record)

    def test_record_payload_shape(self, tmp_path: Path):
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger)
        event = _anomaly("limit_up")
        consumer.handle(event)
        record = self._records(ledger)[0]
        expected_date = (
            datetime.fromtimestamp(event.timestamp / 1000, tz=_CST).date().isoformat()
        )
        assert record["kind"] == "limit_up"
        assert record["symbol"] == "600519.SH"
        assert record["session_date"] == expected_date
        assert record["dedup_id"] == f"{expected_date}|600519.SH|limit_up"

    def test_handle_never_raises_when_append_fails(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        ledger = _RaisingAppendLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger)
        event = _anomaly("limit_up")
        with caplog.at_level(logging.ERROR, logger=decision_module.__name__):
            consumer.handle(event)  # must not raise
        assert any(
            rec.levelno == logging.ERROR and "600519.SH" in rec.getMessage()
            for rec in caplog.records
        )

    def test_ledger_chain_verifies_and_dedup_id_survives_redaction(
        self, tmp_path: Path
    ):
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger)
        consumer.handle(_anomaly("limit_up"))
        consumer.handle(_anomaly("n_day_high"))

        assert ledger.verify_chain() == (True, 0)
        records = self._records(ledger)
        assert len(records) == 2
        # _redact destroys any key containing "key/secret/token/password";
        # dedup_id is deliberately named to survive it.
        assert all(r["dedup_id"] and r["dedup_id"] != "[redacted]" for r in records)


# ---------------------------------------------------------------------------
# Wiring — bus subscription end-to-end
# ---------------------------------------------------------------------------


class TestWiring:
    def test_wired_consumer_appends_one_record_per_published_event(
        self, tmp_path: Path
    ):
        from app.astock_anomaly_wiring import wire_astock_anomaly_consumer

        class _FakeMarketSvc:
            def __init__(self) -> None:
                self.event_bus = EventBus()

        market_svc = _FakeMarketSvc()
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = wire_astock_anomaly_consumer(
            market_svc, ledger, capital_base=100_000, quantity_pct=0.1
        )
        assert market_svc.event_bus.handler_count(AnomalyEvent) == 1

        market_svc.event_bus.publish(_anomaly("limit_up"))
        market_svc.event_bus.publish(_anomaly("limit_up"))

        records = [
            entry.payload
            for entry in ledger.read_chain()
            if entry.payload.get("stage") == "anomaly_decision"
        ]
        assert [r["result"] for r in records] == [
            "observed",
            "duplicate_suppressed",
        ]
        assert consumer is not None

    def test_wiring_rejects_illegal_mode(self, tmp_path: Path):
        """The wiring must forward ``mode`` to the consumer, not drop it.

        A wiring that silently ignored ``mode`` would construct an observe
        consumer and never raise — the exact production gap this pins. ``auto``
        (still illegal) is the probe: were ``mode`` dropped, no ValueError.
        """
        from app.astock_anomaly_wiring import wire_astock_anomaly_consumer

        class _FakeMarketSvc:
            def __init__(self) -> None:
                self.event_bus = EventBus()

        market_svc = _FakeMarketSvc()
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        with pytest.raises(ValueError, match=ANOMALY_MODE_ENV):
            wire_astock_anomaly_consumer(
                market_svc,
                ledger,
                capital_base=100_000,
                quantity_pct=0.1,
                mode="auto",
            )
        # Nothing should have been subscribed when construction aborts.
        assert market_svc.event_bus.handler_count(AnomalyEvent) == 0

    def test_wiring_forwards_suggest_mode(self, tmp_path: Path):
        """A suggest wiring yields a suggest consumer — mode is not dropped."""
        from app.astock_anomaly_wiring import wire_astock_anomaly_consumer

        class _FakeMarketSvc:
            def __init__(self) -> None:
                self.event_bus = EventBus()

        market_svc = _FakeMarketSvc()
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = wire_astock_anomaly_consumer(
            market_svc,
            ledger,
            capital_base=100_000,
            quantity_pct=0.1,
            mode="suggest",
        )
        assert consumer.mode == "suggest"

    def test_wiring_observe_mode_matches_unset(self, tmp_path: Path):
        from app.astock_anomaly_wiring import wire_astock_anomaly_consumer

        class _FakeMarketSvc:
            def __init__(self) -> None:
                self.event_bus = EventBus()

        market_svc = _FakeMarketSvc()
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = wire_astock_anomaly_consumer(
            market_svc,
            ledger,
            capital_base=100_000,
            quantity_pct=0.1,
            mode=resolve_anomaly_mode("observe"),
        )
        assert consumer is not None
        assert market_svc.event_bus.handler_count(AnomalyEvent) == 1


# ---------------------------------------------------------------------------
# Suggest tier — pending proposals, verdicts, and the no-order guarantee
# ---------------------------------------------------------------------------


class TestSuggestTier:
    def _consumer(self, ledger: HashChainLedger, mode: str = "suggest") -> Any:
        return AnomalyDecisionConsumer(
            ledger=ledger,
            capital_base=100_000,
            quantity_pct=0.1,
            mode=mode,
        )

    def _records(self, ledger: HashChainLedger, stage: str) -> list[dict[str, Any]]:
        return [
            entry.payload
            for entry in ledger.read_chain()
            if entry.payload.get("stage") == stage
        ]

    def test_observe_mode_parks_nothing(self, tmp_path: Path):
        # The core regression guard: observe must stay inert. If parking ever
        # leaks into observe, a deployment nobody armed starts queuing trades
        # for approval — the exact green-light-on-a-false-premise failure.
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger, mode="observe")
        consumer.handle(_anomaly("limit_up"))
        assert consumer.list_pending() == []

    def test_suggest_parks_mapped_kind(self, tmp_path: Path):
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger)
        event = _anomaly("limit_up")
        consumer.handle(event)

        pending = consumer.list_pending()
        assert len(pending) == 1
        p = pending[0]
        assert p.kind == "limit_up"
        assert p.symbol == "600519.SH"
        assert p.side == OrderSide.BUY.value
        # floor((100_000 * 0.1) / 11.0 / 100) * 100 = 900 shares
        assert p.quantity == 900.0
        assert p.price == 11.0

    def test_suggest_parks_nothing_for_record_only_kind(self, tmp_path: Path):
        # volume_surge maps to None in KIND_DECISION_MAP → no intent → no
        # proposal. It must still be *observed* (the ledger record stays).
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger)
        consumer.handle(_anomaly("volume_surge"))
        assert consumer.list_pending() == []
        assert [r["result"] for r in self._records(ledger, "anomaly_decision")] == [
            "observed"
        ]

    def test_duplicate_does_not_resurrect_proposal(self, tmp_path: Path):
        # Decide on a proposal, then a duplicate anomaly of the same
        # (session, symbol, kind) arrives. It must be suppressed and must NOT
        # re-park a proposal the user already resolved.
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger)
        event = _anomaly("limit_up")
        consumer.handle(event)
        dedup_id = consumer.list_pending()[0].dedup_id
        assert consumer.decide(dedup_id, approve=True) == "approved"

        consumer.handle(_anomaly("limit_up", timestamp=event.timestamp))
        assert consumer.list_pending() == []
        assert [r["result"] for r in self._records(ledger, "anomaly_decision")] == [
            "observed",
            "duplicate_suppressed",
        ]

    def test_approve_records_confirmation_without_order(self, tmp_path: Path):
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger)
        consumer.handle(_anomaly("limit_up"))
        dedup_id = consumer.list_pending()[0].dedup_id

        assert consumer.decide(dedup_id, approve=True) == "approved"
        approvals = self._records(ledger, "anomaly_approval")
        assert len(approvals) == 1
        rec = approvals[0]
        assert rec["result"] == "approved"
        assert rec["dedup_id"] == dedup_id
        assert rec["symbol"] == "600519.SH"
        # The honesty marker: approving is a human read, not an order.
        assert rec["executes_order"] is False
        # And no proposal remains pending.
        assert consumer.list_pending() == []
        assert ledger.verify_chain() == (True, 0)

    def test_reject_records_rejection(self, tmp_path: Path):
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger)
        consumer.handle(_anomaly("limit_up"))
        dedup_id = consumer.list_pending()[0].dedup_id

        assert consumer.decide(dedup_id, approve=False) == "rejected"
        rec = self._records(ledger, "anomaly_approval")[0]
        assert rec["result"] == "rejected"
        assert rec["executes_order"] is False

    def test_decide_unknown_id_is_no_pending(self, tmp_path: Path):
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger)
        assert consumer.decide("2099-01-01|X|limit_up", approve=True) == (
            "no_pending_proposal"
        )
        # A miss must not write anything.
        assert self._records(ledger, "anomaly_approval") == []

    def test_decide_replay_is_not_a_second_approval(self, tmp_path: Path):
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger)
        consumer.handle(_anomaly("limit_up"))
        dedup_id = consumer.list_pending()[0].dedup_id

        assert consumer.decide(dedup_id, approve=True) == "approved"
        assert consumer.decide(dedup_id, approve=True) == "no_pending_proposal"
        # Exactly one approval record — the replay wrote nothing.
        assert len(self._records(ledger, "anomaly_approval")) == 1

    def test_failed_approval_write_keeps_proposal_retryable(self, tmp_path: Path):
        # The decision record lands (append #1); the approval write (append #2)
        # fails. Popping the proposal before the append would destroy the only
        # handle on it — and since handling the anomaly already put its
        # dedup_id in ``_seen``, a re-published duplicate is suppressed, so the
        # confirmation would be unretryable for the rest of the session. The
        # proposal must survive the failure so the human can retry it.
        ledger = _FailNthAppendLedger(tmp_path / "chain.jsonl", fail_on=2)
        consumer = self._consumer(ledger)
        consumer.handle(_anomaly("limit_up"))
        dedup_id = consumer.list_pending()[0].dedup_id

        with pytest.raises(RuntimeError):
            consumer.decide(dedup_id, approve=True)

        # Still pending → retry is possible.
        assert [p.dedup_id for p in consumer.list_pending()] == [dedup_id]
        # And nothing was half-written.
        assert self._records(ledger, "anomaly_approval") == []

    def test_retry_after_failed_approval_write_succeeds(self, tmp_path: Path):
        # The same id, once the ledger recovers, records exactly one approval.
        path = tmp_path / "chain.jsonl"
        failing = _FailNthAppendLedger(path, fail_on=2)
        consumer = self._consumer(failing)
        consumer.handle(_anomaly("limit_up"))
        dedup_id = consumer.list_pending()[0].dedup_id
        with pytest.raises(RuntimeError):
            consumer.decide(dedup_id, approve=True)

        # Heal the ledger (a real one would recover when the disk does) and
        # confirm the same proposal is still resolvable.
        failing._fail_on = -1
        assert consumer.decide(dedup_id, approve=True) == "approved"
        approvals = self._records(HashChainLedger(path), "anomaly_approval")
        assert len(approvals) == 1
        assert approvals[0]["executes_order"] is False

    def test_decide_unknown_id_does_not_touch_a_parked_proposal(self, tmp_path: Path):
        # A miss must not evict anything (guards the ``.get`` → ``pop`` split).
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger)
        consumer.handle(_anomaly("limit_up"))
        parked = consumer.list_pending()[0].dedup_id

        assert consumer.decide("2099-01-01|X|limit_up", approve=True) == (
            "no_pending_proposal"
        )
        assert [p.dedup_id for p in consumer.list_pending()] == [parked]

    def test_pending_registry_is_bounded_and_evicts_oldest(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch_cap = 3
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger)
        # distinct symbols → distinct dedup ids, all buy-mapped kinds
        names = ["600519.SH", "000001.SZ", "300750.SZ", "601318.SH", "000858.SZ"]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(decision_module, "_PROPOSAL_CAP", monkeypatch_cap)
            with caplog.at_level(logging.WARNING, logger=decision_module.__name__):
                for sym in names:
                    consumer.handle(_anomaly("limit_up", symbol=sym))
        pending = consumer.list_pending()
        assert len(pending) == monkeypatch_cap
        # Oldest evicted first → the first two symbols are gone.
        kept = {p.symbol for p in pending}
        assert "600519.SH" not in kept
        assert "000001.SZ" not in kept
        assert "000858.SZ" in kept
        assert any("evicted oldest" in r.getMessage() for r in caplog.records)

    def test_pending_payload_is_json_safe(self, tmp_path: Path):
        ledger = HashChainLedger(tmp_path / "chain.jsonl")
        consumer = self._consumer(ledger)
        consumer.handle(_anomaly("limit_up"))
        payload = consumer.list_pending()[0].to_payload()
        # Must round-trip through json — enum leaks would raise here.
        assert json.loads(json.dumps(payload)) == payload
        assert payload["side"] == "buy"  # the wire value, not OrderSide.BUY

"""Describe an exported QuantDinger strategy without running a backtest.

Symbol, frequency, and warmup are *declared by the source itself* inside
``initialize(context)`` -- ``g.symbol = "Crypto:XLM/USDT@okx:swap"``,
``context.subscribe(frequency="1h", ...)``, ``context.set_warmup(320)``. The
declaration hook is side-effect-free by contract, so running it against a
throwaway :class:`QDManifest` recovers those values exactly. Filenames and the
backup ``_index_sources_*.json`` are not a substitute: the index is
heterogeneous (a few entries carry a ``strategy_manifest``, most carry an empty
``metadata``) and file names embed no venue at all.

A text fallback covers sources whose ``initialize`` needs per-bar API
(``get_history`` and friends are bound only while ``handle_data`` runs).
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass

from selene_space_strategy.qd.context import (
    QDContext,
    QDManifest,
    QDParams,
    normalize_instrument,
)
from selene_space_strategy.qd.contract import (
    QDCompiledStrategy,
    compile_qd_strategy,
    parse_params,
)
from selene_space_strategy.qd.sandbox import SecurityError

logger = logging.getLogger(__name__)

_FUTURES_TOKENS = frozenset({"swap", "futures", "future", "perp", "perpetual"})
_CRYPTO_PREFIX = "Crypto:"

_SYMBOL_RE = re.compile(r"""^\s*g\.symbol\s*=\s*["']([^"']+)["']""", re.MULTILINE)
_FREQUENCY_RE = re.compile(r"""frequency\s*=\s*["']([^"']+)["']""")
_WARMUP_RE = re.compile(r"set_warmup\(\s*(\d+)\s*\)")


@dataclass(frozen=True)
class QDSourceInfo:
    """What one exported strategy source binds itself to."""

    source_name: str
    name: str
    symbol: str
    instrument: str
    exchange: str | None
    market_type: str | None
    interval: str
    warmup: int
    direction_mode: str
    params: dict[str, float | int]
    instruments: tuple[str, ...]
    runnable: bool
    reason: str

    def as_payload(self) -> dict[str, object]:
        """JSON-ready projection for the listing endpoint."""
        return {
            "symbol": self.symbol,
            "instrument": self.instrument,
            "exchange": self.exchange,
            "market_type": self.market_type,
            "interval": self.interval,
            "warmup": self.warmup,
            "direction_mode": self.direction_mode,
            "params": dict(self.params),
            "instruments": list(self.instruments),
            "runnable": self.runnable,
            "reason": self.reason,
        }


def describe_qd_source(
    source: str, *, source_name: str = "<qd-strategy>", name: str = ""
) -> QDSourceInfo:
    """Parse the instrument binding a QuantDinger source declares."""
    compiled, compile_error = _compile_or_none(source, source_name)
    manifest = _run_initialize(compiled) if compiled else None

    instruments = list(manifest.universe) if manifest else []
    if not instruments:
        instruments = _universe_from_text(source)
    if not instruments:
        instruments = _legs_from_symbols_dict(source)
    interval = (
        _driving_frequency(manifest.frequencies)
        if manifest and manifest.frequencies
        else ""
    ) or _frequency_from_text(source)
    warmup = (manifest.warmup if manifest else 0) or _warmup_from_text(source)

    instrument = instruments[0] if instruments else ""
    symbol = normalize_instrument(instrument)
    exchange, market_type = parse_venue(instrument)
    runnable, reason = _run_gate(instruments, instrument, symbol, compile_error)

    return QDSourceInfo(
        source_name=source_name,
        name=name or source_name,
        symbol=symbol,
        instrument=instrument,
        exchange=exchange,
        market_type=market_type,
        interval=interval,
        warmup=warmup,
        direction_mode=manifest.direction_mode if manifest else "both",
        params=dict(compiled.param_defaults if compiled else parse_params(source)),
        instruments=tuple(instruments),
        runnable=runnable,
        reason=reason,
    )


def _compile_or_none(
    source: str, source_name: str
) -> tuple[QDCompiledStrategy | None, str]:
    """Compile, or report why this source cannot be driven at all.

    The backup library holds strategy kinds this adapter does not model --
    portfolio sources declare no ``handle_data``, and a few exports trip the
    sandbox gate. Describing them must not raise: the listing surface renders
    them as unavailable, which is information worth showing.
    """
    try:
        return compile_qd_strategy(source, source_name=source_name), ""
    except (ValueError, SecurityError) as exc:
        logger.debug("qd source %s: compile failed", source_name, exc_info=True)
        return None, str(exc)


def parse_venue(instrument: str) -> tuple[str | None, str | None]:
    """Split a QuantDinger instrument into ``(exchange, market_type)``.

    ``Crypto:XLM/USDT@okx:swap`` -> ``("okx", "futures")``; ``...@swap`` (no
    exchange) -> ``(None, "futures")``; ``...@spot`` -> ``(None, "spot")``;
    a bare equity ticker such as ``TSLA`` -> ``(None, None)``. Market type is
    collapsed onto selene's two values, so an unrecognized token reads spot.
    """
    if "@" not in instrument:
        return None, None
    venue = instrument.split("@", 1)[1]
    exchange: str | None = None
    token = venue
    if ":" in venue:
        head, token = venue.split(":", 1)
        exchange = head.strip().lower() or None
    token = token.strip().lower()
    if not token:
        return exchange, None
    return exchange, "futures" if token in _FUTURES_TOKENS else "spot"


def _run_initialize(compiled: QDCompiledStrategy) -> QDManifest | None:
    """Execute the declaration hook; ``None`` when the source cannot run here.

    Sources are opaque third-party scripts written against a wider API than
    this adapter models, so a failure here is expected for some exports and is
    handled by falling back to text parsing rather than aborting the caller.
    """
    manifest = QDManifest()
    context = QDContext(manifest, QDParams(compiled.param_defaults), data_symbol="")
    try:
        compiled.new_g()
        if compiled.initialize is not None:
            compiled.initialize(context)
    except Exception:  # noqa: BLE001 - opaque source; text fallback takes over
        logger.debug(
            "qd source %s: initialize() failed", compiled.source_name, exc_info=True
        )
        return None
    return manifest


def _universe_from_text(source: str) -> list[str]:
    return [match.group(1) for match in _SYMBOL_RE.finditer(source)]


def _legs_from_symbols_dict(source: str) -> list[str]:
    """Instrument legs declared as a ``g.symbols = {...}`` dict literal.

    Portfolio exports bind a leg-name -> instrument mapping rather than the
    single ``g.symbol`` scalar, and they define no ``handle_data``, so neither
    the manifest nor the scalar regex ever sees their legs. Reading the literal
    keeps their leg count -- and therefore their un-runnability -- visible.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        logger.debug("qd source: unparseable, no symbol dict to read", exc_info=True)
        return []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        if not any(
            isinstance(target, ast.Attribute) and target.attr == "symbols"
            for target in node.targets
        ):
            continue
        legs = [
            value.value
            for value in node.value.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        ]
        if legs:
            return legs
    return []


#: QD's event loop drives ``handle_data`` on the *fastest* subscribed
#: frequency, not the first-declared one (src7 declares 15m before 5m).
_FREQUENCY_ORDER = {
    "1m": 0,
    "3m": 1,
    "5m": 2,
    "15m": 3,
    "30m": 4,
    "1h": 5,
    "4h": 6,
    "1d": 7,
    "1w": 8,
}


def _driving_frequency(frequencies: list[str]) -> str:
    return min(frequencies, key=lambda f: _FREQUENCY_ORDER.get(f, 99))


def _frequency_from_text(source: str) -> str:
    match = _FREQUENCY_RE.search(source)
    return match.group(1) if match else ""


def _warmup_from_text(source: str) -> int:
    match = _WARMUP_RE.search(source)
    return int(match.group(1)) if match else 0


def _run_gate(
    instruments: list[str],
    instrument: str,
    symbol: str,
    compile_error: str = "",
) -> tuple[bool, str]:
    """Whether selene's single-symbol kline engine can drive this source."""
    if compile_error:
        return False, compile_error
    if len(instruments) > 1:
        return (
            False,
            f"multi-instrument ({len(instruments)} legs); engine is single-symbol",
        )
    if not instrument or not symbol:
        return False, "source declares no instrument"
    if "/" not in symbol:
        return False, f"non-crypto instrument {instrument!r} has no kline source"
    if not (instrument.startswith(_CRYPTO_PREFIX) or "@" in instrument):
        return False, f"unrecognized instrument form {instrument!r}"
    return True, ""

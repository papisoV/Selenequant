"""EventGuard — unified event-driven position multiplier.

Aggregates alerts from multiple sources (macro calendar, VIX regime,
TG realtime, unlock calendar) and produces a single position multiplier
per symbol.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .credit_source import CreditSource, resolve_credit_db_path
from .macro_source import MacroSource
from .models import EventAlert, EventSource
from .tg_source import TGSource
from .vix_source import VixSource
from selene_space_alpha.factors.tg_spike_source import DEFAULT_TG_DB_PATH
from selene_space_alpha.factors.tg_vol_regime_source import TGVolRegimeSource

logger = logging.getLogger(__name__)


class EventGuard:
    """Unified event-driven position multiplier."""

    def __init__(
        self,
        *,
        macro_db_path: str | Path = "data/macro_events.db",
        enable_vix: bool = True,
        enable_tg: bool = True,
        tg_regime_db_path: str | Path | None = None,
        enable_tg_regime: bool = False,
        enable_credit: bool = False,
        credit_db_path: str | Path | None = None,
    ) -> None:
        self._macro_source = MacroSource(macro_db_path)
        self._vix_source = VixSource(macro_db_path) if enable_vix else None
        self._tg_source = TGSource(macro_db_path) if enable_tg else None
        self._enable_vix = enable_vix
        self._enable_tg = enable_tg
        # TG volatility-regime feed (persistence de-risk, 48h x 0.85) is opt-in:
        # enable_tg_regime=False keeps pre-wiring behavior bit-identical.
        self._tg_regime_db_path = (
            DEFAULT_TG_DB_PATH if tg_regime_db_path is None else Path(tg_regime_db_path)
        )
        self._enable_tg_regime = enable_tg_regime
        self._regime_sources: dict[str, TGVolRegimeSource] = {}
        # WARNING: enable_credit 未经 V10 框架回测校准前不建议开启,开启属实验性质(ADR follow-up ②)
        self._enable_credit = enable_credit
        self._credit_source = (
            CreditSource(resolve_credit_db_path(credit_db_path))
            if enable_credit
            else None
        )
        # Future: self._tg_source = TGSource(...)
        # Future: self._unlock_source = UnlockSource(...)

    def get_position_multiplier(self, symbol: str, now_ts: int | None = None) -> float:
        """Get the current position multiplier for a symbol.

        Returns 0.0 (no new positions) to 1.0 (full position).
        Checks all sources and returns the most restrictive multiplier.
        """
        if now_ts is None:
            now_ts = int(time.time() * 1000)

        all_alerts: list[EventAlert] = []

        # Macro calendar alerts
        macro_alerts = self._macro_source.check(symbol, now_ts)
        all_alerts.extend(macro_alerts)

        # TG realtime alerts
        if self._tg_source is not None:
            all_alerts.extend(self._tg_source.check(symbol, now_ts))

        # VIX regime alerts
        if self._vix_source is not None:
            all_alerts.extend(self._vix_source.check(symbol, now_ts))

        # TG volatility-regime alerts (persistence de-risk layer, 48h x 0.85)
        regime_source = self._regime_source_for(symbol)
        if regime_source is not None:
            all_alerts.extend(regime_source.check(now_ts))

        # Credit regime alerts (HY OAS 5y percentile, opt-in)
        if self._credit_source is not None:
            all_alerts.extend(self._credit_source.check(symbol, now_ts))

        # Future: TG realtime alerts
        # all_alerts.extend(self._tg_source.check(symbol, now_ts))

        # Future: Unlock calendar alerts
        # all_alerts.extend(self._unlock_source.check(symbol, now_ts))

        if not all_alerts:
            return 1.0

        # Filter to only active alerts
        active = [a for a in all_alerts if a.is_active(now_ts)]
        if not active:
            return 1.0

        # Global alerts (symbol="*") apply to all symbols
        # Symbol-specific alerts apply only to that symbol
        relevant = [
            a
            for a in active
            if a.symbol == "*"
            or a.symbol.upper() == symbol.upper().replace("/USDT", "")
        ]

        if not relevant:
            return 1.0

        # Compute combined multiplier:
        # 1. Start with the most restrictive macro event multiplier
        # 2. Apply VIX event overlap reduction if VIX is high + event active
        # 3. TG alerts apply as an independent de-risk (most restrictive wins)
        macro_alerts_active = [
            a for a in relevant if a.event_type in ("FOMC", "CPI", "NFP")
        ]
        tg_alerts_active = [a for a in relevant if a.source == EventSource.TG_REALTIME]
        vix_alerts_active = [a for a in relevant if a.event_type == "VIX"]

        combined = 1.0

        if macro_alerts_active:
            # Take most restrictive macro multiplier
            worst_macro = min(macro_alerts_active, key=lambda a: a.multiplier)
            combined = worst_macro.multiplier

            # VIX event overlap: if high VIX + macro event, apply extra reduction
            # (ordering: macro → VIX overlap multiplies into combined BEFORE the
            # TG step below, so TG's min() can only tighten further, never loosen).
            if self._vix_source is not None and vix_alerts_active:
                vix_regime = self._vix_source.get_regime(now_ts)
                if vix_regime in ("high", "crisis"):
                    from .vix_source import _VIX_EVENT_OVERLAP_MULT

                    overlap_key = f"{vix_regime}_event"
                    overlap_mult = _VIX_EVENT_OVERLAP_MULT.get(overlap_key, 1.0)
                    combined *= overlap_mult
                    logger.debug(
                        "EventGuard VIX+event overlap: %s + %s → mult=%.2f * %.2f = %.2f",
                        vix_regime,
                        worst_macro.reason,
                        worst_macro.multiplier,
                        overlap_mult,
                        combined,
                    )

        # TG alerts: independent de-risk reduction, NOT compounded with macro.
        # combined = min(combined, tg_mult) — the single most-restrictive reduction
        # among independent event sources wins, eliminating the calendar×TG
        # double-cut (FOMC 0.85 × TG 0.7 = 0.595 → 0.7). min() (NOT the plan's
        # max()): 2026-08-13 user-confirmed deviation — max() would ignore a
        # catastrophic TG alert (RED=0.0) whenever any mild macro window is
        # active, allowing full-size trading during a hack.
        if tg_alerts_active:
            worst_tg = min(tg_alerts_active, key=lambda a: a.multiplier)
            combined = min(combined, worst_tg.multiplier)
            logger.debug(
                "EventGuard TG: %s → mult=%.2f, combined=%.2f",
                worst_tg.reason,
                worst_tg.multiplier,
                combined,
            )

        # VIX-only alerts (no macro event)
        if not macro_alerts_active and not tg_alerts_active and vix_alerts_active:
            worst_vix = min(vix_alerts_active, key=lambda a: a.multiplier)
            combined = worst_vix.multiplier

        # TG vol-regime alerts: independent persistence de-risk (48h x 0.85).
        # Deliberately NOT in get_tg_multiplier (TG-shock path) — regime covers
        # macro-ETH/SOL persistence which tg-spike-v1 already overlays at signal
        # time; adding it to the TG-only path would double-cut. min() keeps the
        # catastrophic-alert guarantee: a regime alert can only tighten, never
        # loosen.
        regime_alerts_active = [
            a for a in relevant if a.source == EventSource.TG_REGIME
        ]
        if regime_alerts_active:
            worst_regime = min(regime_alerts_active, key=lambda a: a.multiplier)
            combined = min(combined, worst_regime.multiplier)

        # Credit regime alerts: independent de-risk, min() semantics — can only
        # tighten, never loosen (same guarantee as the TG regime step above).
        credit_alerts_active = [a for a in relevant if a.source == EventSource.CREDIT]
        if credit_alerts_active:
            worst_credit = min(credit_alerts_active, key=lambda a: a.multiplier)
            combined = min(combined, worst_credit.multiplier)

        return combined

    def _regime_source_for(self, symbol: str) -> TGVolRegimeSource | None:
        """Cached per-symbol regime source; None when the feed is disabled."""
        if not self._enable_tg_regime:
            return None
        source = self._regime_sources.get(symbol)
        if source is None:
            source = TGVolRegimeSource(
                tg_db_path=self._tg_regime_db_path, symbol=symbol
            )
            self._regime_sources[symbol] = source
        return source

    def get_tg_multiplier(self, symbol: str, now_ts: int | None = None) -> float:
        """Get position multiplier from TG realtime alerts only (no macro, no VIX).

        Used by strategies that already apply their own macro overlay at
        signal-generation time (e.g. tg-spike-v1), to avoid double-cutting
        the macro event multiplier. Still picks up real-time TG risk
        events (SECURITY/REGULATORY/GEOPOLITICAL/WHALE) for ±2h protection.

        Returns 0.0 (no new positions) to 1.0 (full position).
        """
        if now_ts is None:
            now_ts = int(time.time() * 1000)
        if self._tg_source is None:
            return 1.0

        tg_alerts = [
            a for a in self._tg_source.check(symbol, now_ts) if a.is_active(now_ts)
        ]
        relevant = [
            a
            for a in tg_alerts
            if a.symbol == "*"
            or a.symbol.upper() == symbol.upper().replace("/USDT", "")
        ]
        if not relevant:
            return 1.0

        worst = min(relevant, key=lambda a: a.multiplier)
        logger.debug(
            "EventGuard TG-only: %s → mult=%.2f",
            worst.reason,
            worst.multiplier,
        )
        return worst.multiplier

    def get_all_multipliers(
        self, symbols: list[str], now_ts: int | None = None
    ) -> dict[str, float]:
        """Batch query multipliers for multiple symbols."""
        return {s: self.get_position_multiplier(s, now_ts) for s in symbols}

    def get_active_alerts(
        self, symbol: str | None = None, now_ts: int | None = None
    ) -> list[EventAlert]:
        """Get all active alerts, optionally filtered by symbol."""
        if now_ts is None:
            now_ts = int(time.time() * 1000)

        all_alerts: list[EventAlert] = []
        all_alerts.extend(self._macro_source.check(symbol or "*", now_ts))
        if self._tg_source is not None:
            all_alerts.extend(self._tg_source.check(symbol or "*", now_ts))
        if self._vix_source is not None:
            all_alerts.extend(self._vix_source.check(symbol or "*", now_ts))

        active = [a for a in all_alerts if a.is_active(now_ts)]
        if symbol:
            active = [
                a
                for a in active
                if a.symbol == "*"
                or a.symbol.upper() == symbol.upper().replace("/USDT", "")
            ]
        return active

    def get_vix_regime(self, now_ts: int | None = None) -> str:
        """Get current VIX regime label for display/logging."""
        if self._vix_source is None:
            return "unknown"
        if now_ts is None:
            now_ts = int(time.time() * 1000)
        return self._vix_source.get_regime(now_ts)

    def get_credit_status(self, now_ts: int | None = None) -> dict:
        """Credit source status {available, last_row_ts, degraded_reason} for display/observability."""
        if self._credit_source is None:
            return {
                "available": False,
                "last_row_ts": None,
                "degraded_reason": "credit source disabled",
            }
        if now_ts is None:
            now_ts = int(time.time() * 1000)
        return self._credit_source.get_status(now_ts)

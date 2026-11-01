"""TG event overlay source — de-risk only, no directional signals.

Event-sensor semantics (audit 2026-08-13, TG_FACTOR_REVALIDATION_2026-08-13.md):
direction prediction falsified (perm_p 0.12-0.17; BTC bullish→long harmful), but
macro/listing/whale events validated as volume events (bar volume percentile
59-75%, p≈0.002). This source converts TG factors into POSITION-SIZE REDUCTIONS
(de-risk) only — it never emits a buy/sell direction bet.

Emitted dicts carry `position_multiplier` (reduction) + event metadata and NO
side/direction key. Builds are mutually exclusive with TGSpikeSource at the
factory layer (`use_event_overlay_source`); a session must never enable both
(double de-risk).

Event types (canonical de-risk set, synced with
event_guard/tg_source._LLM_EVENT_TYPE_MAP in the US-005 single-source-of-truth
pass):
  - macro, security, market: always on
  - listing: ALT_only default OFF (three-symbol universe degenerates to binary);
    enable per-symbol via listing_whitelist
  - whale: per-symbol switch, default OFF

market joined the always-on set 2026-08-14 (forward-vol research,
tg_event_forward_vol_2026-08-14.py): market (price-breakout) is the only type
with robust forward persistence on BTC (24h 1.19x p=0.002 / 48h 1.13x p=0.004)
and was previously dropped as "no alpha in backtest". De-risk stays FLAT
(binary 0.5, no magnitude grading): Q8 grading is falsified (macro 24-48h
graded IC -0.24 to -0.37, all significant negative) — only the event marker
carries forward-vol information, not the LLM magnitude.

Only pure loader helpers (load_tg_signals / map_signal_to_symbols /
SYMBOL_SHORT_MAP) are reused from tg_spike_source — the directional tier/spike
logic is intentionally NOT imported.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from selene_space_alpha.factors.tg_spike_source import (
    DEFAULT_TG_DB_PATH,
    SYMBOL_SHORT_MAP,
    load_tg_signals,
    map_signal_to_symbols,
)

# Canonical set of event types that trigger de-risk (lowercase LLM event_type).
EVENT_OVERLAY_TYPES: frozenset[str] = frozenset({"macro", "listing", "whale", "security", "market"})

# Always-on regardless of whitelist.
DEFAULT_ON_TYPES: frozenset[str] = frozenset({"macro", "security", "market"})

# De-risk window mirrors the validated macro overlay (2026-07-18): ±24h.
OVERLAY_WINDOW_BEFORE_HOURS = 24.0
OVERLAY_WINDOW_AFTER_HOURS = 24.0

# Uniform de-risk reduction (halve exposure inside the window). Pre-registered;
# not tuned per-event (see the pre-registration record §1).
DE_RISK_POSITION_MULT = 0.5

# Confidence / magnitude gate mirrors the event gate in signal_engine.py.
CONFIDENCE_MIN = 0.3


class TGEventOverlaySource:
    """Event-sensor de-risk overlay (no directional signals).

    Reads tg_factors.db, finds bars inside the event window for enabled
    macro/listing/whale/market/security events that map to this symbol, and
    marks them with DE_RISK_POSITION_MULT (0.5) — a position-size reduction,
    not a direction bet. Emitted dicts have NO side/direction key.

    Args:
        tg_db_path: path to tg_factors.db
        symbol: "BTC/USDT" / "ETH/USDT" / "SOL/USDT"
        interval: kline interval (default "4h")
        strategy_id: strategy id (kept for interface parity with TGSpikeSource)
        listing_whitelist: symbol shorts (e.g. {"BTC"}) to enable listing
            events for; empty/None = listing off (ALT_only default)
        whale_whitelist: symbol shorts to enable whale events for;
            empty/None = whale off
    """

    def __init__(
        self,
        tg_db_path: str | Path = DEFAULT_TG_DB_PATH,
        symbol: str = "BTC/USDT",
        interval: str = "4h",
        strategy_id: str = "tg-event-overlay-v1",
        listing_whitelist: set[str] | None = None,
        whale_whitelist: set[str] | None = None,
    ) -> None:
        self.tg_db_path = Path(tg_db_path)
        self.symbol = symbol
        self.symbol_short = SYMBOL_SHORT_MAP.get(symbol, symbol.split("/")[0])
        self.interval = interval
        self.strategy_id = strategy_id
        self.listing_whitelist = set(listing_whitelist or [])
        self.whale_whitelist = set(whale_whitelist or [])
        self._signals_cache: pd.DataFrame | None = None

    def _enabled_types(self) -> set[str]:
        types = set(DEFAULT_ON_TYPES)
        if self.symbol_short in self.listing_whitelist:
            types.add("listing")
        if self.symbol_short in self.whale_whitelist:
            types.add("whale")
        return types & set(EVENT_OVERLAY_TYPES)

    def _load_signals(self) -> pd.DataFrame:
        if self._signals_cache is not None:
            return self._signals_cache
        df = load_tg_signals(self.tg_db_path, filter_noise=True)
        df["mapped_symbols"] = df["symbols_list"].apply(map_signal_to_symbols)
        df["confidence_f"] = pd.to_numeric(df.get("confidence"), errors="coerce").fillna(0.0)
        df["magnitude_f"] = pd.to_numeric(df.get("magnitude"), errors="coerce").fillna(0.0)
        self._signals_cache = df
        return df

    def _gated_signals(self) -> pd.DataFrame:
        """Events mapped to this symbol, enabled type, passing the event gate.

        Single gated frame shared by _event_bars and build_signal_dicts so the
        dominant-event pick can never bypass the type/confidence/magnitude gate.
        """
        enabled = self._enabled_types()
        signals = self._load_signals()
        if not enabled or signals.empty:
            return signals.iloc[0:0]
        sigs = signals[
            signals["mapped_symbols"].apply(lambda lst: self.symbol_short in lst)
        ].copy()
        if sigs.empty:
            return sigs
        sigs = sigs[sigs["event_type"].isin(enabled)]
        sigs = sigs[(sigs["confidence_f"] >= CONFIDENCE_MIN) & (sigs["magnitude_f"] > 0)]
        return sigs

    def _event_bars(self, klines: pd.DataFrame) -> pd.DataFrame:
        """Return klines subset whose bars sit inside an enabled event window.

        A bar counts as overlaid when bar_open ∈ [msg_ts - before, msg_ts + after]
        for an event passing the event gate (_gated_signals).
        """
        sigs = self._gated_signals()
        if sigs.empty:
            return klines.iloc[0:0]

        before_ns = int(OVERLAY_WINDOW_BEFORE_HOURS * 3_600_000_000_000)
        after_ns = int(OVERLAY_WINDOW_AFTER_HOURS * 3_600_000_000_000)
        bar_ns = klines.index.astype("int64").values
        ev_ns = sigs["ts"].astype("int64").values
        in_window = np.zeros(len(klines), dtype=bool)
        for e in ev_ns:
            in_window |= (bar_ns >= e - before_ns) & (bar_ns <= e + after_ns)
        return klines[in_window]

    def enrich_klines(self, klines: pd.DataFrame) -> pd.DataFrame:
        """Attach event_overlay_mult column: 1.0 normally, DE_RISK_POSITION_MULT in window."""
        klines = klines.copy()
        klines["event_overlay_mult"] = 1.0
        overlaid = self._event_bars(klines)
        if not overlaid.empty:
            klines.loc[overlaid.index, "event_overlay_mult"] = DE_RISK_POSITION_MULT
        return klines

    def build_signal_dicts(self, klines: pd.DataFrame) -> list[dict[str, Any]]:
        """De-risk overlay dicts (one per overlaid bar).

        Each dict: timestamp (ms), event_type, magnitude, confidence,
        position_multiplier (reduction), reason. NO side/direction key.
        When multiple gated events hit the same bar, the highest-confidence
        event labels it (the reduction is the same 0.5 either way).
        """
        overlaid = self._event_bars(klines)
        if overlaid.empty:
            return []
        sigs = self._gated_signals()
        ev_ns = sigs["ts"].astype("int64").values
        before_ns = int(OVERLAY_WINDOW_BEFORE_HOURS * 3_600_000_000_000)
        after_ns = int(OVERLAY_WINDOW_AFTER_HOURS * 3_600_000_000_000)
        bar_ns = overlaid.index.astype("int64").values

        out: list[dict[str, Any]] = []
        for i, ts in enumerate(overlaid.index):
            bn = bar_ns[i]
            mask = (ev_ns >= bn - before_ns) & (ev_ns <= bn + after_ns)
            if not mask.any():
                continue
            best = sigs[mask].loc[sigs[mask]["confidence_f"].idxmax()]
            out.append({
                "timestamp": int(ts.value // 10**6),
                "event_type": str(best["event_type"]),
                "magnitude": float(best["magnitude"] or 0),
                "confidence": float(best["confidence"] or 0),
                "position_multiplier": DE_RISK_POSITION_MULT,
                "reason": (
                    f"TG event overlay de-risk event={best['event_type']} "
                    f"conf={best['confidence']:.2f} mult={DE_RISK_POSITION_MULT}"
                ),
            })
        return out

    def build_signals(self, klines: pd.DataFrame) -> list[Any]:
        """Not implemented: a de-risk overlay has no direction side.

        Signal (selene_space_types) requires side ∈ {buy, sell}; expressing a
        position reduction as a directional Signal would reintroduce the
        falsified direction alpha. Consume via build_signal_dicts instead.
        """
        raise NotImplementedError(
            "TGEventOverlaySource is de-risk only (no direction side). "
            "Use build_signal_dicts(); pydantic Signal requires side in {buy, sell}."
        )


__all__ = [
    "TGEventOverlaySource",
    "EVENT_OVERLAY_TYPES",
    "DEFAULT_ON_TYPES",
    "OVERLAY_WINDOW_BEFORE_HOURS",
    "OVERLAY_WINDOW_AFTER_HOURS",
    "DE_RISK_POSITION_MULT",
    "CONFIDENCE_MIN",
]

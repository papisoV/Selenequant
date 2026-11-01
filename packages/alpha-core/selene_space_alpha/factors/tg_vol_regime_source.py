"""TG volatility regime source — independent risk-budget input, no directional signals.

Separates the TG event PERSISTENCE signal (forward realized-vol elevation
24-48h) from the de-risk overlay's SHOCK signal (event-bar volume/amplitude
expansion, 0.5 x ±24h). Research (2026-08-14, tg_event_forward_vol) showed
forward-vol elevation is (symbol, type)-heterogeneous and only robust for
three combos:

  | combo      | 24h fwd vol | 48h fwd vol | n   |
  |------------|-------------|-------------|-----|
  | macro-ETH  | 1.20x p=0.002| 1.15x p=0.004| 92 |
  | macro-SOL  | 1.17x p=0.002| 1.15x p=0.002| 91 |
  | market-BTC | 1.19x p=0.002| 1.13x p=0.004| 68 |

This source converts those combos into a position-multiplier reduction only
inside a 48h AFTER-ONLY window (persistence is forward, unlike the overlay's
±24h). BINARY marker only — Q8 magnitude grading is falsified (macro graded
IC -0.24..-0.37, all significant negative), so REGIME_MULT is pre-registered
at 0.85 (vol-targeting median: position ∝ 1/vol) and never tuned per-event.

Architecture independence (SPEC 2026-08-14): vol_regime_mult is a SEPARATE
column from the overlay's event_overlay_mult. The backtest regime mode does
NOT import the overlay source. The two sources can coexist (no factory
use_event_overlay_source-style mutual exclusion) because this feed is NOT
wired into the factory yet — phase-1 is source + pre-registered A/B only.

Only pure loader helpers (load_tg_signals / map_signal_to_symbols /
SYMBOL_SHORT_MAP) are reused from tg_spike_source.
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

# Risk-budget multiplier inside a regime window. Pre-registered from
# vol-targeting (1/1.143 ≈ 0.875 at 48h, 1/1.187 ≈ 0.842 at 24h → median
# 0.85); NOT tuned by backtest (see the pre-registration record §3).
REGIME_MULT = 0.85

# Persistence window is forward-only: [event_ts, event_ts + 48h].
REGIME_WINDOW_AFTER_HOURS = 48.0

# Whitelisted (symbol_short, event_type) combos with robust forward-vol
# persistence (single source of truth — mirror in SPEC §3 and the backtest
# script's ImportError fallback). Everything else does NOT trigger.
REGIME_TYPES_BY_SYMBOL: dict[str, frozenset[str]] = {
    "BTC": frozenset({"market"}),
    "ETH": frozenset({"macro"}),
    "SOL": frozenset({"macro"}),
}

# Confidence / magnitude gate mirrors the event gate in signal_engine.py.
# No-op on the current snapshot (794/794 pass) — kept for parity, documented
# as a US-002 observation, not changed.
CONFIDENCE_MIN = 0.3


class TGVolRegimeSource:
    """Volatility-regime feed (no directional signals).

    Reads tg_factors.db, finds bars inside the forward 48h window for
    whitelisted (symbol_short, event_type) combos passing the event gate, and
    marks them with REGIME_MULT (0.85) — a risk-budget reduction, not a
    direction bet. Emitted dicts have NO side/direction key.

    Args:
        tg_db_path: path to tg_factors.db
        symbol: "BTC/USDT" / "ETH/USDT" / "SOL/USDT"
        interval: kline interval (default "4h")
        strategy_id: strategy id (interface parity with TGSpikeSource)
    """

    def __init__(
        self,
        tg_db_path: str | Path = DEFAULT_TG_DB_PATH,
        symbol: str = "BTC/USDT",
        interval: str = "4h",
        strategy_id: str = "tg-vol-regime-v1",
    ) -> None:
        self.tg_db_path = Path(tg_db_path)
        self.symbol = symbol
        self.symbol_short = SYMBOL_SHORT_MAP.get(symbol, symbol.split("/")[0])
        self.interval = interval
        self.strategy_id = strategy_id
        self._signals_cache: pd.DataFrame | None = None

    def _whitelisted_types(self) -> frozenset[str]:
        return REGIME_TYPES_BY_SYMBOL.get(self.symbol_short, frozenset())

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
        """Events mapped to this symbol, whitelisted combo, passing the gate."""
        whitelisted = self._whitelisted_types()
        signals = self._load_signals()
        if not whitelisted or signals.empty:
            return signals.iloc[0:0]
        sigs = signals[
            signals["mapped_symbols"].apply(lambda lst: self.symbol_short in lst)
        ].copy()
        if sigs.empty:
            return sigs
        sigs = sigs[sigs["event_type"].isin(whitelisted)]
        sigs = sigs[(sigs["confidence_f"] >= CONFIDENCE_MIN) & (sigs["magnitude_f"] > 0)]
        return sigs

    def _regime_bars(self, klines: pd.DataFrame) -> pd.DataFrame:
        """Klines subset whose bars sit inside a whitelisted event's 48h-forward window.

        A bar counts as in-regime when bar_open ∈ [msg_ts, msg_ts + 48h] for a
        gated whitelisted-combo event. After-only (persistence is forward).
        """
        sigs = self._gated_signals()
        if sigs.empty:
            return klines.iloc[0:0]

        after_ns = int(REGIME_WINDOW_AFTER_HOURS * 3_600_000_000_000)
        bar_ns = klines.index.astype("int64").values
        ev_ns = sigs["ts"].astype("int64").values
        in_window = np.zeros(len(klines), dtype=bool)
        for e in ev_ns:
            in_window |= (bar_ns >= e) & (bar_ns <= e + after_ns)
        return klines[in_window]

    def enrich_klines(self, klines: pd.DataFrame) -> pd.DataFrame:
        """Attach vol_regime_mult column: 1.0 normally, REGIME_MULT in window.

        Orthogonal to event_overlay_mult (overlay source) — separate column,
        separate consumer.
        """
        klines = klines.copy()
        klines["vol_regime_mult"] = 1.0
        regime = self._regime_bars(klines)
        if not regime.empty:
            klines.loc[regime.index, "vol_regime_mult"] = REGIME_MULT
        return klines

    def build_signal_dicts(self, klines: pd.DataFrame) -> list[dict[str, Any]]:
        """Regime-feed dicts (one per in-window bar).

        Each dict: timestamp (ms), event_type, magnitude, confidence,
        position_multiplier (risk-budget reduction), reason. NO side/direction
        key. When multiple gated events hit the same bar, the highest-confidence
        event labels it (the reduction is the same 0.85 either way).
        """
        regime = self._regime_bars(klines)
        if regime.empty:
            return []
        sigs = self._gated_signals()
        ev_ns = sigs["ts"].astype("int64").values
        after_ns = int(REGIME_WINDOW_AFTER_HOURS * 3_600_000_000_000)
        bar_ns = regime.index.astype("int64").values

        out: list[dict[str, Any]] = []
        for i, ts in enumerate(regime.index):
            bn = bar_ns[i]
            mask = (ev_ns >= bn - after_ns) & (ev_ns <= bn)  # event before-or-equal bar
            if not mask.any():
                continue
            best = sigs[mask].loc[sigs[mask]["confidence_f"].idxmax()]
            out.append({
                "timestamp": int(ts.value // 10**6),
                "event_type": str(best["event_type"]),
                "magnitude": float(best["magnitude"] or 0),
                "confidence": float(best["confidence"] or 0),
                "position_multiplier": REGIME_MULT,
                "reason": (
                    f"TG vol regime feed event={best['event_type']} "
                    f"conf={best['confidence']:.2f} mult={REGIME_MULT}"
                ),
            })
        return out

    def check(self, now_ts: int) -> list[Any]:
        """Live EventGuard interface: regime alerts active at now_ts (ms epoch).

        One EventAlert per whitelisted gated event whose 48h-forward window
        covers now_ts. All share REGIME_MULT, so EventGuard's min() yields
        0.85 whenever any is active. Times follow the EventAlert ms-epoch
        contract (start = event_ts, end = event_ts + 48h, half-open). Lazily
        imports EventAlert/EventSource to keep the factors → event_guard import
        edge time-acyclic (guard.py imports this module at module level).
        """
        sigs = self._gated_signals()
        if sigs.empty:
            return []
        from selene_space_alpha.event_guard.models import AlertLevel, EventAlert, EventSource

        after_ms = int(REGIME_WINDOW_AFTER_HOURS * 3_600_000)
        ev_ms = (sigs["ts"].astype("int64") // 10**6).rename("_ev_ms")
        in_window = (ev_ms <= now_ts) & (now_ts < ev_ms + after_ms)
        active = sigs[in_window]
        if active.empty:
            return []
        active_ms = ev_ms[in_window].astype(int)
        return [
            EventAlert(
                source=EventSource.TG_REGIME,
                level=AlertLevel.YELLOW,
                symbol=self.symbol_short,
                reason=(
                    f"TG vol regime: {row['event_type']} "
                    f"conf={row['confidence_f']:.2f} mult={REGIME_MULT}"
                ),
                start_ts=int(active_ms.loc[idx]),
                end_ts=int(active_ms.loc[idx]) + after_ms,
                multiplier=REGIME_MULT,
                event_type=str(row["event_type"]),
            )
            for idx, row in active.iterrows()
        ]

    def build_signals(self, klines: pd.DataFrame) -> list[Any]:
        """Not implemented: a regime feed has no direction side.

        Signal (selene_space_types) requires side ∈ {buy, sell}; expressing a
        risk-budget reduction as a directional Signal would reintroduce the
        falsified direction alpha. Consume via build_signal_dicts instead.
        """
        raise NotImplementedError(
            "TGVolRegimeSource is a risk-budget feed (no direction side). "
            "Use build_signal_dicts(); pydantic Signal requires side in {buy, sell}."
        )


__all__ = [
    "TGVolRegimeSource",
    "REGIME_MULT",
    "REGIME_WINDOW_AFTER_HOURS",
    "REGIME_TYPES_BY_SYMBOL",
    "CONFIDENCE_MIN",
]

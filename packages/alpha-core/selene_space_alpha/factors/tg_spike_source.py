"""DEPRECATED — TG spike signal source (directional).

.. deprecated:: 2026-08-13
    Directional alpha falsified by the five-dimension audit
    (TG_FACTOR_REVALIDATION_2026-08-13.md): no-short perm_p 0.12-0.17; BTC
    bullish→long -3.99% (harmful). The earlier "+12.72% / Sharpe ~4.62"
    claim was NOT reproducible out-of-sample and is retracted.
    Production path migrates to TGEventOverlaySource (de-risk only, no
    direction bets). Keep this class for historical backtest comparison;
    do NOT add new production consumers.

Historical implementation (2026-07-18 revalidation, kept for reference):
- vol_trigger = Σ signals (urgency × magnitude × exp(-Δt_min / halflife_min))
- strict spike detection: rolling(20) z-score, shift(1) for zero lookahead
- cluster dedup: gap >= 24 bars (96h)
- tier-based position sizing:
  S (≥2 symbols sync spike): 1.0x short
  A (listing/whale/security): 0.7x short
  B (market/macro/partnership): 0.5x short
  C_ETH_REG (ETH + regulatory): 0.5x long (reverse alpha)
  other: skip

Usage:
    from selene_space_alpha.factors.tg_spike_source import TGSpikeSource

    source = TGSpikeSource(
        tg_db_path="internal-archive",
        symbol="BTC/USDT",
        interval="4h",
    )
    signals = source.build_signals(klines_df)
    # signals: list[Signal] with side in {buy, sell}, strength = position_mult
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    from selene_space_types.strategy import Signal
    from selene_space_types.trading import OrderSide
except ImportError:  # pragma: no cover
    Signal = None  # type: ignore
    OrderSide = None  # type: ignore


# ---------------------------------------------------------------------------
# Configuration constants (validated 2026-07-18)
# ---------------------------------------------------------------------------

DEFAULT_TG_DB_PATH = "internal-archive"

# vol_trigger decay window (signals older than 24h don't contribute)
DECAY_WINDOW_HOURS = 24
DEFAULT_HALFLIFE_MIN = 360  # 6h

# Spike detection (strict, zero-lookahead)
SPIKE_WINDOW = 20
SPIKE_MIN_PERIODS = 20
SPIKE_Z_THRESHOLD = 2.0

# Cluster dedup
DEDUP_GAP_BARS = 24  # 96h on 4h bars

# Tier rules
A_TIER_EVENTS = {"listing", "whale", "security"}
B_TIER_EVENTS = {"market", "macro", "partnership"}

TIER_POSITION_MULT: dict[str, float] = {
    "S": 1.0,
    "A": 0.7,
    "B": 0.5,
    "C_ETH_REG": 0.5,
    "skip": 0.0,
}

# Per-symbol hold bars. Unified 24 bars (96h on 4h) after clean-data revalidation
# 2026-07-18: prior BTC=36 was tuned on klines.db with 1854 duplicate timestamps;
# deduped data shows hold=24 unified gives +11.46% / 36 trades / 61.1% win vs
# per-symbol +6.76% / 32 / 56.2%.
SYMBOL_HOLD_BARS: dict[str, int] = {
    "BTC/USDT": 24,   # 96h
    "ETH/USDT": 24,   # 96h
    "SOL/USDT": 24,   # 96h
}
DEFAULT_HOLD_BARS = 24

# Per-symbol TP/SL. Clean-data scan shows no TP/SL beats any TP/SL config on
# unified hold=24 — TP/SL tuned on dirty data was an artifact of duplicate bars.
SYMBOL_TP_PCT: dict[str, float] = {
    "BTC/USDT": 0.0,
    "ETH/USDT": 0.0,
    "SOL/USDT": 0.0,
}
SYMBOL_SL_PCT: dict[str, float] = {
    "BTC/USDT": 0.0,
    "ETH/USDT": 0.0,
    "SOL/USDT": 0.0,
}

# Macro event overlay (2026-07-18).
# CPI/FOMC/NFP ±24h windows contain 12/33 factory trades; mean pnl -1.43%,
# 25% win rate (vs full-sample +0.5% / 57.6%). Macro events during these
# windows produce trend-continue behavior (not mean reversion), so shorting
# TG spikes there loses. Overlay halves position size (reduce, not skip —
# 12 trades is borderline significant at p=7.3%, single-tail, so we keep
# exposure but cut it).
DEFAULT_MACRO_DB_PATH = "data/macro_events.db"
MACRO_EVENT_TYPES = ("CPI", "FOMC", "NFP")
MACRO_WINDOW_BEFORE_HOURS = 24.0
MACRO_WINDOW_AFTER_HOURS = 24.0
MACRO_OVERLAY_POSITION_MULT = 0.5

# TG event overlay (2026-07-19, Step 2 of TG-AI-Agent linkage plan).
# Mirrors EventGuard.get_tg_multiplier (TG-only source) inside the factory
# path so backtests can validate the ±2h SECURITY/REGULATORY event protection
# that the live strategy_runner applies via EventGuard. Without this overlay,
# factory backtests (which don't call EventGuard) would diverge from live
# behavior. GEOPOLITICAL is excluded here because the live TGSource sets it
# to ORANGE level too but with a longer 4h half-life — keeping parity with
# the most common (SECURITY/REGULATORY, 2h) case is enough for backtest
# validation; the live EventGuard.TGSource remains the source of truth.
TG_OVERLAY_EVENT_TYPES = ("SECURITY", "REGULATORY")
TG_OVERLAY_WINDOW_BEFORE_HOURS = 2.0
TG_OVERLAY_WINDOW_AFTER_HOURS = 2.0
TG_OVERLAY_POSITION_MULT = 0.7

NOISE_PREFIX = "noise:"
BAR_MS_BY_INTERVAL: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "1d": 86_400_000,
}

# B-mapping: '*' → all three symbols
ASTERISK_SYMBOLS = ("BTC", "ETH", "SOL")
SYMBOL_SHORT_MAP = {
    "BTC/USDT": "BTC",
    "ETH/USDT": "ETH",
    "SOL/USDT": "SOL",
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

# Signal dicts are plain dict[str, Any]; see build_signal_dicts for shape.


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_tg_signals(tg_db_path: str | Path = DEFAULT_TG_DB_PATH,
                    filter_noise: bool = True) -> pd.DataFrame:
    """Load TG signals from tg_factors.db, optionally filtering bot noise.

    Returns DataFrame with columns:
        ts (datetime), symbols (list[str]), direction, magnitude, urgency,
        halflife_min, event_type, reasoning
    """
    conn = sqlite3.connect(str(tg_db_path))
    query = (
        "SELECT msg_id, ts, symbols, direction, magnitude, urgency, confidence, "
        "halflife_min, event_type, reasoning FROM tg_factors"
    )
    if filter_noise:
        query += f" WHERE reasoning NOT LIKE '{NOISE_PREFIX}%'"
    df = pd.read_sql_query(query, conn)
    conn.close()

    df['ts'] = pd.to_datetime(df['ts'])
    df['symbols_list'] = df['symbols'].apply(lambda s: json.loads(s) if s else [])
    return df.sort_values('ts').reset_index(drop=True)


def map_signal_to_symbols(symbols_list: list[str]) -> list[str]:
    """B-mapping: '*' → BTC+ETH+SOL, else explicit short names."""
    if "*" in symbols_list:
        return list(ASTERISK_SYMBOLS)
    return [s for s in symbols_list if s in ASTERISK_SYMBOLS]


# ---------------------------------------------------------------------------
# vol_trigger aggregation
# ---------------------------------------------------------------------------

def attach_vol_trigger(klines: pd.DataFrame, signals: pd.DataFrame,
                       symbol_short: str) -> pd.DataFrame:
    """Attach vol_trigger column to klines DataFrame.

    vol_trigger[bar] = Σ signals (urgency × magnitude × exp(-Δt_min / halflife_min))
    where Δt = bar_open_ts - msg_ts, only signals in [bar_open - 24h, bar_open].

    Dedups klines by index (klines.db may have duplicate timestamps).
    """
    klines = klines.copy()
    if not klines.index.is_unique:
        klines = klines[~klines.index.duplicated(keep='first')]
    klines['vol_trigger'] = 0.0
    klines['event_count'] = 0

    if signals.empty:
        return klines

    # Filter signals to this symbol
    sigs = signals[signals['mapped_symbols'].apply(lambda lst: symbol_short in lst)].copy()
    if sigs.empty:
        return klines

    bar_ts_ms = klines['timestamp'].astype('int64').values if 'timestamp' in klines.columns else (
        klines.index.astype('int64') // 10**6
    ).astype('int64')

    sig_ts_ms = (sigs['ts'].astype('int64') // 10**6).values
    sig_hl = sigs['halflife_min'].fillna(DEFAULT_HALFLIFE_MIN).astype(float).values
    sig_val = (sigs['urgency'] * sigs['magnitude']).values

    decay_window_ms = DECAY_WINDOW_HOURS * 3_600_000
    vol_trigger = np.zeros(len(klines))
    event_count = np.zeros(len(klines))

    for i, bt in enumerate(bar_ts_ms):
        mask = (sig_ts_ms >= bt - decay_window_ms) & (sig_ts_ms <= bt)
        if not mask.any():
            continue
        dt_min = (bt - sig_ts_ms[mask]) / 60_000.0
        decay = np.exp(-dt_min / sig_hl[mask])
        contributions = sig_val[mask] * decay
        vol_trigger[i] = contributions.sum()
        event_count[i] = mask.sum()

    klines['vol_trigger'] = vol_trigger
    klines['event_count'] = event_count.astype(int)
    return klines


def compute_spike(klines: pd.DataFrame,
                  window: int = SPIKE_WINDOW,
                  min_periods: int = SPIKE_MIN_PERIODS,
                  z_threshold: float = SPIKE_Z_THRESHOLD) -> pd.DataFrame:
    """Compute spike column from vol_trigger via strict zero-lookahead z-score.

    - rolling(window, min_periods).mean/std
    - z = (vol_trigger - mean) / std
    - spike_z = z.shift(1)  # current bar uses previous bar's z (zero lookahead)
    - spike = (spike_z > z_threshold).astype(int)
    """
    klines = klines.copy()
    mean = klines['vol_trigger'].rolling(window, min_periods=min_periods).mean()
    std = klines['vol_trigger'].rolling(window, min_periods=min_periods).std()
    z = (klines['vol_trigger'] - mean) / std.replace(0, np.nan)
    klines['spike_z'] = z.shift(1)
    klines['spike'] = (klines['spike_z'] > z_threshold).astype(int)
    return klines


def cluster_dedup_spikes(spike_ts: list | pd.DatetimeIndex,
                         gap_bars: int = DEDUP_GAP_BARS,
                         bar_hours: float = 4.0) -> list:
    """Dedup spikes within gap_bars of each other (cluster → first)."""
    if len(spike_ts) == 0:
        return []
    sorted_ts = sorted(spike_ts)
    kept = [sorted_ts[0]]
    gap_seconds = gap_bars * bar_hours * 3600
    for ts in sorted_ts[1:]:
        if (ts - kept[-1]).total_seconds() >= gap_seconds:
            kept.append(ts)
    return kept


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------

def find_dominant_event(bar_ts: pd.Timestamp, signals: pd.DataFrame,
                        lookback_hours: int = 24) -> str | None:
    """Find dominant event_type in the lookback window before bar_ts.

    Dominant = event with highest urgency*magnitude.
    """
    bar_ns = bar_ts.value
    sig_ts_ns = signals['ts'].astype('int64').values
    mask = (sig_ts_ns >= bar_ns - lookback_hours * 3600 * 10**9) & (sig_ts_ns <= bar_ns)
    if not mask.any():
        return None
    evts = signals['event_type'].values[mask]
    ums = (signals['urgency'] * signals['magnitude']).values[mask]
    return evts[int(np.argmax(ums))]


def is_multi_symbol_spike(bar_ts: pd.Timestamp,
                          other_symbol_spike_sets: list[set]) -> bool:
    """Check if ≥2 symbols have a spike at the same bar (±0 tolerance)."""
    cnt = 1  # current symbol
    for sset in other_symbol_spike_sets:
        if bar_ts in sset:
            cnt += 1
    return cnt >= 2


def classify_tier(spike_bar: bool, multi_sym: bool, event_type: str | None,
                  symbol_short: str) -> str:
    """Classify a spike bar into a tier.

    Returns one of: 'S', 'A', 'B', 'C_ETH_REG', 'skip'.
    """
    if not spike_bar:
        return 'skip'
    if multi_sym:
        return 'S'
    if event_type in A_TIER_EVENTS:
        return 'A'
    if event_type in B_TIER_EVENTS:
        return 'B'
    if symbol_short == 'ETH' and event_type == 'regulatory':
        return 'C_ETH_REG'
    return 'skip'


# ---------------------------------------------------------------------------
# Main source
# ---------------------------------------------------------------------------

class TGSpikeSource:
    """DEPRECATED — directional TG spike source (2026-08-13).

    Direction alpha falsified; migrate to TGEventOverlaySource (de-risk only).
    Do NOT add new production consumers. Kept for historical backtest
    comparison and reference only.

    Produces directional trading signals: reads tg_factors.db, computes
    vol_trigger aggregation on 4h bars, detects strict spikes, dedups
    clusters, classifies tiers, and emits Signal objects with
    side ∈ {buy, sell} and strength = position_multiplier.

    Args:
        tg_db_path: path to tg_factors.db
        symbol: "BTC/USDT" / "ETH/USDT" / "SOL/USDT"
        interval: kline interval (default "4h")
        z_threshold: spike z-score threshold (default 2.0)
        hold_bars: override per-symbol default hold period
        tp_pct: override per-symbol default TP
        sl_pct: override per-symbol default SL
        strategy_id: strategy_id to attach to emitted Signals
    """

    def __init__(
        self,
        tg_db_path: str | Path = DEFAULT_TG_DB_PATH,
        symbol: str = "BTC/USDT",
        interval: str = "4h",
        z_threshold: float = SPIKE_Z_THRESHOLD,
        hold_bars: int | None = None,
        tp_pct: float | None = None,
        sl_pct: float | None = None,
        strategy_id: str = "tg-spike-v1",
        other_symbol_spike_sets: list[set] | None = None,
        macro_db_path: str | Path | None = DEFAULT_MACRO_DB_PATH,
        enable_macro_overlay: bool = True,
        enable_tg_overlay: bool = True,
    ) -> None:
        self.tg_db_path = Path(tg_db_path)
        self.symbol = symbol
        self.symbol_short = SYMBOL_SHORT_MAP.get(symbol, symbol.split("/")[0])
        self.interval = interval
        self.z_threshold = z_threshold
        self.hold_bars = hold_bars or SYMBOL_HOLD_BARS.get(symbol, DEFAULT_HOLD_BARS)
        self.tp_pct = tp_pct if tp_pct is not None else SYMBOL_TP_PCT.get(symbol, 0.0)
        self.sl_pct = sl_pct if sl_pct is not None else SYMBOL_SL_PCT.get(symbol, 0.0)
        self.strategy_id = strategy_id
        self._other_spike_sets = other_symbol_spike_sets or []
        self.macro_db_path = Path(macro_db_path) if macro_db_path else None
        self.enable_macro_overlay = enable_macro_overlay
        self.enable_tg_overlay = enable_tg_overlay
        self._macro_event_ts: list[pd.Timestamp] | None = None
        self._tg_overlay_event_ts: list[pd.Timestamp] | None = None
        self._signals_cache: pd.DataFrame | None = None

    def _load_signals(self) -> pd.DataFrame:
        df = load_tg_signals(self.tg_db_path, filter_noise=True)
        df['mapped_symbols'] = df['symbols_list'].apply(map_signal_to_symbols)
        return df

    def _load_macro_events(self) -> list[pd.Timestamp]:
        """Load CPI/FOMC/NFP event timestamps from macro_events.db (cached)."""
        if self._macro_event_ts is not None:
            return self._macro_event_ts
        if not self.enable_macro_overlay or self.macro_db_path is None:
            self._macro_event_ts = []
            return self._macro_event_ts
        if not self.macro_db_path.exists():
            self._macro_event_ts = []
            return self._macro_event_ts
        conn = sqlite3.connect(str(self.macro_db_path))
        placeholders = ",".join("?" * len(MACRO_EVENT_TYPES))
        df = pd.read_sql_query(
            f"SELECT timestamp_utc FROM macro_events WHERE event_type IN ({placeholders})",
            conn, params=MACRO_EVENT_TYPES,
        )
        conn.close()
        if df.empty:
            self._macro_event_ts = []
            return self._macro_event_ts
        self._macro_event_ts = [
            pd.Timestamp(int(ms), unit='ms', tz='UTC') for ms in df['timestamp_utc'].tolist()
        ]
        return self._macro_event_ts

    def _macro_overlay_mult(self, bar_ts: pd.Timestamp) -> float:
        """Return position multiplier for macro overlay (1.0 if no overlay)."""
        events = self._load_macro_events()
        if not events:
            return 1.0
        before_ns = MACRO_WINDOW_BEFORE_HOURS * 3600 * 10**9
        after_ns = MACRO_WINDOW_AFTER_HOURS * 3600 * 10**9
        bar_ns = bar_ts.value
        for ev in events:
            ev_ns = ev.value
            if (ev_ns - before_ns) <= bar_ns <= (ev_ns + after_ns):
                return MACRO_OVERLAY_POSITION_MULT
        return 1.0

    def _load_tg_overlay_events(self) -> list[pd.Timestamp]:
        """Load SECURITY/REGULATORY tg_events timestamps from macro_events.db (cached).

        These are the same rows EventGuard.TGSource would consult at execution
        time. Reading them here lets the factory backtest mirror live behavior.
        """
        if self._tg_overlay_event_ts is not None:
            return self._tg_overlay_event_ts
        if not self.enable_tg_overlay or self.macro_db_path is None:
            self._tg_overlay_event_ts = []
            return self._tg_overlay_event_ts
        if not self.macro_db_path.exists():
            self._tg_overlay_event_ts = []
            return self._tg_overlay_event_ts
        conn = sqlite3.connect(str(self.macro_db_path))
        placeholders = ",".join("?" * len(TG_OVERLAY_EVENT_TYPES))
        df = pd.read_sql_query(
            f"SELECT timestamp FROM tg_events WHERE event_type IN ({placeholders}) "
            f"AND alert_level IN ('orange', 'red')",
            conn, params=TG_OVERLAY_EVENT_TYPES,
        )
        conn.close()
        if df.empty:
            self._tg_overlay_event_ts = []
            return self._tg_overlay_event_ts
        self._tg_overlay_event_ts = [
            pd.Timestamp(int(ms), unit='ms', tz='UTC') for ms in df['timestamp'].tolist()
        ]
        return self._tg_overlay_event_ts

    def _tg_overlay_mult(self, bar_ts: pd.Timestamp) -> float:
        """Return position multiplier for TG event overlay (1.0 if no overlay).

        Mirrors EventGuard.get_tg_multiplier: ±2h SECURITY/REGULATORY → 0.7.
        """
        events = self._load_tg_overlay_events()
        if not events:
            return 1.0
        before_ns = TG_OVERLAY_WINDOW_BEFORE_HOURS * 3600 * 10**9
        after_ns = TG_OVERLAY_WINDOW_AFTER_HOURS * 3600 * 10**9
        bar_ns = bar_ts.value
        for ev in events:
            ev_ns = ev.value
            if (ev_ns - before_ns) <= bar_ns <= (ev_ns + after_ns):
                return TG_OVERLAY_POSITION_MULT
        return 1.0

    def enrich_klines(self, klines: pd.DataFrame) -> pd.DataFrame:
        """Attach vol_trigger, spike, dominant_event, multi_sym, tier columns."""
        signals = self._load_signals()
        klines = attach_vol_trigger(klines, signals, self.symbol_short)
        klines = compute_spike(klines, z_threshold=self.z_threshold)

        # dominant_event for spike bars only
        klines['dominant_event'] = None
        spike_mask = klines['spike'] == 1
        for ts in klines.index[spike_mask]:
            klines.at[ts, 'dominant_event'] = find_dominant_event(ts, signals)

        # multi_sym: check other symbols' spike sets
        klines['multi_sym'] = False
        if self._other_spike_sets:
            for ts in klines.index[spike_mask]:
                klines.at[ts, 'multi_sym'] = is_multi_symbol_spike(ts, self._other_spike_sets)

        # tier (vectorized where possible; multi_sym/event may be None)
        spike_mask = klines['spike'] == 1
        tiers = ['skip'] * len(klines)
        for i, (ts, row) in enumerate(klines.iterrows()):
            if not spike_mask.iloc[i]:
                continue
            multi = bool(row['multi_sym']) if not isinstance(row['multi_sym'], pd.Series) else bool(row['multi_sym'].iloc[0])
            evt = row['dominant_event']
            evt = evt.iloc[0] if isinstance(evt, pd.Series) else evt
            tiers[i] = classify_tier(
                spike_bar=True,
                multi_sym=multi,
                event_type=evt,
                symbol_short=self.symbol_short,
            )
        klines['tier'] = tiers
        return klines

    def dedup_spikes(self, klines: pd.DataFrame) -> list[pd.Timestamp]:
        """Return cluster-deduped spike timestamps (tier != skip)."""
        actionable = klines[klines['tier'] != 'skip']
        spike_ts = list(actionable.index)
        return cluster_dedup_spikes(spike_ts, gap_bars=DEDUP_GAP_BARS,
                                     bar_hours=_bar_hours(self.interval))

    def build_signal_dicts(self, klines: pd.DataFrame) -> list[dict[str, Any]]:
        """Build list of signal dicts (lightweight, no pydantic dep).

        Each dict has: timestamp (ms), side, strength, tier, event_type,
        hold_bars, tp_pct, sl_pct, reason.
        """
        enriched = self.enrich_klines(klines)
        dedup_ts = set(self.dedup_spikes(enriched))

        signals: list[dict[str, Any]] = []
        for ts in sorted(dedup_ts):
            row = enriched.loc[ts]
            tier = row['tier']
            pos_mult = TIER_POSITION_MULT.get(tier, 0.0)
            if pos_mult <= 0:
                continue
            side = 'buy' if tier == 'C_ETH_REG' else 'sell'
            macro_mult = self._macro_overlay_mult(ts)
            tg_mult = self._tg_overlay_mult(ts)
            final_strength = pos_mult * macro_mult * tg_mult
            macro_tag = ' macro=0.5' if macro_mult < 1.0 else ''
            tg_tag = ' tg=0.7' if tg_mult < 1.0 else ''
            signals.append({
                'timestamp': int(ts.value // 10**6),
                'side': side,
                'strength': final_strength,
                'tier': tier,
                'event_type': row['dominant_event'] or '',
                'hold_bars': self.hold_bars,
                'tp_pct': self.tp_pct,
                'sl_pct': self.sl_pct,
                'reason': f"TG spike z={row['spike_z']:.2f} tier={tier} event={row['dominant_event']}{macro_tag}{tg_tag}",
            })
        return signals

    def build_signals(self, klines: pd.DataFrame) -> list[Any]:
        """Build pydantic Signal objects (requires selene_space_types)."""
        if Signal is None or OrderSide is None:
            raise ImportError("selene_space_types not available; use build_signal_dicts")
        dicts = self.build_signal_dicts(klines)
        out = []
        for d in dicts:
            out.append(Signal(
                strategy_id=self.strategy_id,
                symbol=self.symbol,
                side=OrderSide(d['side']),
                strength=d['strength'],
                timestamp=d['timestamp'],
                reason=d['reason'],
            ))
        return out


def _bar_hours(interval: str) -> float:
    """Convert interval string to hours."""
    ms = BAR_MS_BY_INTERVAL.get(interval)
    if ms:
        return ms / 3_600_000
    # fallback parsing
    if interval.endswith('m'):
        return float(interval[:-1]) / 60
    if interval.endswith('h'):
        return float(interval[:-1])
    if interval.endswith('d'):
        return float(interval[:-1]) * 24
    return 4.0  # safe default


def compute_other_symbol_spike_sets(tg_db_path: str | Path = DEFAULT_TG_DB_PATH,
                                    klines_by_symbol: dict[str, pd.DataFrame] | None = None,
                                    interval: str = "4h") -> dict[str, set]:
    """Pre-compute spike sets for all symbols, for multi_sym detection.

    Call this once before building sources for multiple symbols, then pass
    each symbol's spike set (minus itself) to TGSpikeSource via other_symbol_spike_sets.

    Returns {symbol: set of spike Timestamps}.
    """
    signals = load_tg_signals(tg_db_path, filter_noise=True)
    signals['mapped_symbols'] = signals['symbols_list'].apply(map_signal_to_symbols)

    out: dict[str, set] = {}
    if klines_by_symbol is None:
        return out

    for sym, klines in klines_by_symbol.items():
        sym_short = SYMBOL_SHORT_MAP.get(sym, sym.split("/")[0])
        klines_vt = attach_vol_trigger(klines, signals, sym_short)
        klines_vt = compute_spike(klines_vt)
        out[sym] = set(klines_vt.index[klines_vt['spike'] == 1])
    return out


__all__ = [
    "TGSpikeSource",
    "load_tg_signals",
    "map_signal_to_symbols",
    "attach_vol_trigger",
    "compute_spike",
    "cluster_dedup_spikes",
    "find_dominant_event",
    "is_multi_symbol_spike",
    "classify_tier",
    "compute_other_symbol_spike_sets",
    "TIER_POSITION_MULT",
    "A_TIER_EVENTS",
    "B_TIER_EVENTS",
    "SYMBOL_HOLD_BARS",
    "SYMBOL_TP_PCT",
    "SYMBOL_SL_PCT",
]

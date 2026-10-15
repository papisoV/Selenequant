"""
TG 因子重测 pipeline (2026-07-18)

口径:
- filter_noise: reasoning NOT LIKE 'noise:%'
- B 口径: symbols 含 '*'  → BTC/ETH/SOL 都算; 含 'BTC' → 仅 BTC; 含 'ETH' → 仅 ETH; 含 'SOL' → 仅 SOL
- 聚合: vol_trigger = urgency * magnitude * exp(-dt_halflife_min / halflife)
  - halflife 取消息自带 halflife_min (默认 360 min = 6h)
  - dt = bar_ts_min - msg_ts_min (信号到下一个 4h bar open 的延迟)
- spike: vol_trigger rolling(20).mean/std z-score > 2.0, shift(1) 零 lookahead, min_periods=20
- cluster dedup: gap >= 24 bars (96h) 取第一个
- alpha: spike@96h net return vs nonspike@96h net return
"""
from __future__ import annotations
import sqlite3
import json
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np

REPO = Path(__file__).resolve().parents[1]
KLINES_DB = REPO / 'services' / 'local-api' / 'data' / 'klines.db'
TG_DB = REPO / '实验' / 'tg_factors.db'
OUT_DIR = REPO / '实验'
OUT_DIR.mkdir(exist_ok=True)

SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
SYM_KEY = {'BTC/USDT': 'BTC', 'ETH/USDT': 'ETH', 'SOL/USDT': 'SOL'}
INTERVAL = '4h'
BAR_MS = 4 * 3600 * 1000
HALFLIFE_DEFAULT_MIN = 360  # 6h
NOISE_PREFIX = 'noise:'

SPIKE_Z_THRESHOLD = 2.0
SPIKE_WINDOW = 20
SPIKE_MIN_PERIODS = 20
DEDUP_GAP_BARS = 24   # 96h
HOLD_BARS = 24        # 96h short window


def load_klines(symbol: str) -> pd.DataFrame:
    conn = sqlite3.connect(str(KLINES_DB))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE exchange='binance' AND market_type='futures' AND symbol=? AND interval=? "
        "ORDER BY timestamp",
        conn, params=(symbol, INTERVAL)
    )
    conn.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df = df.set_index('ts').sort_index()
    return df


def load_tg_signals() -> pd.DataFrame:
    """返回所有真信号 (已过滤 noise)."""
    conn = sqlite3.connect(str(TG_DB))
    df = pd.read_sql_query(
        "SELECT msg_id, ts, symbols, direction, magnitude, urgency, confidence, halflife_min, event_type, reasoning "
        "FROM tg_factors WHERE reasoning NOT LIKE ?",
        conn, params=(f'{NOISE_PREFIX}%',)
    )
    conn.close()
    df['ts'] = pd.to_datetime(df['ts'])
    df['symbols_list'] = df['symbols'].apply(lambda s: json.loads(s) if s else [])
    return df.sort_values('ts').reset_index(drop=True)


def map_signal_to_symbols(row) -> list[str]:
    """B 口径: '*' → 三个币种; 否则按显式命名."""
    syms = row['symbols_list']
    if '*' in syms:
        return ['BTC', 'ETH', 'SOL']
    out = []
    for s in syms:
        if s in ('BTC', 'ETH', 'SOL'):
            out.append(s)
    return out


def attach_vol_trigger_to_bars(klines: pd.DataFrame, signals: pd.DataFrame, sym_short: str) -> pd.DataFrame:
    """把 vol_trigger 衰减聚合到 4h bar.

    每个 bar 的 vol_trigger = sum over signals (msg in [bar_open - decay_window, bar_open])
      of  urgency * magnitude * exp(-(bar_open - msg_ts) / halflife)
    信号延迟至少要到 bar open 才能用 (零 lookahead: bar close 时刻才能看到 bar open 前的信号)
    """
    klines = klines.copy()
    klines['vol_trigger'] = 0.0
    klines['event_count'] = 0

    bar_ts_ms = klines['timestamp'].astype('int64').values
    # 用 bar 的 open timestamp 作为信号生效点 (信号在 bar open 之前到达, 在 bar open 起作用)
    # shift(1) 留给后面 z-score 计算使用, 这里聚合不 shift

    sigs = signals.copy()
    sigs = sigs[sigs['mapped_symbols'].apply(lambda lst: sym_short in lst)]
    sig_ts_ms = (sigs['ts'].astype('int64') // 10**6).values  # ns -> ms
    sig_halflife = sigs['halflife_min'].fillna(HALFLIFE_DEFAULT_MIN).astype(float).values
    sig_val = (sigs['urgency'] * sigs['magnitude']).values
    sig_event = sigs['event_type'].values

    # 对每个 bar, 累加 decay
    vol_trigger = np.zeros(len(klines))
    event_count = np.zeros(len(klines))
    # 为效率: 只考虑 bar_ts - 24h <= sig_ts <= bar_ts (24h = 6 bars 前)
    decay_window_ms = 24 * 3600 * 1000
    for i, bt in enumerate(bar_ts_ms):
        # 信号在 bar open 前到达 (bt - decay_window <= sig_ts <= bt)
        mask = (sig_ts_ms >= bt - decay_window_ms) & (sig_ts_ms <= bt)
        if not mask.any():
            continue
        dt_min = (bt - sig_ts_ms[mask]) / 60000.0
        hl = sig_halflife[mask]
        decay = np.exp(-dt_min / hl)
        contributions = sig_val[mask] * decay
        vol_trigger[i] = contributions.sum()
        event_count[i] = mask.sum()

    klines['vol_trigger'] = vol_trigger
    klines['event_count'] = event_count.astype(int)
    return klines


def compute_spike(klines: pd.DataFrame) -> pd.DataFrame:
    """strict 口径: rolling(20) z-score, shift(1) 零 lookahead."""
    klines = klines.copy()
    mean = klines['vol_trigger'].rolling(SPIKE_WINDOW, min_periods=SPIKE_MIN_PERIODS).mean()
    std = klines['vol_trigger'].rolling(SPIKE_WINDOW, min_periods=SPIKE_MIN_PERIODS).std()
    z = (klines['vol_trigger'] - mean) / std.replace(0, np.nan)
    # shift(1): 当前 bar 用上一个 bar 的 z (零 lookahead)
    klines['spike_z'] = z.shift(1)
    klines['spike'] = (klines['spike_z'] > SPIKE_Z_THRESHOLD).astype(int)
    return klines


def cluster_dedup_spikes(spike_ts: pd.DatetimeIndex, gap_bars: int = DEDUP_GAP_BARS) -> list[pd.Timestamp]:
    """gap >= gap_bars 的 spike 聚成一簇, 取第一个."""
    if len(spike_ts) == 0:
        return []
    sorted_ts = sorted(spike_ts)
    kept = [sorted_ts[0]]
    for ts in sorted_ts[1:]:
        if (ts - kept[-1]).total_seconds() >= gap_bars * 4 * 3600:
            kept.append(ts)
    return kept


def _find_pos(klines: pd.DataFrame, ts: pd.Timestamp) -> int | None:
    """返回 ts 在 klines.index 中的 integer position; None 表示找不到."""
    try:
        loc = klines.index.get_loc(ts)
        if isinstance(loc, slice):
            return loc.start
        if isinstance(loc, (list, np.ndarray)):
            return int(loc[0]) if len(loc) else None
        return int(loc)
    except KeyError:
        return None


def net_return_96h(klines: pd.DataFrame, entry_ts: pd.Timestamp, hold_bars: int = HOLD_BARS) -> float | None:
    """开仓后 hold_bars 个 bar 的 net return (short: -ret)."""
    idx = _find_pos(klines, entry_ts)
    if idx is None:
        return None
    exit_idx = idx + hold_bars
    if exit_idx >= len(klines):
        return None
    entry_close = klines.iloc[idx]['close']
    exit_close = klines.iloc[exit_idx]['close']
    ret = (exit_close - entry_close) / entry_close
    return -ret  # short return


def nonspike_sample(klines: pd.DataFrame, spike_ts_set: set, gap_bars: int = DEDUP_GAP_BARS,
                    hold_bars: int = HOLD_BARS, max_samples: int = 200) -> list[pd.Timestamp]:
    """从 non-spike bars 里采样: 排除 spike 前后 gap_bars 范围, 保证独立性."""
    n = len(klines)
    blocked = set()
    for st in spike_ts_set:
        i = _find_pos(klines, st)
        if i is None:
            continue
        for j in range(max(0, i - gap_bars), min(n, i + hold_bars + gap_bars)):
            blocked.add(j)
    candidates_idx = [i for i in range(n) if i not in blocked and i + hold_bars < n]
    if len(candidates_idx) > max_samples:
        rng = np.random.default_rng(42)
        candidates_idx = rng.choice(candidates_idx, size=max_samples, replace=False).tolist()
    candidates_idx = sorted(candidates_idx)
    kept_idx = []
    for i in candidates_idx:
        if kept_idx and (i - kept_idx[-1]) < gap_bars:
            continue
        kept_idx.append(i)
    return [klines.index[i] for i in kept_idx]


def run_one_symbol(sym: str, signals: pd.DataFrame) -> dict:
    sym_short = SYM_KEY[sym]
    klines = load_klines(sym)
    klines = attach_vol_trigger_to_bars(klines, signals, sym_short)
    klines = compute_spike(klines)

    # 限定到 TG 信号时间窗口 (2025-11-01 之后)
    tg_start = pd.Timestamp('2025-11-01', tz='UTC')
    klines = klines[klines.index >= tg_start]

    spike_bars = klines[klines['spike'] == 1]
    spike_ts_all = list(spike_bars.index)
    spike_ts_dedup = cluster_dedup_spikes(spike_ts_all, gap_bars=DEDUP_GAP_BARS)

    # spike@96h net return (dedup 后)
    spike_returns = []
    for ts in spike_ts_dedup:
        r = net_return_96h(klines, ts, HOLD_BARS)
        if r is not None:
            spike_returns.append(r)

    # nonspike sample
    nonspike_ts = nonspike_sample(klines, set(spike_ts_dedup), gap_bars=DEDUP_GAP_BARS,
                                  hold_bars=HOLD_BARS, max_samples=200)
    nonspike_returns = []
    for ts in nonspike_ts:
        r = net_return_96h(klines, ts, HOLD_BARS)
        if r is not None:
            nonspike_returns.append(r)

    def stats(rs):
        if not rs:
            return {'n': 0, 'mean': None, 'median': None, 'win': None}
        arr = np.array(rs)
        return {
            'n': len(arr),
            'mean': float(arr.mean()),
            'median': float(np.median(arr)),
            'win': float((arr > 0).mean()),
        }

    spike_stats = stats(spike_returns)
    nonspike_stats = stats(nonspike_returns)
    alpha = (spike_stats['mean'] - nonspike_stats['mean']) if (spike_stats['mean'] is not None and nonspike_stats['mean'] is not None) else None

    return {
        'symbol': sym,
        'bar_count': len(klines),
        'spike_total': len(spike_ts_all),
        'spike_independent': len(spike_ts_dedup),
        'spike_returns': spike_returns,
        'nonspike_returns': nonspike_returns,
        'spike_stats': spike_stats,
        'nonspike_stats': nonspike_stats,
        'alpha': alpha,
        'spike_ts_dedup': [str(t) for t in spike_ts_dedup],
        'vol_trigger_mean': float(klines['vol_trigger'].mean()),
        'vol_trigger_max': float(klines['vol_trigger'].max()),
        'event_count_total': int(klines['event_count'].sum()),
        'bars_with_signal': int((klines['event_count'] > 0).sum()),
    }


def main():
    print('=' * 70)
    print('TG 因子重测 pipeline (2026-07-18) - B 口径')
    print('=' * 70)
    signals = load_tg_signals()
    print(f'真信号总数: {len(signals)}')
    signals['mapped_symbols'] = signals.apply(map_signal_to_symbols, axis=1)
    # 按币种拆分计数
    for s in ['BTC', 'ETH', 'SOL']:
        n = signals['mapped_symbols'].apply(lambda lst: s in lst).sum()
        print(f'  {s}: {n} 条')

    print()
    print(f'klines 数据源: {KLINES_DB}')
    print(f'tg_factors 数据源: {TG_DB}')
    print('时间窗口: 2025-11-01 -> 2026-07-18')
    print(f'strict 口径: rolling({SPIKE_WINDOW}) z>{SPIKE_Z_THRESHOLD}, min_periods={SPIKE_MIN_PERIODS}, shift(1)')
    print(f'cluster dedup: gap>={DEDUP_GAP_BARS} bars ({DEDUP_GAP_BARS*4}h)')
    print(f'hold: {HOLD_BARS} bars ({HOLD_BARS*4}h)')
    print()

    results = {}
    for sym in SYMBOLS:
        print(f'--- {sym} ---')
        r = run_one_symbol(sym, signals)
        results[sym] = r
        print(f"  bars: {r['bar_count']}, 总 spike: {r['spike_total']}, 独立 spike: {r['spike_independent']}")
        print(f"  spike@96h: {r['spike_stats']}")
        print(f"  nonspike@96h: {r['nonspike_stats']}")
        print(f"  ALPHA = {r['alpha']}")
        print()

    print('=' * 70)
    print('汇总')
    print('=' * 70)
    print(f"{'币种':<10} {'独立spike':<12} {'spike_net':<15} {'nonspike_net':<15} {'ALPHA':<12}")
    for sym in SYMBOLS:
        r = results[sym]
        s = r['spike_stats']
        ns = r['nonspike_stats']
        sn = f"{s['mean']:+.4%} (n={s['n']})" if s['mean'] is not None else 'N/A'
        nsn = f"{ns['mean']:+.4%} (n={ns['n']})" if ns['mean'] is not None else 'N/A'
        al = f"{r['alpha']:+.4%}" if r['alpha'] is not None else 'N/A'
        print(f"{sym:<10} {r['spike_independent']:<12} {sn:<25} {nsn:<25} {al:<12}")

    # 落盘
    out = OUT_DIR / 'tg_revalidation_2026-07-18.json'
    payload = {
        'generated_at': datetime.utcnow().isoformat(),
        'config': {
            'interval': INTERVAL,
            'spike_z_threshold': SPIKE_Z_THRESHOLD,
            'spike_window': SPIKE_WINDOW,
            'spike_min_periods': SPIKE_MIN_PERIODS,
            'dedup_gap_bars': DEDUP_GAP_BARS,
            'hold_bars': HOLD_BARS,
            'mapping': 'B (asterisk maps to BTC+ETH+SOL)',
        },
        'signal_counts': {
            'total_real': len(signals),
            'BTC': int(signals['mapped_symbols'].apply(lambda lst: 'BTC' in lst).sum()),
            'ETH': int(signals['mapped_symbols'].apply(lambda lst: 'ETH' in lst).sum()),
            'SOL': int(signals['mapped_symbols'].apply(lambda lst: 'SOL' in lst).sum()),
        },
        'results': {sym: {k: v for k, v in r.items() if k not in ('spike_returns', 'nonspike_returns')}
                    for sym, r in results.items()},
    }
    out.write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')
    print(f'\n结果已保存: {out}')


if __name__ == '__main__':
    main()

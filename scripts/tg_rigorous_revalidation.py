"""
TG 因子严谨重测 (2026-08-13)
============================

对 2026-07-18 复验口径做三处升级，回答三个问题:

Q1. 扣成本后 alpha 还在吗?
    原复验的 spike@96h 净收益是 close-to-close 毛收益，没算 taker fee / funding / 滑点。
    本脚本加真实成本模型，区分 spike 交易(滑点更高)与随机基线交易。

Q2. LLM 的 direction 到底有没有信息量?
    原复验对每个 deduped spike 一律做空(方向被丢弃)。本脚本按 spike 窗口内
    衰减加权净方向把 spike 分成 bullish / bearish / neutral:
      - bullish  spike → 测【做多】前向收益
      - bearish  spike → 测【做空】前向收益
    若 direction 有信息: bullish 做多 > 基线做多, bearish 做空 > 基线做空。
    若 direction 是噪音: bullish spike 做空照样赢(纯均值回归), 做多则输。

Q3. 14 个独立 spike 的 alpha 在统计上显著吗?
    用 permutation 检验(N=1000 次随机非重叠采样)构建 null 分布, 输出 p-value,
    替代原复验"固定 seed 42 采样 200 个"的做法。

成本模型 (Binance USDT-M 永续, 保守偏贵):
  taker fee           0.05% / 边  (0.0005)
  滑点(基线)           0.03% / 边  (0.0003)
  spike 额外滑点       0.03% / 边  (spike bar 波动大)
  funding             0.01% / 8h  (96h 持有 = 12 期, 空单付正 funding)
  基线 round-trip: 2*(0.0005+0.0003)+0.0012 = 0.28%
  spike round-trip: 2*(0.0005+0.0003+0.0003)+0.0012 = 0.34%

用法:
  python scripts/tg_rigorous_revalidation.py [--nperm 1000] [--hold 24] [--klines PATH]
  (默认读 services/local-api/data/klines.db + internal-archive;
   --klines 可指向覆盖信号尾部更全的 klines.db, 如 0718celue/selene-master1 版)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
KLINES_DB = REPO / 'services' / 'local-api' / 'data' / 'klines.db'
TG_DB = REPO / '实验' / 'tg_factors.db'
OUT_DIR = REPO / '实验'
OUT_DIR.mkdir(exist_ok=True)

SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
SYM_KEY = {'BTC/USDT': 'BTC', 'ETH/USDT': 'ETH', 'SOL/USDT': 'SOL'}
INTERVAL = '4h'

# --- spike 检测口径 (与 2026-07-18 完全一致) ---
HALFLIFE_DEFAULT_MIN = 360
NOISE_PREFIX = 'noise:'
SPIKE_Z_THRESHOLD = 2.0
SPIKE_WINDOW = 20
SPIKE_MIN_PERIODS = 20
DEDUP_GAP_BARS = 24      # 96h
DECAY_WINDOW_MS = 24 * 3600 * 1000

# --- 成本模型 (可 CLI 覆盖) ---
TAKER_FEE_PER_SIDE = 0.0005
SLIPPAGE_PER_SIDE = 0.0003
SPIKE_SLIPPAGE_EXTRA_PER_SIDE = 0.0003
FUNDING_PER_8H = 0.0001
FUNDING_INTERVALS_96H = 12

# 方向分叉阈值: 净方向 decay 加权和 |net_dir| < DIR_EPS 视为 neutral
DIR_EPS = 0.1


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
    return df.set_index('ts').sort_index()


def load_tg_signals() -> pd.DataFrame:
    conn = sqlite3.connect(str(TG_DB))
    df = pd.read_sql_query(
        "SELECT msg_id, ts, symbols, direction, magnitude, urgency, confidence, "
        "halflife_min, event_type, reasoning "
        "FROM tg_factors WHERE reasoning NOT LIKE ?",
        conn, params=(f'{NOISE_PREFIX}%',)
    )
    conn.close()
    df['ts'] = pd.to_datetime(df['ts'])
    df['symbols_list'] = df['symbols'].apply(lambda s: json.loads(s) if s else [])
    return df.sort_values('ts').reset_index(drop=True)


def map_signal_to_symbols(symbols_list: list[str]) -> list[str]:
    """B 口径: '*' → 三币种; 否则显式短名."""
    if '*' in symbols_list:
        return ['BTC', 'ETH', 'SOL']
    return [s for s in symbols_list if s in ('BTC', 'ETH', 'SOL')]


def attach_vol_trigger(klines: pd.DataFrame, signals: pd.DataFrame, sym_short: str) -> pd.DataFrame:
    """vol_trigger 衰减聚合 + net_direction (decay 加权 direction 和)."""
    klines = klines.copy()
    klines['vol_trigger'] = 0.0
    klines['net_dir'] = 0.0

    sigs = signals[signals['mapped_symbols'].apply(lambda lst: sym_short in lst)]
    if sigs.empty:
        return klines

    bar_ts_ms = klines['timestamp'].astype('int64').values
    sig_ts_ms = (sigs['ts'].astype('int64') // 10**6).values
    sig_hl = sigs['halflife_min'].fillna(HALFLIFE_DEFAULT_MIN).astype(float).values
    sig_val = (sigs['urgency'] * sigs['magnitude']).values
    sig_dir = sigs['direction'].astype(float).values

    vt = np.zeros(len(klines))
    nd = np.zeros(len(klines))
    for i, bt in enumerate(bar_ts_ms):
        mask = (sig_ts_ms >= bt - DECAY_WINDOW_MS) & (sig_ts_ms <= bt)
        if not mask.any():
            continue
        dt_min = (bt - sig_ts_ms[mask]) / 60_000.0
        decay = np.exp(-dt_min / sig_hl[mask])
        vt[i] = (sig_val[mask] * decay).sum()
        nd[i] = (sig_dir[mask] * sig_val[mask] * decay).sum()

    klines['vol_trigger'] = vt
    klines['net_dir'] = nd
    return klines


def compute_spike(klines: pd.DataFrame) -> pd.DataFrame:
    """rolling(20) z-score, shift(1) 零 lookahead."""
    klines = klines.copy()
    mean = klines['vol_trigger'].rolling(SPIKE_WINDOW, min_periods=SPIKE_MIN_PERIODS).mean()
    std = klines['vol_trigger'].rolling(SPIKE_WINDOW, min_periods=SPIKE_MIN_PERIODS).std()
    z = (klines['vol_trigger'] - mean) / std.replace(0, np.nan)
    klines['spike_z'] = z.shift(1)
    klines['spike'] = (klines['spike_z'] > SPIKE_Z_THRESHOLD).astype(int)
    return klines


def cluster_dedup_spikes(spike_ts: list[pd.Timestamp], gap_bars: int = DEDUP_GAP_BARS) -> list[pd.Timestamp]:
    if not spike_ts:
        return []
    sorted_ts = sorted(spike_ts)
    kept = [sorted_ts[0]]
    gap = gap_bars * 4 * 3600
    for ts in sorted_ts[1:]:
        if (ts - kept[-1]).total_seconds() >= gap:
            kept.append(ts)
    return kept


def _pos_of(klines: pd.DataFrame, ts: pd.Timestamp) -> int | None:
    try:
        loc = klines.index.get_loc(ts)
        if isinstance(loc, slice):
            return loc.start
        if isinstance(loc, (list, np.ndarray)):
            return int(loc[0]) if len(loc) else None
        return int(loc)
    except KeyError:
        return None


def fwd_return(klines: pd.DataFrame, idx: int, hold: int, side: str) -> float | None:
    """bar idx close 入场, hold 个 bar 后 close 出场. side=long/short."""
    exit_idx = idx + hold
    if exit_idx >= len(klines):
        return None
    entry = klines.iloc[idx]['close']
    exit_c = klines.iloc[exit_idx]['close']
    ret = (exit_c - entry) / entry
    return ret if side == 'long' else -ret


# --- 成本 ---

def make_costs(hold: int) -> dict:
    fund_intervals = max(1, round(hold * 4 / 8))  # hold bars * 4h / 8h per funding
    baseline_rt = 2 * (TAKER_FEE_PER_SIDE + SLIPPAGE_PER_SIDE) + FUNDING_PER_8H * fund_intervals
    spike_rt = 2 * (TAKER_FEE_PER_SIDE + SLIPPAGE_PER_SIDE + SPIKE_SLIPPAGE_EXTRA_PER_SIDE) \
        + FUNDING_PER_8H * fund_intervals
    return {
        'funding_intervals': fund_intervals,
        'baseline_rt': baseline_rt,
        'spike_rt': spike_rt,
        'differential_rt': spike_rt - baseline_rt,
    }


def permutation_test(
    returns: np.ndarray,       # 每个有效入场位的收益 (长度 n_start)
    k: int,                    # 观测到的交易数
    obs_mean: float,
    rng: np.random.Generator,
    nperm: int,
) -> dict:
    """随机采样 k 个入场位的均值分布 → 观测均值的经验 p-value.

    null = "k 个随机时机入场, 同方向前向收益的均值" 分布。
    用无放回随机子集(无间距约束): spike 很稀疏(k≈10 / 1500 bar),
    子集内重叠前向窗口的影响可忽略, 且无偏 (null 均值 ≈ 全样本基线均值)。
    """
    n_start = len(returns)
    if k <= 0 or n_start <= 0:
        return {'nperm': 0, 'p_ge': None, 'null_mean': None, 'null_std': None}
    if k > n_start:
        k = n_start
    nulls = np.empty(nperm)
    for i in range(nperm):
        idxs = rng.choice(n_start, size=k, replace=False)
        nulls[i] = returns[idxs].mean()
    p_ge = (float((nulls >= obs_mean).mean()) * nperm + 1) / (nperm + 1)
    return {
        'nperm': nperm,
        'p_ge': p_ge,
        'null_mean': float(nulls.mean()),
        'null_std': float(nulls.std()),
        'null_p5': float(np.percentile(nulls, 5)),
        'null_p95': float(np.percentile(nulls, 95)),
    }


def run_symbol(sym: str, signals: pd.DataFrame, hold: int, nperm: int, rng: np.random.Generator) -> dict:
    sym_short = SYM_KEY[sym]
    klines = load_klines(sym)
    klines = attach_vol_trigger(klines, signals, sym_short)
    klines = compute_spike(klines)
    klines = klines[klines.index >= pd.Timestamp('2025-11-01', tz='UTC')]

    # 每个有效入场位的前向收益 (全量预计算, 供 permutation 用)
    n = len(klines)
    n_start = n - hold
    short_ret = np.full(n, np.nan)
    long_ret = np.full(n, np.nan)
    for i in range(n_start):
        short_ret[i] = fwd_return(klines, i, hold, 'short')
        long_ret[i] = fwd_return(klines, i, hold, 'long')

    valid_start = np.where(~np.isnan(short_ret))[0]

    # 市场 regime: 窗口内 buy-and-hold
    if n_start > 1:
        b_h = (klines.iloc[n_start - 1]['close'] - klines.iloc[0]['close']) / klines.iloc[0]['close']
    else:
        b_h = float('nan')

    spike_bars = klines[klines['spike'] == 1]
    dedup_ts = cluster_dedup_spikes(list(spike_bars.index))

    costs = make_costs(hold)
    base_cost = costs['baseline_rt']
    spike_cost = costs['spike_rt']

    # ---- Q1: always-short 毛/净 alpha + permutation ----
    spike_idx = []
    for ts in dedup_ts:
        p = _pos_of(klines, ts)
        if p is not None and p < n_start:
            spike_idx.append(p)
    k_spike = len(spike_idx)
    obs_short_gross = float(short_ret[spike_idx].mean()) if k_spike else float('nan')
    obs_short_net = obs_short_gross - spike_cost
    perm_short = permutation_test(short_ret[valid_start], k_spike, obs_short_gross, rng, nperm)

    # ---- Q2: 方向分叉 ----
    dir_info = {'bull': {'n': 0, 'long_gross': None, 'long_net': None, 'p_long': None,
                         'short_gross': None, 'null_mean_long': None},
                'bear': {'n': 0, 'short_gross': None, 'short_net': None, 'p_short': None,
                         'long_gross': None, 'null_mean_short': None},
                'neutral': 0}
    bull_idx, bear_idx = [], []
    for p in spike_idx:
        nd = klines.iloc[p]['net_dir']
        if nd > DIR_EPS:
            bull_idx.append(p)
        elif nd < -DIR_EPS:
            bear_idx.append(p)
        else:
            dir_info['neutral'] += 1

    if bull_idx:
        obs_long_g = float(long_ret[bull_idx].mean())
        dir_info['bull'] = {
            'n': len(bull_idx),
            'long_gross': obs_long_g,
            'long_net': obs_long_g - spike_cost,
            'short_gross': float(short_ret[bull_idx].mean()),
            'null_mean_long': float(long_ret[valid_start].mean()),
            'p_long': permutation_test(long_ret[valid_start], len(bull_idx), obs_long_g, rng, nperm)['p_ge'],
        }
    if bear_idx:
        obs_short_g = float(short_ret[bear_idx].mean())
        dir_info['bear'] = {
            'n': len(bear_idx),
            'short_gross': obs_short_g,
            'short_net': obs_short_g - spike_cost,
            'long_gross': float(long_ret[bear_idx].mean()),
            'null_mean_short': float(short_ret[valid_start].mean()),
            'p_short': permutation_test(short_ret[valid_start], len(bear_idx), obs_short_g, rng, nperm)['p_ge'],
        }

    # 方向对齐综合: (bull 做多 + bear 做空) 合并
    aligned = long_ret[bull_idx].tolist() + short_ret[bear_idx].tolist() if (bull_idx or bear_idx) else []
    aligned_net = [r - spike_cost for r in aligned]
    aligned_gross_mean = float(np.mean(aligned)) if aligned else None
    aligned_net_mean = float(np.mean(aligned_net)) if aligned else None

    return {
        'symbol': sym,
        'bars': n,
        'start_bars': n_start,
        'buy_hold': b_h,
        'spike_total': int(spike_bars.shape[0]),
        'spike_independent': k_spike,
        'costs': costs,
        'always_short': {
            'gross_mean': obs_short_gross,
            'net_mean': obs_short_net,
            'baseline_gross_mean': float(short_ret[valid_start].mean()),
            'baseline_net_mean': float(short_ret[valid_start].mean()) - base_cost,
            'alpha_gross': obs_short_gross - float(short_ret[valid_start].mean()),
            'alpha_net': (obs_short_gross - spike_cost) - (float(short_ret[valid_start].mean()) - base_cost),
            'perm': perm_short,
            'spike_idx': spike_idx,
        },
        'direction_split': dir_info,
        'aligned_direction': {
            'n': len(aligned),
            'gross_mean': aligned_gross_mean,
            'net_mean': aligned_net_mean,
            'baseline_gross_mean': (float(long_ret[valid_start].mean()) + float(short_ret[valid_start].mean())) / 2,
        },
        'event_counts': {
            'vol_trigger_mean': float(klines['vol_trigger'].mean()),
            'bars_with_signal': int((klines['vol_trigger'] > 0).sum()),
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nperm', type=int, default=1000)
    ap.add_argument('--hold', type=int, default=24)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--klines', type=str, default=None,
                    help='覆盖 klines.db 路径 (默认 services/local-api/data/klines.db)')
    args = ap.parse_args()
    if args.klines:
        globals()['KLINES_DB'] = Path(args.klines)
    rng = np.random.default_rng(args.seed)

    print('=' * 72)
    print('TG 因子严谨重测 (2026-08-13) — 成本 + 方向分叉 + permutation')
    print('=' * 72)
    print(f'klines: {KLINES_DB}')
    print(f'tg_factors: {TG_DB}')
    print(f'hold={args.hold} bars, nperm={args.nperm}, seed={args.seed}')
    print(f'成本: taker={TAKER_FEE_PER_SIDE:.4%}/边, 滑点={SLIPPAGE_PER_SIDE:.4%}/边, '
          f'spike额外滑点={SPIKE_SLIPPAGE_EXTRA_PER_SIDE:.4%}/边, funding={FUNDING_PER_8H:.4%}/8h')
    print()

    signals = load_tg_signals()
    signals['mapped_symbols'] = signals['symbols_list'].apply(map_signal_to_symbols)
    print(f'真信号: {len(signals)} 条')
    print()

    results = {}
    for sym in SYMBOLS:
        r = run_symbol(sym, signals, args.hold, args.nperm, rng)
        results[sym] = r
        print(f'--- {sym} ---')
        print(f"  窗口 buy&hold: {r['buy_hold']:+.2%}   bars={r['bars']}  独立spike={r['spike_independent']}")
        al = r['always_short']
        print(f"  [Q1 无脑做空]  spike毛={al['gross_mean']:+.4%} 净={al['net_mean']:+.4%} | "
              f"基线毛={al['baseline_gross_mean']:+.4%} 净={al['baseline_net_mean']:+.4%}")
        print(f"       alpha毛={al['alpha_gross']:+.4%} 净={al['alpha_net']:+.4%} "
              f"perm_p={al['perm']['p_ge']:.3f} (null {al['perm']['null_mean']:+.4%}±{al['perm']['null_std']:.4%})")
        d = r['direction_split']
        if d['bull']['n']:
            b = d['bull']
            print(f"  [Q2 bullish] n={b['n']}  做多毛={b['long_gross']:+.4%} 净={b['long_net']:+.4%} "
                  f"(基线做多 {b['null_mean_long']:+.4%}, p={b['p_long']:.3f})  同bar做空={b['short_gross']:+.4%}")
        if d['bear']['n']:
            b = d['bear']
            print(f"  [Q2 bearish] n={b['n']}  做空毛={b['short_gross']:+.4%} 净={b['short_net']:+.4%} "
                  f"(基线做空 {b['null_mean_short']:+.4%}, p={b['p_short']:.3f})  同bar做多={b['long_gross']:+.4%}")
        print(f"  [Q2 neutral] n={d['neutral']}")
        ad = r['aligned_direction']
        if ad['gross_mean'] is not None:
            print(f"  [Q2 方向对齐] n={ad['n']} 毛={ad['gross_mean']:+.4%} 净={ad['net_mean']:+.4%} "
                  f"(基线 {ad['baseline_gross_mean']:+.4%})")
        print()

    print('=' * 72)
    print('汇总')
    print('=' * 72)
    print(f"{'币种':<10}{'B&H':>9}{'spike_n':>8}{'α毛':>10}{'α净':>10}{'perm_p':>8}  {'bull做多净':>10}{'bear做空净':>10}")
    for sym in SYMBOLS:
        r = results[sym]
        al = r['always_short']
        d = r['direction_split']
        bull_net = f"{d['bull']['long_net']:+.4%}" if d['bull']['n'] else '  --'
        bear_net = f"{d['bear']['short_net']:+.4%}" if d['bear']['n'] else '  --'
        print(f"{sym:<10}{r['buy_hold']:>+8.2%}{r['spike_independent']:>8}"
              f"{al['alpha_gross']:>+9.2%}{al['alpha_net']:>+9.2%}"
              f"{al['perm']['p_ge']:>8.3f}  {bull_net:>10}{bear_net:>10}")

    out = OUT_DIR / f'tg_rigorous_revalidation_2026-08-13_hold{args.hold}.json'
    payload = {
        'generated_at': datetime.utcnow().isoformat(),
        'config': {
            'interval': INTERVAL, 'hold_bars': args.hold, 'nperm': args.nperm, 'seed': args.seed,
            'spike_z_threshold': SPIKE_Z_THRESHOLD, 'spike_window': SPIKE_WINDOW,
            'dedup_gap_bars': DEDUP_GAP_BARS,
            'costs': make_costs(args.hold),
        },
        'results': results,
    }
    # 去掉 numpy 标量
    payload['results'] = json.loads(json.dumps(payload['results'], default=float))
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=float), encoding='utf-8')
    print(f'\n结果已保存: {out}')


if __name__ == '__main__':
    main()

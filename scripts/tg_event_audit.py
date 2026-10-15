"""
TG 事件类型审计 (2026-08-13)
============================

在严谨重测 (scripts/tg_rigorous_revalidation.py) 之后，对 TG 消息按 event_type
分层检验两个市场性质，回答"消息到底有没有可交易价值":

Q4. 量能: 消息所在 bar 的成交量是否显著放大?
     用绝对量能百分位(全窗口 rank)，消除 rolling 窗口分母被前置放量抬高的干扰。
     对比: 该 bar 百分位 vs 随机抽样分布 (permutation p)。

Q5. 波动: 消息所在 bar 的振幅是否显著放大? 事件后 24h 累计绝对波动呢?
     当 bar 振幅 = (high-low)/open; 后 24h 波动 = 事件后 4 bar |close/prev-1| 之和。
     两者都对比全窗口基线 + permutation p。

事件对齐: 每条消息 floor 到所属 4h bar (消息时间 <= bar open)。同 bar 多消息
按 bar 去重，避免同一事件被多次加权。B 口径 symbols 映射 (同严谨重测)。

用法:
  python scripts/tg_event_audit.py [--klines PATH] [--nperm 500] [--seed 42]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
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
EVENT_TYPES = ['macro', 'market', 'regulatory', 'listing', 'whale', 'security', 'partnership']


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
        "SELECT ts, symbols, event_type, direction FROM tg_factors "
        "WHERE reasoning NOT LIKE 'noise:%'",
        conn
    )
    conn.close()
    df['ts'] = pd.to_datetime(df['ts'])
    df['symbols_list'] = df['symbols'].apply(lambda s: json.loads(s) if s else [])
    return df


def map_signal_to_symbols(symbols_list: list[str]) -> list[str]:
    if '*' in symbols_list:
        return ['BTC', 'ETH', 'SOL']
    return [s for s in symbols_list if s in ('BTC', 'ETH', 'SOL')]


def analyze_symbol(sym: str, signals: pd.DataFrame, nperm: int, rng: np.random.Generator) -> dict:
    sym_short = SYM_KEY[sym]
    k = load_klines(sym)
    k = k[k.index >= pd.Timestamp('2025-11-01', tz='UTC')]  # 与信号同期

    o = k['open'].values
    h = k['high'].values
    l = k['low'].values
    cl = k['close'].values
    amp = (h - l) / o                       # 当 bar 振幅
    pct = k['volume'].rank(pct=True) * 100  # 绝对量能百分位
    ctoc = np.abs(cl[1:] / cl[:-1] - 1)     # ctoc[i] = bar i -> i+1 绝对收益
    n = len(k)

    opens = k.index.values.astype('int64') // 10**6
    ss = signals[signals['mapped'].apply(lambda lst: sym_short in lst)]
    sig_ms = (ss['ts'].astype('int64') // 10**6).values
    bar_idx = np.searchsorted(opens, sig_ms, side='right') - 1

    out = {}
    for et in EVENT_TYPES + ['macro+regulatory']:
        if et == 'macro+regulatory':
            mask = ss['event_type'].isin(['macro', 'regulatory']).values
        else:
            mask = (ss['event_type'] == et).values
        idx = bar_idx[mask]
        idx = idx[(idx >= 0) & (idx < n - 4)]
        ev = sorted(set(idx.tolist()))  # 按 bar 去重
        if len(ev) < 4:
            out[et] = None
            continue

        # Q4 量能: bar0 绝对量能百分位
        b0_pct = np.array([pct.iloc[i] for i in ev])
        obs_pct = float(b0_pct.mean())
        nulls_pct = np.array([
            float(np.mean(pct.iloc[rng.choice(n, size=len(ev), replace=False)]))
            for _ in range(nperm)
        ])
        pv_pct = (float((nulls_pct >= obs_pct).mean()) * nperm + 1) / (nperm + 1)

        # Q5 波动: 当 bar 振幅 + 后 24h 累计绝对波动
        def cum4(i: int) -> float:
            return ctoc[i + 1:i + 5].sum()

        obs_amp = float(np.mean(amp[ev]))
        base_amp = float(np.mean(amp))
        obs_cum = float(np.mean([cum4(i) for i in ev]))
        nulls_cum = np.array([
            np.mean([cum4(j) for j in rng.choice(n - 4, size=len(ev), replace=False)])
            for _ in range(nperm)
        ])
        pv_cum = (float((nulls_cum >= obs_cum).mean()) * nperm + 1) / (nperm + 1)

        # 当 bar 振幅曲线 off=-2..+8
        curve = np.full((len(ev), 11), np.nan)
        for r, i in enumerate(ev):
            for j, off in enumerate(range(-2, 9)):
                q = i + off
                if 0 <= q < n:
                    curve[r, j] = amp[q]

        out[et] = {
            'n_bars': len(ev),
            'volume_pct_mean': obs_pct,
            'volume_pct_med': float(np.median(b0_pct)),
            'volume_pct_p': pv_pct,
            'amp_bar0': obs_amp,
            'amp_baseline': base_amp,
            'amp_ratio': obs_amp / base_amp,
            'cum24h': obs_cum,
            'cum24h_baseline': float(nulls_cum.mean()),
            'cum24h_p': pv_cum,
            'amp_curve': curve.mean(axis=0).tolist(),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--klines', type=str, default=None)
    ap.add_argument('--nperm', type=int, default=500)
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()
    if args.klines:
        globals()['KLINES_DB'] = Path(args.klines)
    rng = np.random.default_rng(args.seed)

    signals = load_tg_signals()
    signals['mapped'] = signals['symbols_list'].apply(map_signal_to_symbols)

    print(f"klines: {KLINES_DB}")
    print(f"真信号: {len(signals)} 条")
    print(f"{'币种/类型':<20}{'n':>4}{'量能%':>7}{'量p':>7}{'当bar振幅':>10}{'基线':>9}{'倍':>6}{'后24h':>8}{'基':>7}{'p':>7}")
    results = {}
    for sym in SYMBOLS:
        r = analyze_symbol(sym, signals, args.nperm, rng)
        results[sym] = r
        for et in EVENT_TYPES + ['macro+regulatory']:
            q = r[et]
            if q is None:
                print(f"{sym+'/'+et:<20}  样本<4, 跳过")
                continue
            print(f"{sym+'/'+et:<20}{q['n_bars']:>4}{q['volume_pct_mean']:>6.0f}%{q['volume_pct_p']:>7.3f}"
                  f"{q['amp_bar0']:>10.3%}{q['amp_baseline']:>9.3%}{q['amp_ratio']:>5.2f}x"
                  f"{q['cum24h']:>8.3%}{q['cum24h_baseline']:>7.3%}{q['cum24h_p']:>7.3f}")
        print()

    out = OUT_DIR / 'tg_event_audit_2026-08-13.json'
    out.write_text(json.dumps({
        'config': {'interval': INTERVAL, 'nperm': args.nperm, 'seed': args.seed,
                   'event_types': EVENT_TYPES},
        'results': json.loads(json.dumps(results, default=float)),
    }, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"结果已保存: {out}")


if __name__ == '__main__':
    main()

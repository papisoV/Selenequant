"""Param scan v2: 重叠信号占用约束 — 模拟真实仓位.

每个信号开仓后 hold 期间, 同币种不能再开仓 (跟 pure_strategy 一致).
这给出与 pure_strategy 可直接比较的 fair comparison.

3 个维度:
1. DEDUP_GAP_BARS: 24 (默认) vs 12 vs 0 (关 dedup)
2. BTC hold_bars: 36 vs 24
3. BTC TP: 5% vs 0%
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "strategy-engine"))
sys.path.insert(0, str(REPO / "packages" / "alpha-core"))
sys.path.insert(0, str(REPO / "packages" / "shared-types"))

import sqlite3
import numpy as np
import pandas as pd

KLINES_DB = REPO / "services" / "local-api" / "data" / "klines.db"
TG_DB = REPO / "实验" / "tg_factors.db"

INITIAL_CASH = 10_000.0
COMMISSION = 0.001
SLIPPAGE_BPS = 1.0


def load_klines(symbol: str, interval: str = "4h") -> pd.DataFrame:
    conn = sqlite3.connect(str(KLINES_DB))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE exchange='binance' AND market_type='futures' AND symbol=? AND interval=? "
        "ORDER BY timestamp",
        conn, params=(symbol, interval))
    conn.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df.set_index('ts').sort_index()


def backtest_symbol_no_overlap(symbol, signals, klines):
    """No-overlap backtest: while in position, skip new signals."""
    klines = klines[~klines.index.duplicated(keep='first')]
    cash = INITIAL_CASH / 3
    trades = []
    in_position_until = pd.Timestamp(0, tz='UTC')  # exit timestamp

    sorted_sigs = sorted(signals, key=lambda s: s['timestamp'])
    for sig in sorted_sigs:
        entry_ts = pd.Timestamp(sig['timestamp'], unit='ms', tz='UTC')
        if entry_ts < in_position_until:
            continue  # skip overlapping signal
        try:
            idx = klines.index.get_loc(entry_ts)
        except KeyError:
            continue
        if idx + sig['hold_bars'] >= len(klines):
            continue

        entry_price = klines.iloc[idx]['close']
        if sig['side'] == 'sell':
            entry_price *= (1 - SLIPPAGE_BPS / 10000)
        else:
            entry_price *= (1 + SLIPPAGE_BPS / 10000)

        pos_mult = sig['strength']
        use_cash = cash * 0.5 * pos_mult
        if use_cash < 50:
            continue

        if sig['side'] == 'sell':
            qty = use_cash / entry_price
            cash += use_cash - use_cash * COMMISSION
        else:
            qty = use_cash / entry_price
            cash -= use_cash + use_cash * COMMISSION

        exit_idx = idx + sig['hold_bars']
        exit_ts = klines.index[exit_idx]
        exit_price = None
        exit_reason = None
        tp = sig.get('tp_pct', 0)
        sl = sig.get('sl_pct', 0)
        actual_exit_idx = exit_idx

        for i in range(idx + 1, min(exit_idx + 1, len(klines))):
            cur = klines.iloc[i]['close']
            if sig['side'] == 'sell':
                cur_ret = (entry_price - cur) / entry_price
                if tp > 0 and cur_ret >= tp:
                    exit_price = entry_price * (1 - tp)
                    actual_exit_idx = i
                    exit_reason = 'tp'
                    break
                if sl > 0 and cur_ret <= -sl:
                    exit_price = entry_price * (1 + sl)
                    actual_exit_idx = i
                    exit_reason = 'sl'
                    break
            else:
                cur_ret = (cur - entry_price) / entry_price
                if tp > 0 and cur_ret >= tp:
                    exit_price = entry_price * (1 + tp)
                    actual_exit_idx = i
                    exit_reason = 'tp'
                    break
                if sl > 0 and cur_ret <= -sl:
                    exit_price = entry_price * (1 - sl)
                    actual_exit_idx = i
                    exit_reason = 'sl'
                    break

        if exit_price is None:
            exit_price = klines.iloc[exit_idx]['close']
            if sig['side'] == 'sell':
                exit_price *= (1 + SLIPPAGE_BPS / 10000)
            else:
                exit_price *= (1 - SLIPPAGE_BPS / 10000)
            exit_reason = 'hold_end'

        in_position_until = klines.index[actual_exit_idx]

        if sig['side'] == 'sell':
            cost = exit_price * qty
            cash -= cost + cost * COMMISSION
            pnl_pct = (entry_price - exit_price) / entry_price - 2 * COMMISSION
        else:
            proceeds = exit_price * qty
            cash += proceeds - proceeds * COMMISSION
            pnl_pct = (exit_price - entry_price) / entry_price - 2 * COMMISSION

        trades.append({'pnl_pct': pnl_pct})

    if trades:
        pnls = [t['pnl_pct'] for t in trades]
        return {
            'n_trades': len(trades),
            'win_rate': float((np.array(pnls) > 0).mean()),
            'total_return': (cash - INITIAL_CASH / 3) / (INITIAL_CASH / 3),
        }
    return {'n_trades': 0, 'win_rate': 0.0, 'total_return': 0.0}


def run_portfolio(dedup_gap, btc_hold, btc_tp):
    import selene_space_alpha.factors.tg_spike_source as tgs
    tgs.DEDUP_GAP_BARS = dedup_gap
    from selene_space_alpha.factors.tg_spike_source import (
        TGSpikeSource, compute_other_symbol_spike_sets,
    )

    klines_by_symbol = {sym: load_klines(sym) for sym in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']}
    for sym in klines_by_symbol:
        klines_by_symbol[sym] = klines_by_symbol[sym][
            klines_by_symbol[sym].index >= pd.Timestamp('2025-11-01', tz='UTC')
        ]

    spike_sets = compute_other_symbol_spike_sets(str(TG_DB), klines_by_symbol, '4h')

    combined_ret = 0.0
    combined_n = 0
    combined_wins = 0
    per_symbol = {}

    for sym in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']:
        others = [spike_sets[s] for s in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT'] if s != sym]
        kwargs = dict(
            tg_db_path=str(TG_DB), symbol=sym, interval='4h',
            other_symbol_spike_sets=others,
        )
        if sym == 'BTC/USDT':
            kwargs['hold_bars'] = btc_hold
            kwargs['tp_pct'] = btc_tp
        src = TGSpikeSource(**kwargs)
        signals = src.build_signal_dicts(klines_by_symbol[sym])
        r = backtest_symbol_no_overlap(sym, signals, klines_by_symbol[sym])
        per_symbol[sym] = r
        combined_ret += r['total_return'] / 3
        combined_n += r['n_trades']
        combined_wins += int(r['win_rate'] * r['n_trades'])

    tgs.DEDUP_GAP_BARS = 24

    return {
        'dedup_gap': dedup_gap, 'btc_hold': btc_hold, 'btc_tp': btc_tp,
        'portfolio_return': combined_ret,
        'n_trades': combined_n,
        'win_rate': combined_wins / combined_n if combined_n else 0,
        'per_symbol': per_symbol,
    }


def main():
    print('=' * 95)
    print('Param scan v2 (no-overlap backtest, fair comparison vs pure_strategy)')
    print('=' * 95)
    print(f"{'gap':<6}{'btc_hold':<10}{'btc_tp':<10}{'port_ret':<12}{'n':<5}{'win':<8}{'BTC':<10}{'ETH':<10}{'SOL':<10}")
    print('-' * 95)

    configs = [
        (24, 36, 0.05),  # 工厂默认
        (24, 36, 0.0),   # 关 BTC TP
        (24, 24, 0.05),  # BTC hold 24
        (24, 24, 0.0),   # BTC hold 24 + 无 TP (最接近 pure_strategy BTC config)
        (12, 36, 0.05),  # gap=12
        (12, 24, 0.0),   # gap=12 + BTC hold 24 + 无 TP
        (12, 24, 0.05),  # gap=12 + hold 24 + TP 5%
        (0, 24, 0.0),    # 关 dedup (模拟 pure_strategy 完全相同)
    ]

    results = []
    for gap, hold, tp in configs:
        r = run_portfolio(gap, hold, tp)
        results.append(r)
        ps = r['per_symbol']
        print(f"{gap:<6}{hold:<10}{tp:<10.2f}{r['portfolio_return']:+.2%}      {r['n_trades']:<5}"
              f"{r['win_rate']:.1%}    {ps['BTC/USDT']['total_return']:+.2%}    "
              f"{ps['ETH/USDT']['total_return']:+.2%}    {ps['SOL/USDT']['total_return']:+.2%}")

    print()
    print('Validation (pure_strategy, hold=24, no dedup, BTC TP=5%, no-overlap): +12.72% / 41 / 65.9%')

    best = max(results, key=lambda x: x['portfolio_return'])
    print()
    print(f"Best: gap={best['dedup_gap']} btc_hold={best['btc_hold']} btc_tp={best['btc_tp']}")
    print(f"  port_ret={best['portfolio_return']:+.2%} n={best['n_trades']} win={best['win_rate']:.1%}")


if __name__ == '__main__':
    main()

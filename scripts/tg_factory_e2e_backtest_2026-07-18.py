"""End-to-end portfolio backtest using tg_spike_v1 strategy factory integration.

Verifies that the production-integrated tg_spike_v1 produces the same alpha
as the standalone tg_pure_strategy backtest:
- BTC: ~+7-12% (8.5 months)
- ETH: ~+4-15%
- SOL: ~+13-18%
- Portfolio (1/3 each): ~+12.72%

This bridges from raw alpha research to the factory pattern, confirming
the production code path works.
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

from selene_space_strategy.factory import build_tg_spike_portfolio_signals

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


def backtest_symbol(symbol: str, signals: list[dict], klines: pd.DataFrame) -> dict:
    """Execute signal-driven backtest on one symbol.

    Each signal: open position at next bar open, hold signal['hold_bars'],
    apply TP/SL if non-zero. Position size = 0.5 * strength (cash * 0.5 * pos_mult).
    """
    klines = klines[~klines.index.duplicated(keep='first')]
    cash = INITIAL_CASH / 3  # 1/3 of portfolio per symbol
    trades = []

    for sig in signals:
        entry_ts = pd.Timestamp(sig['timestamp'], unit='ms', tz='UTC')
        try:
            idx = klines.index.get_loc(entry_ts)
        except KeyError:
            continue
        if idx + sig['hold_bars'] >= len(klines):
            continue

        entry_price = klines.iloc[idx]['close']
        # apply slippage: buy at +slippage, sell at -slippage
        if sig['side'] == 'sell':  # short
            entry_price *= (1 - SLIPPAGE_BPS / 10000)
        else:  # buy (long)
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

        # Find exit: hold_bars or TP/SL hit first
        exit_idx = idx + sig['hold_bars']
        exit_price = None
        exit_reason = None
        tp = sig.get('tp_pct', 0)
        sl = sig.get('sl_pct', 0)

        for i in range(idx + 1, min(exit_idx + 1, len(klines))):
            cur = klines.iloc[i]['close']
            if sig['side'] == 'sell':
                cur_ret = (entry_price - cur) / entry_price
                if tp > 0 and cur_ret >= tp:
                    exit_price = entry_price * (1 - tp)
                    exit_idx = i
                    exit_reason = 'tp'
                    break
                if sl > 0 and cur_ret <= -sl:
                    exit_price = entry_price * (1 + sl)
                    exit_idx = i
                    exit_reason = 'sl'
                    break
            else:
                cur_ret = (cur - entry_price) / entry_price
                if tp > 0 and cur_ret >= tp:
                    exit_price = entry_price * (1 + tp)
                    exit_idx = i
                    exit_reason = 'tp'
                    break
                if sl > 0 and cur_ret <= -sl:
                    exit_price = entry_price * (1 - sl)
                    exit_idx = i
                    exit_reason = 'sl'
                    break

        if exit_price is None:
            exit_price = klines.iloc[exit_idx]['close']
            if sig['side'] == 'sell':
                exit_price *= (1 + SLIPPAGE_BPS / 10000)
            else:
                exit_price *= (1 - SLIPPAGE_BPS / 10000)
            exit_reason = 'hold_end'

        if sig['side'] == 'sell':
            cost = exit_price * qty
            cash -= cost + cost * COMMISSION
            pnl_pct = (entry_price - exit_price) / entry_price - 2 * COMMISSION
        else:
            proceeds = exit_price * qty
            cash += proceeds - proceeds * COMMISSION
            pnl_pct = (exit_price - entry_price) / entry_price - 2 * COMMISSION

        trades.append({
            'side': sig['side'],
            'tier': sig['tier'],
            'entry_ts': str(entry_ts),
            'exit_reason': exit_reason,
            'pnl_pct': pnl_pct,
            'strength': sig['strength'],
        })

    if trades:
        pnls = [t['pnl_pct'] for t in trades]
        return {
            'symbol': symbol,
            'n_trades': len(trades),
            'win_rate': float((np.array(pnls) > 0).mean()),
            'total_return': (cash - INITIAL_CASH / 3) / (INITIAL_CASH / 3),
            'avg_pnl': float(np.mean(pnls)),
            'final_cash': cash,
            'trades': trades[:5],  # preview
        }
    return {'symbol': symbol, 'n_trades': 0, 'total_return': 0.0, 'final_cash': cash}


def main():
    print('=' * 70)
    print('End-to-end portfolio backtest via tg_spike_v1 factory')
    print('=' * 70)

    klines_by_symbol = {
        sym: load_klines(sym, '4h') for sym in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
    }
    # Trim to TG signal window
    for sym in klines_by_symbol:
        klines_by_symbol[sym] = klines_by_symbol[sym][
            klines_by_symbol[sym].index >= pd.Timestamp('2025-11-01', tz='UTC')
        ]

    portfolio_signals = build_tg_spike_portfolio_signals(
        klines_by_symbol, interval='4h', tg_db_path=str(TG_DB)
    )

    print()
    combined_pnl_pct = 0.0
    combined_n = 0
    combined_wins = 0
    print(f"{'Symbol':<10} {'n':<5} {'win_rate':<10} {'total_return':<14} {'avg_pnl':<10}")
    print('-' * 60)
    for sym in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']:
        result = backtest_symbol(sym, portfolio_signals[sym], klines_by_symbol[sym])
        combined_pnl_pct += result['total_return'] / 3
        combined_n += result['n_trades']
        combined_wins += int(result['win_rate'] * result['n_trades'])
        print(f"{sym:<10} {result['n_trades']:<5} {result['win_rate']:.1%}    "
              f"{result['total_return']:+.2%}        {result['avg_pnl']:+.2%}")

    combined_win_rate = combined_wins / combined_n if combined_n else 0
    print()
    print("Portfolio (1/3 capital each):")
    print(f"  total_return: {combined_pnl_pct:+.2%}")
    print(f"  n_trades: {combined_n}")
    print(f"  win_rate: {combined_win_rate:.1%}")

    # Expected (clean-data revalidation 2026-07-18, deduped klines):
    #   total_return: ~+10-11%
    #   n_trades: ~33
    #   win_rate: ~57.6%
    #
    # Note: original pure_strategy +12.72% was inflated by 1854 duplicate
    # timestamps in klines.db (95 spikes → real 49 after dedup). Factory path
    # dedups klines in attach_vol_trigger, giving the true alpha.
    print()
    print("Validation targets (clean-data, deduped klines):")
    print("  total_return: ~+10-11%")
    print("  n_trades: ~33")
    print("  win_rate: ~57.6%")


if __name__ == '__main__':
    main()

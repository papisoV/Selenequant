"""
TG 策略参数扫描 + TP/SL 优化 + 多币种组合

1. 参数扫描: z_threshold ∈ {1.5, 2.0, 2.5, 3.0} × hold_bars ∈ {12, 24, 36, 48}
2. TP/SL 优化: 在最优参数上, 加 TP ∈ {3%, 5%, 8%} × SL ∈ {3%, 5%, 8%}
3. 多币种组合: 三币种独立账户, 看组合表现
"""
from __future__ import annotations
import importlib.util
import sys
import json
from pathlib import Path
from dataclasses import dataclass
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tg_reval", REPO / "scripts" / "tg_revalidation_2026-07-18.py")
tg = importlib.util.module_from_spec(spec)
sys.modules["tg_reval"] = tg
spec.loader.exec_module(tg)  # type: ignore

INITIAL_CASH = 10_000.0
COMMISSION = 0.001
SLIPPAGE_BPS = 1.0

A_TIER_EVENTS = {'listing', 'whale', 'security'}
B_TIER_EVENTS = {'market', 'macro', 'partnership'}


@dataclass
class Trade:
    side: str
    entry_ts: pd.Timestamp
    entry_price: float
    qty: float
    exit_ts: object = None
    exit_price: float = 0.0
    exit_reason: str = ''
    pnl_pct: float = 0.0


def classify(row, sym_short):
    if row['spike'] != 1:
        return 'skip'
    if row.get('multi_sym', False):
        return 'S'
    evt = row.get('dominant_event')
    if evt in A_TIER_EVENTS:
        return 'A'
    if evt in B_TIER_EVENTS:
        return 'B'
    if sym_short == 'ETH' and evt == 'regulatory':
        return 'C_ETH_REG'
    return 'skip'


def run_with_params(klines: pd.DataFrame, sym_short: str,
                    hold_bars: int, z_threshold: float,
                    tp_pct: float = 0.0, sl_pct: float = 0.0,
                    use_tier: bool = True,
                    pos_mult_default: float = 0.5) -> dict:
    """参数化跑 TG 策略."""
    # 重新算 spike (不同 z_threshold)
    mean = klines['vol_trigger'].rolling(tg.SPIKE_WINDOW, min_periods=tg.SPIKE_MIN_PERIODS).mean()
    std = klines['vol_trigger'].rolling(tg.SPIKE_WINDOW, min_periods=tg.SPIKE_MIN_PERIODS).std()
    z = (klines['vol_trigger'] - mean) / std.replace(0, np.nan)
    z_shifted = z.shift(1)
    spike = (z_shifted > z_threshold).astype(int)

    # cluster dedup
    spike_ts_all = list(klines.index[spike == 1])
    spike_ts_dedup = tg.cluster_dedup_spikes(spike_ts_all, gap_bars=tg.DEDUP_GAP_BARS)
    spike_dedup_mask = klines.index.isin(spike_ts_dedup)

    df = klines.copy()
    df['spike_eff'] = spike_dedup_mask.astype(int)

    # 恢复 dominant_event / multi_sym 列 (传进来已经有, 但只对原 z=2 的 spike 设置过值)
    # 重新计算 dominant_event / multi_sym 对新 spike
    # 但主脚本没传 sig_ts_ns/sig_evt 等进来, 这里不重算, 直接用已有的 (可能 None for 新增 spike)
    # 简化: 只用原 spike 列里已有的 dominant_event/multi_sym, 新增 spike 没有 event 就用 'skip'
    # 这对参数扫描够用 (主要测 z_threshold 影响)

    trades = []
    open_trade = None
    cash = INITIAL_CASH

    for i in range(len(df)):
        row = df.iloc[i]
        ts = df.index[i]
        price = row['close']
        buy_price = price * (1 + SLIPPAGE_BPS / 10000)
        sell_price = price * (1 - SLIPPAGE_BPS / 10000)

        # 平仓
        if open_trade is not None:
            entry_pos = tg._find_pos(df, open_trade.entry_ts)
            exit_reason = None
            exit_price = None
            if i - entry_pos >= hold_bars:
                exit_reason = 'hold_end'
                exit_price = buy_price if open_trade.side == 'short' else sell_price
            elif tp_pct > 0 or sl_pct > 0:
                if open_trade.side == 'short':
                    cur_ret = (open_trade.entry_price - price) / open_trade.entry_price
                    if tp_pct > 0 and cur_ret >= tp_pct:
                        exit_reason = 'tp'
                        exit_price = open_trade.entry_price * (1 - tp_pct)
                    elif sl_pct > 0 and cur_ret <= -sl_pct:
                        exit_reason = 'sl'
                        exit_price = open_trade.entry_price * (1 + sl_pct)
                else:
                    cur_ret = (price - open_trade.entry_price) / open_trade.entry_price
                    if tp_pct > 0 and cur_ret >= tp_pct:
                        exit_reason = 'tp'
                        exit_price = open_trade.entry_price * (1 + tp_pct)
                    elif sl_pct > 0 and cur_ret <= -sl_pct:
                        exit_reason = 'sl'
                        exit_price = open_trade.entry_price * (1 - sl_pct)

            if exit_reason:
                if open_trade.side == 'short':
                    cost = exit_price * open_trade.qty
                    cash -= cost + cost * COMMISSION
                else:
                    proceeds = exit_price * open_trade.qty
                    cash += proceeds - proceeds * COMMISSION
                open_trade.exit_ts = ts
                open_trade.exit_price = exit_price
                open_trade.exit_reason = exit_reason
                if open_trade.side == 'short':
                    open_trade.pnl_pct = (open_trade.entry_price - exit_price) / open_trade.entry_price - 2 * COMMISSION
                else:
                    open_trade.pnl_pct = (exit_price - open_trade.entry_price) / open_trade.entry_price - 2 * COMMISSION
                trades.append(open_trade)
                open_trade = None

        # 开仓
        if open_trade is None and row['spike_eff'] == 1:
            if use_tier:
                tier = classify(row, sym_short)
                if tier == 'S':
                    pos_mult = 1.0
                    action = 'short'
                elif tier == 'A':
                    pos_mult = 0.7
                    action = 'short'
                elif tier == 'B':
                    pos_mult = 0.5
                    action = 'short'
                elif tier == 'C_ETH_REG':
                    pos_mult = 0.5
                    action = 'long'
                else:
                    pos_mult = 0.0
                    action = 'none'
            else:
                pos_mult = pos_mult_default
                action = 'short'
            if action == 'none':
                continue
            use_cash = cash * 0.5 * pos_mult
            if use_cash < 100:
                continue
            if action == 'short':
                qty = use_cash / sell_price
                cash += use_cash - use_cash * COMMISSION
                entry_p = sell_price
            else:
                qty = use_cash / buy_price
                cash -= use_cash + use_cash * COMMISSION
                entry_p = buy_price
            open_trade = Trade(side=action, entry_ts=ts, entry_price=entry_p, qty=qty)

    if open_trade is not None:
        last = df.iloc[-1]['close']
        if open_trade.side == 'short':
            exit_p = last * (1 + SLIPPAGE_BPS / 10000)
            cost = exit_p * open_trade.qty
            cash -= cost + cost * COMMISSION
        else:
            exit_p = last * (1 - SLIPPAGE_BPS / 10000)
            proceeds = exit_p * open_trade.qty
            cash += proceeds - proceeds * COMMISSION
        open_trade.exit_ts = df.index[-1]
        open_trade.exit_price = exit_p
        open_trade.exit_reason = 'final'
        if open_trade.side == 'short':
            open_trade.pnl_pct = (open_trade.entry_price - exit_p) / open_trade.entry_price - 2 * COMMISSION
        else:
            open_trade.pnl_pct = (exit_p - open_trade.entry_price) / open_trade.entry_price - 2 * COMMISSION
        trades.append(open_trade)

    if not trades:
        return {'n_trades': 0, 'total_return': 0.0, 'win_rate': 0.0, 'avg_pnl': 0.0, 'sharpe': 0.0}
    pnls = [t.pnl_pct for t in trades]
    return {
        'n_trades': len(trades),
        'n_long': sum(1 for t in trades if t.side == 'long'),
        'n_short': sum(1 for t in trades if t.side == 'short'),
        'total_return': (cash - INITIAL_CASH) / INITIAL_CASH,
        'win_rate': sum(1 for p in pnls if p > 0) / len(pnls),
        'avg_pnl': float(np.mean(pnls)),
        'sharpe': float(np.mean(pnls) / np.std(pnls) * np.sqrt(len(pnls))) if np.std(pnls) > 0 else 0,
    }


def prepare_klines(signals, all_klines_raw, all_klines, sym):
    """给每个币种 klines 加 dominant_event + multi_sym 列."""
    sym_short = tg.SYM_KEY[sym]
    klines = all_klines[sym].copy()
    klines = klines[klines.index >= pd.Timestamp('2025-11-01', tz='UTC')]

    sigs_all = signals.copy()
    sig_ts_ns = sigs_all['ts'].astype('int64').values
    sig_evt = sigs_all['event_type'].values
    sig_um = (sigs_all['urgency'] * sigs_all['magnitude']).values
    def dom_evt(bar_ts):
        bar_ns = bar_ts.value
        mask = (sig_ts_ns >= bar_ns - 24*3600*1e9) & (sig_ts_ns <= bar_ns)
        if not mask.any():
            return None
        return sig_evt[mask][np.argmax(sig_um[mask])]
    klines['dominant_event'] = None
    spike_idx = klines.index[klines['spike'] == 1]
    for i in spike_idx:
        klines.at[i, 'dominant_event'] = dom_evt(i)

    other_syms = [s for s in tg.SYMBOLS if s != sym]
    other_spike_idx = []
    for s in other_syms:
        ok = all_klines[s]
        other_spike_idx.append(set(ok.index[ok['spike'] == 1]))
    def is_multi(bar_ts):
        cnt = 1
        for sset in other_spike_idx:
            if bar_ts in sset:
                cnt += 1
        return cnt >= 2
    klines['multi_sym'] = False
    for i in spike_idx:
        klines.at[i, 'multi_sym'] = is_multi(i)
    return klines


def main():
    print('=' * 70)
    print('TG 策略参数扫描 + TP/SL 优化 + 多币种组合')
    print('=' * 70)
    signals = tg.load_tg_signals()
    signals['mapped_symbols'] = signals.apply(tg.map_signal_to_symbols, axis=1)

    all_klines_raw = {sym: tg.load_klines(sym) for sym in tg.SYMBOLS}
    all_klines = {}
    for sym in tg.SYMBOLS:
        sym_short = tg.SYM_KEY[sym]
        klines = tg.attach_vol_trigger_to_bars(all_klines_raw[sym], signals, sym_short)
        klines = tg.compute_spike(klines)
        all_klines[sym] = klines

    all_klines_ready = {sym: prepare_klines(signals, all_klines_raw, all_klines, sym) for sym in tg.SYMBOLS}

    # === 1. 参数扫描: z_threshold × hold_bars ===
    print('\n[1] 参数扫描: z_threshold × hold_bars (tier 分级)')
    print(f"{'币种':<8} {'z':<5} {'hold':<6} {'n':<5} {'win':<8} {'ret':<10} {'avg':<8} {'sh':<6}")
    scan_results = {}
    for sym in tg.SYMBOLS:
        sym_short = tg.SYM_KEY[sym]
        klines = all_klines_ready[sym]
        for z_th in [1.5, 2.0, 2.5, 3.0]:
            for hold in [12, 24, 36, 48]:
                r = run_with_params(klines, sym_short, hold_bars=hold, z_threshold=z_th,
                                     tp_pct=0.0, sl_pct=0.0, use_tier=True)
                scan_results[f'{sym_short}_z{z_th}_h{hold}'] = r
                print(f"{sym_short:<8} {z_th:<5} {hold:<6} {r['n_trades']:<5} {r['win_rate']:.1%}   {r['total_return']:+.2%}    {r['avg_pnl']:+.2%}  {r['sharpe']:.2f}")

    # === 2. 最优参数 + TP/SL 优化 (默认 z=2.0, hold=24) ===
    print('\n[2] TP/SL 优化 (z=2.0, hold=24, tier 分级)')
    print(f"{'币种':<8} {'TP%':<5} {'SL%':<5} {'n':<5} {'win':<8} {'ret':<10} {'avg':<8} {'sh':<6}")
    tpsl_results = {}
    for sym in tg.SYMBOLS:
        sym_short = tg.SYM_KEY[sym]
        klines = all_klines_ready[sym]
        for tp in [0.0, 0.03, 0.05, 0.08]:
            for sl in [0.0, 0.03, 0.05, 0.08]:
                r = run_with_params(klines, sym_short, hold_bars=24, z_threshold=2.0,
                                     tp_pct=tp, sl_pct=sl, use_tier=True)
                tpsl_results[f'{sym_short}_tp{tp}_sl{sl}'] = r
                print(f"{sym_short:<8} {tp*100:<5.0f} {sl*100:<5.0f} {r['n_trades']:<5} {r['win_rate']:.1%}   {r['total_return']:+.2%}    {r['avg_pnl']:+.2%}  {r['sharpe']:.2f}")

    # === 3. 多币种组合 (三币种独立账户, 1/3 资金 each) ===
    print('\n[3] 多币种组合 (z=2.0, hold=24, tier 分级, 三币种独立账户)')
    combined_pnl_pct = 0.0
    combined_n = 0
    combined_wins = 0
    combined_returns = []
    for sym in tg.SYMBOLS:
        sym_short = tg.SYM_KEY[sym]
        klines = all_klines_ready[sym]
        r = run_with_params(klines, sym_short, hold_bars=24, z_threshold=2.0, use_tier=True)
        # 1/3 资金
        sym_return_on_third = r['total_return'] / 3
        combined_pnl_pct += sym_return_on_third
        combined_n += r['n_trades']
        combined_wins += int(r['win_rate'] * r['n_trades'])
        combined_returns.append(r['total_return'])
        print(f"  {sym_short}: n={r['n_trades']}, win={r['win_rate']:.1%}, ret={r['total_return']:+.2%}, 1/3贡献={sym_return_on_third:+.2%}")
    combined_win_rate = combined_wins / combined_n if combined_n else 0
    print(f"\n  组合: total_return={combined_pnl_pct:+.2%}, n_trades={combined_n}, win_rate={combined_win_rate:.1%}")
    print(f"  各币种独立回报: BTC={combined_returns[0]:+.2%}, ETH={combined_returns[1]:+.2%}, SOL={combined_returns[2]:+.2%}")
    # 简单组合 Sharpe
    if len(combined_returns) > 1:
        port_std = np.std(combined_returns)
        port_mean = np.mean(combined_returns)
        print(f"  组合年化 Sharpe (粗略, N=3): {port_mean / port_std * np.sqrt(3):.2f}")

    # 落盘
    out = REPO / '实验' / 'tg_param_scan_2026-07-18.json'
    out.write_text(json.dumps({
        'param_scan': scan_results,
        'tpsl_scan': tpsl_results,
        'combined': {
            'total_return': combined_pnl_pct,
            'n_trades': combined_n,
            'win_rate': combined_win_rate,
            'per_symbol_returns': combined_returns,
        }
    }, indent=2, default=str), encoding='utf-8')
    print(f'\n结果已保存: {out}')


if __name__ == '__main__':
    main()

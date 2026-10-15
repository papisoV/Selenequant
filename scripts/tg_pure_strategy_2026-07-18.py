"""
纯 TG 策略回测 (不依赖 bb_v3)

只在 spike 时开仓, 按 tier 分级路由:
- S 级 (≥2 币种同 bar spike): 做空 96h, 仓位 1.0x
- A 级 (listing/whale/security): 做空 96h, 仓位 0.7x
- B 级 (market/macro/partnership): 做空 96h, 仓位 0.5x
- C_ETH_REG: ETH + regulatory → 做多 96h, 仓位 0.5x
- 其他: 跳过

对比:
- pure_tg: 纯 TG, 不开 bb_v3 多仓
- pure_tg_no_tier: 纯 TG, 所有 spike 都做空 0.5x (不分 tier, baseline)
- buy_and_hold: 买入持有
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
HOLD_BARS = 24  # 96h

TIER_CONFIG = {
    'S': {'pos_mult': 1.0, 'action': 'short'},
    'A': {'pos_mult': 0.7, 'action': 'short'},
    'B': {'pos_mult': 0.5, 'action': 'short'},
    'C_ETH_REG': {'pos_mult': 0.5, 'action': 'long'},
    'skip': {'pos_mult': 0.0, 'action': 'none'},
}
A_TIER_EVENTS = {'listing', 'whale', 'security'}
B_TIER_EVENTS = {'market', 'macro', 'partnership'}


@dataclass
class Trade:
    side: str
    entry_ts: pd.Timestamp
    entry_price: float
    exit_ts: object = None
    exit_price: float = 0.0
    exit_reason: str = ''
    pnl_pct: float = 0.0
    qty: float = 1.0


def classify(row, sym_short):
    if row['spike'] != 1:
        return 'skip'
    if row['multi_sym']:
        return 'S'
    evt = row['dominant_event']
    if evt in A_TIER_EVENTS:
        return 'A'
    if evt in B_TIER_EVENTS:
        return 'B'
    if sym_short == 'ETH' and evt == 'regulatory':
        return 'C_ETH_REG'
    return 'skip'


def run_pure_tg(klines: pd.DataFrame, sym_short: str, use_tier: bool = True) -> dict:
    """只在 spike 时开仓, hold 24 bars."""
    trades = []
    open_trade: Trade | None = None
    cash = INITIAL_CASH

    for i in range(len(klines)):
        row = klines.iloc[i]
        ts = klines.index[i]
        price = row['close']
        buy_price = price * (1 + SLIPPAGE_BPS / 10000)
        sell_price = price * (1 - SLIPPAGE_BPS / 10000)

        # 平仓
        if open_trade is not None:
            entry_pos = tg._find_pos(klines, open_trade.entry_ts)
            if i - entry_pos >= HOLD_BARS:
                if open_trade.side == 'short':
                    cost = buy_price * open_trade.qty
                    cash -= cost + cost * COMMISSION
                    open_trade.exit_price = buy_price
                else:
                    proceeds = sell_price * open_trade.qty
                    cash += proceeds - proceeds * COMMISSION
                    open_trade.exit_price = sell_price
                open_trade.exit_ts = ts
                open_trade.exit_reason = 'hold_end'
                if open_trade.side == 'short':
                    open_trade.pnl_pct = (open_trade.entry_price - open_trade.exit_price) / open_trade.entry_price - 2 * COMMISSION
                else:
                    open_trade.pnl_pct = (open_trade.exit_price - open_trade.entry_price) / open_trade.entry_price - 2 * COMMISSION
                trades.append(open_trade)
                open_trade = None

        # 开仓
        if open_trade is None and row['spike'] == 1:
            if use_tier:
                tier = classify(row, sym_short)
                cfg = TIER_CONFIG.get(tier, TIER_CONFIG['skip'])
            else:
                # 不分 tier, 所有 spike 都做空 0.5x
                cfg = {'pos_mult': 0.5, 'action': 'short'}
            if cfg['action'] == 'none':
                continue
            use_cash = cash * 0.5 * cfg['pos_mult']
            if use_cash < 100:
                continue
            if cfg['action'] == 'short':
                qty = use_cash / sell_price
                cash += use_cash - use_cash * COMMISSION
                entry_p = sell_price
            else:
                qty = use_cash / buy_price
                cash -= use_cash + use_cash * COMMISSION
                entry_p = buy_price
            open_trade = Trade(side=cfg['action'], entry_ts=ts, entry_price=entry_p, qty=qty)

    # 强制平
    if open_trade is not None:
        last = klines.iloc[-1]['close']
        if open_trade.side == 'short':
            exit_p = last * (1 + SLIPPAGE_BPS / 10000)
            cost = exit_p * open_trade.qty
            cash -= cost + cost * COMMISSION
        else:
            exit_p = last * (1 - SLIPPAGE_BPS / 10000)
            proceeds = exit_p * open_trade.qty
            cash += proceeds - proceeds * COMMISSION
        open_trade.exit_ts = klines.index[-1]
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
        'final_cash': cash,
        'win_rate': sum(1 for p in pnls if p > 0) / len(pnls),
        'avg_pnl': float(np.mean(pnls)),
        'max_pnl': max(pnls),
        'min_pnl': min(pnls),
        'sharpe': float(np.mean(pnls) / np.std(pnls) * np.sqrt(len(pnls))) if np.std(pnls) > 0 else 0,
        'trades': [
            {'side': t.side, 'entry': str(t.entry_ts), 'pnl': t.pnl_pct, 'reason': t.exit_reason}
            for t in trades
        ],
    }


def buy_and_hold(klines: pd.DataFrame) -> dict:
    entry = klines.iloc[0]['close']
    exit_ = klines.iloc[-1]['close']
    ret = (exit_ - entry) / entry
    return {'n_trades': 1, 'total_return': ret, 'final_cash': INITIAL_CASH * (1 + ret),
            'win_rate': 1.0 if ret > 0 else 0.0, 'avg_pnl': ret}


def main():
    print('=' * 70)
    print('纯 TG 策略回测 (脱离 bb_v3)')
    print('=' * 70)
    signals = tg.load_tg_signals()
    signals['mapped_symbols'] = signals.apply(tg.map_signal_to_symbols, axis=1)

    # 预加载所有币种, 计算 vol_trigger + spike
    all_klines_raw = {}
    for sym in tg.SYMBOLS:
        all_klines_raw[sym] = tg.load_klines(sym)

    all_klines = {}
    for sym in tg.SYMBOLS:
        sym_short = tg.SYM_KEY[sym]
        klines = tg.attach_vol_trigger_to_bars(all_klines_raw[sym], signals, sym_short)
        klines = tg.compute_spike(klines)
        all_klines[sym] = klines

    # 加 dominant_event + multi_sym (同 bar)
    all_klines_ready = {}
    for sym in tg.SYMBOLS:
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

        # 独立 spike dedup (cluster-aware, gap >= 24 bars)
        spike_ts_all = list(spike_idx)
        spike_ts_dedup = tg.cluster_dedup_spikes(spike_ts_all, gap_bars=tg.DEDUP_GAP_BARS)
        # 用 dedup 后的 spike ts 重置 spike 列 (避免 overlapping trades)
        klines['spike_dedup'] = klines.index.isin(spike_ts_dedup).astype(int)
        # run_pure_tg 用 spike 列, 改成 dedup 版本
        klines['spike_orig'] = klines['spike']
        klines['spike'] = klines['spike_dedup']

        all_klines_ready[sym] = klines

    # 三种配置 × 三币种
    print(f"\n{'币种':<10} {'策略':<22} {'n_trades':<10} {'win_rate':<10} {'total_return':<14} {'avg_pnl':<10} {'sharpe':<8}")
    print('-' * 90)
    results = {}
    for sym in tg.SYMBOLS:
        sym_short = tg.SYM_KEY[sym]
        klines = all_klines_ready[sym]
        # spike 数 (dedup 后)
        n_spike = int(klines['spike'].sum())
        n_multi = int(klines[klines['spike']==1]['multi_sym'].sum())
        n_s_tier = sum(1 for _, r in klines[klines['spike']==1].iterrows() if classify(r, sym_short) == 'S')
        print(f"\n  {sym} ({sym_short}) - 独立 spike: {n_spike}, multi_sym (S级): {n_multi}")

        # 1. buy and hold
        bh = buy_and_hold(klines)
        results[f'{sym_short}_buy_hold'] = bh
        print(f"  {sym_short:<10} {'buy_and_hold':<22} {1:<10} {bh['win_rate']:.1%}    {bh['total_return']:+.2%}        {bh['avg_pnl']:+.2%}    -")

        # 2. pure_tg_no_tier (所有 spike 做空 0.5x, hold 24 bars)
        r2 = run_pure_tg(klines, sym_short, use_tier=False)
        results[f'{sym_short}_pure_notier'] = r2
        print(f"  {sym_short:<10} {'pure_tg_no_tier':<22} {r2['n_trades']:<10} {r2['win_rate']:.1%}    {r2['total_return']:+.2%}        {r2['avg_pnl']:+.2%}    {r2['sharpe']:.2f}")

        # 3. pure_tg_tier (S/A/B/C 分级路由)
        r3 = run_pure_tg(klines, sym_short, use_tier=True)
        results[f'{sym_short}_pure_tier'] = r3
        print(f"  {sym_short:<10} {'pure_tg_tier':<22} {r3['n_trades']:<10} {r3['win_rate']:.1%}    {r3['total_return']:+.2%}        {r3['avg_pnl']:+.2%}    {r3['sharpe']:.2f}")

    # 落盘
    out = REPO / '实验' / 'tg_pure_strategy_2026-07-18.json'
    payload = {}
    for k, v in results.items():
        if isinstance(v, dict) and 'trades' in v:
            payload[k] = {kk: vv for kk, vv in v.items() if kk != 'trades'}
            payload[k]['trades_preview'] = v['trades'][:10]
        else:
            payload[k] = v
    out.write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')
    print(f'\n结果已保存: {out}')


if __name__ == '__main__':
    main()

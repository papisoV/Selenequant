"""
bb_v3 + TG spike overlay 回测

对比 4 种配置:
1. baseline_bb_v3: 纯 bb_v3 BUY/TP/SL
2. tg_filter: bb_v3 BUY + TG spike 时**放弃**这个 BUY (减仓过滤)
3. tg_short_overlay: bb_v3 BUY + TG spike 时**额外开空** (对冲)
4. tg_tier_overlay: bb_v3 BUY + TG 分级规则 (S/A/B/C 路由)

口径与 tg_revalidation_2026-07-18.py 一致:
- B 口径符号映射
- vol_trigger 衰减聚合
- strict spike (rolling 20, shift 1, z > 2.0)
- event_type 取 spike 前 24h 内最强信号
- ≥2 币种同步 spike = S 级
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


# bb_v3 默认参数 (来自 factory.py)
BB_V3_PARAMS = {
    'bb_len': 7,
    'buy_len': 44,
    'buy_mult': 2.664,
    'sell_len': 20,
    'sell_mult': 2.54,
    'buy_tp_pct': 0.12704,
    'buy_sl_pct': 0.07525,
    'enable_long': True,
    'enable_short': False,
}

INITIAL_CASH = 10_000.0
COMMISSION = 0.001  # 0.1%
SLIPPAGE_BPS = 1.0  # 1 bp

# Tier 配置 (来自 alpha breakdown)
TIER_CONFIG = {
    'S': {'pos_mult': 1.0, 'hold_bars': 24, 'action': 'short'},   # ≥2 币种同步
    'A': {'pos_mult': 0.7, 'hold_bars': 24, 'action': 'short'},   # listing/whale/security
    'B': {'pos_mult': 0.5, 'hold_bars': 24, 'action': 'short'},   # market/macro/partnership
    'C_ETH_REG': {'pos_mult': 0.5, 'hold_bars': 24, 'action': 'long'},  # ETH regulatory 反向
    'skip': {'pos_mult': 0.0, 'hold_bars': 0, 'action': 'none'},
}

A_TIER_EVENTS = {'listing', 'whale', 'security'}
B_TIER_EVENTS = {'market', 'macro', 'partnership'}


@dataclass
class Trade:
    side: str  # 'long' or 'short'
    entry_ts: pd.Timestamp
    entry_price: float
    exit_ts: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str = ''  # 'tp', 'sl', 'signal', 'hold_end'
    qty: float = 1.0
    pnl_pct: float = 0.0


def compute_bb_v3_signals(klines: pd.DataFrame) -> pd.DataFrame:
    """生成 bb_v3 BUY/SELL/TP/SL 信号列."""
    df = klines.copy()
    p = BB_V3_PARAMS
    close = df['close']

    buy_mid = close.rolling(p['buy_len'], min_periods=p['buy_len']).mean()
    buy_std = close.rolling(p['buy_len'], min_periods=p['buy_len']).std()
    df['bb_lower'] = buy_mid - p['buy_mult'] * buy_std

    sell_mid = close.rolling(p['sell_len'], min_periods=p['sell_len']).mean()
    sell_std = close.rolling(p['sell_len'], min_periods=p['sell_len']).std()
    df['bb_upper'] = sell_mid + p['sell_mult'] * sell_std

    # BUY: close 上穿 lower (前一个 bar close < lower, 当前 close >= lower)
    df['prev_close'] = close.shift(1)
    df['prev_lower'] = df['bb_lower'].shift(1)
    df['buy_signal'] = (df['prev_close'] < df['prev_lower']) & (close >= df['bb_lower'])
    # SELL (平多): close 下穿 upper
    df['prev_upper'] = df['bb_upper'].shift(1)
    df['sell_signal'] = (df['prev_close'] > df['prev_upper']) & (close <= df['bb_upper'])
    return df


def attach_tg_context(klines: pd.DataFrame, signals: pd.DataFrame, sym_short: str,
                      all_klines: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """把 vol_trigger/spike/event_type/multi_sym 加到 klines."""
    klines = tg.attach_vol_trigger_to_bars(klines, signals, sym_short)
    klines = tg.compute_spike(klines)

    # event_type 标签: spike bar 前 24h 内 urgency*magnitude 最大的 event
    sigs_all = signals.copy()
    sig_ts_ns = sigs_all['ts'].astype('int64').values
    sig_evt = sigs_all['event_type'].values
    sig_um = (sigs_all['urgency'] * sigs_all['magnitude']).values

    def dominant_event(bar_ts):
        bar_ns = bar_ts.value
        mask = (sig_ts_ns >= bar_ns - 24*3600*1e9) & (sig_ts_ns <= bar_ns)
        if not mask.any():
            return None
        evts = sig_evt[mask]
        ums = sig_um[mask]
        # 取 um 最大的 event_type
        idx = np.argmax(ums)
        return evts[idx]

    klines['dominant_event'] = None
    spike_mask = klines['spike'] == 1
    for i in klines.index[spike_mask]:
        klines.at[i, 'dominant_event'] = dominant_event(i)

    # multi_sym: 当 bar 是否有多币种同步 spike
    # (本币 spike + 其他币种同一 bar 或前一 bar 也 spike)
    other_syms = [s for s in all_klines.keys() if tg.SYM_KEY[s] != sym_short]
    other_spike_sets = []
    for s in other_syms:
        ok = all_klines[s]
        # 用同样的 spike 检测, 已经在 load 时算好
        if 'spike' in ok.columns:
            other_spike_sets.append(set(ok.index[ok['spike'] == 1]))

    def is_multi(bar_ts):
        if not other_spike_sets:
            return False
        cnt = 1  # 本币种 spike
        for sset in other_spike_sets:
            # 检查 ±2 bar 范围 (8h 内)
            for offset_bars in [-1, 0, 1]:
                near = bar_ts + pd.Timedelta(hours=4*offset_bars)
                if near in sset:
                    cnt += 1
                    break
        return cnt >= 2

    klines['multi_sym'] = False
    for i in klines.index[spike_mask]:
        klines.at[i, 'multi_sym'] = is_multi(i)

    return klines


def classify_tier(row, sym_short: str) -> str:
    """根据 spike/event/multi_sym 判定 tier."""
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


def run_backtest(klines: pd.DataFrame, mode: str, sym_short: str) -> dict:
    """
    mode:
      'baseline'      - 纯 bb_v3, 不考虑 TG
      'tg_filter'     - bb_v3 BUY 遇 TG spike 时放弃 BUY
      'tg_short'      - bb_v3 + TG spike 额外做空 (固定 0.7x 仓位, 96h)
      'tg_tier'       - bb_v3 + TG 分级路由 (S/A/B/C)
    """
    df = compute_bb_v3_signals(klines).copy()
    if mode != 'baseline':
        df['tier'] = df.apply(lambda r: classify_tier(r, sym_short), axis=1)
    else:
        df['tier'] = 'skip'

    trades: list[Trade] = []
    open_long: Trade | None = None
    open_short: Trade | None = None  # TG 触发的对冲空仓

    cash = INITIAL_CASH
    equity_curve = []

    for i in range(len(df)):
        row = df.iloc[i]
        ts = df.index[i]
        price = row['close']
        # 应用滑点
        buy_price = price * (1 + SLIPPAGE_BPS / 10000)
        sell_price = price * (1 - SLIPPAGE_BPS / 10000)

        # 1. 平多仓 (TP/SL/sell_signal/hold_end)
        if open_long is not None:
            ret = (price - open_long.entry_price) / open_long.entry_price
            exit_reason = None
            exit_price = None
            if ret >= BB_V3_PARAMS['buy_tp_pct']:
                exit_reason = 'tp'
                exit_price = open_long.entry_price * (1 + BB_V3_PARAMS['buy_tp_pct'])
            elif ret <= -BB_V3_PARAMS['buy_sl_pct']:
                exit_reason = 'sl'
                exit_price = open_long.entry_price * (1 - BB_V3_PARAMS['buy_sl_pct'])
            elif row['sell_signal']:
                exit_reason = 'signal'
                exit_price = sell_price
            elif (ts - open_long.entry_ts).total_seconds() >= 7 * 24 * 3600:
                exit_reason = 'hold_end'
                exit_price = sell_price

            if exit_reason:
                # 平多: 卖出 qty 单位, 收回 exit_price*qty, 扣 commission
                proceeds = exit_price * open_long.qty
                commission_cost = proceeds * COMMISSION
                cash += proceeds - commission_cost  # 开仓时已扣本金, 这里加回净收入
                open_long.exit_ts = ts
                open_long.exit_price = exit_price
                open_long.exit_reason = exit_reason
                open_long.pnl_pct = (exit_price - open_long.entry_price) / open_long.entry_price - 2 * COMMISSION
                trades.append(open_long)
                open_long = None

        # 2. 平 TG 对冲空仓 (按 hold_bars=24 平)
        if open_short is not None:
            entry_pos = tg._find_pos(df, open_short.entry_ts)
            cur_pos = tg._find_pos(df, ts)
            exit_reason = None
            exit_price = None
            if cur_pos is not None and entry_pos is not None and cur_pos - entry_pos >= 24:
                exit_reason = 'hold_end'
                exit_price = buy_price  # 平空 = 买回

            if exit_reason:
                # 平空: 买回 qty 单位, 花费 exit_price*qty + commission
                cost = exit_price * open_short.qty
                commission_cost = cost * COMMISSION
                cash -= cost + commission_cost  # 开仓时收了 entry*qty, 现在花掉 cost
                open_short.exit_ts = ts
                open_short.exit_price = exit_price
                open_short.exit_reason = exit_reason
                open_short.pnl_pct = (open_short.entry_price - exit_price) / open_short.entry_price - 2 * COMMISSION
                trades.append(open_short)
                open_short = None

        # 3. 开 bb_v3 BUY
        if open_long is None and row['buy_signal']:
            if mode == 'tg_filter' and row['spike'] == 1:
                pass  # skip BUY
            else:
                use_cash = cash * 0.95
                qty = use_cash / buy_price
                commission = use_cash * COMMISSION
                cash -= use_cash + commission  # 扣全额本金 + commission
                open_long = Trade(side='long', entry_ts=ts, entry_price=buy_price, qty=qty)

        # 4. 开 TG 对冲空仓 / 分级路由
        if mode == 'tg_short' and row['spike'] == 1 and open_short is None:
            use_cash = cash * 0.5
            qty = use_cash / sell_price
            commission = use_cash * COMMISSION
            cash += use_cash - commission  # 开空: 收 entry*qty (相当于借入卖出), 扣 commission
            open_short = Trade(side='short', entry_ts=ts, entry_price=sell_price, qty=qty)
        elif mode == 'tg_tier':
            tier = row['tier']
            cfg = TIER_CONFIG.get(tier, TIER_CONFIG['skip'])
            if cfg['action'] != 'none' and open_short is None:
                pos_mult = cfg['pos_mult']
                use_cash = cash * 0.5 * pos_mult
                if use_cash > 100:
                    if cfg['action'] == 'short':
                        qty = use_cash / sell_price
                        commission = use_cash * COMMISSION
                        cash += use_cash - commission
                        entry_p = sell_price
                    else:  # long (C_ETH_REG)
                        qty = use_cash / buy_price
                        commission = use_cash * COMMISSION
                        cash -= use_cash + commission
                        entry_p = buy_price
                    open_short = Trade(side=cfg['action'], entry_ts=ts, entry_price=entry_p, qty=qty)

        equity_curve.append({'ts': ts, 'cash': cash, 'open_long': open_long is not None,
                              'open_short': open_short is not None, 'price': price})

    # 强制平仓所有剩余仓位
    if open_long is not None:
        last_price = df.iloc[-1]['close'] * (1 - SLIPPAGE_BPS / 10000)
        proceeds = last_price * open_long.qty
        commission_cost = proceeds * COMMISSION
        cash += proceeds - commission_cost
        open_long.exit_ts = df.index[-1]
        open_long.exit_price = last_price
        open_long.exit_reason = 'final'
        open_long.pnl_pct = (last_price - open_long.entry_price) / open_long.entry_price - 2 * COMMISSION
        trades.append(open_long)
    if open_short is not None:
        last_price = df.iloc[-1]['close'] * (1 + SLIPPAGE_BPS / 10000)
        cost = last_price * open_short.qty
        commission_cost = cost * COMMISSION
        cash -= cost + commission_cost
        open_short.exit_ts = df.index[-1]
        open_short.exit_price = last_price
        open_short.exit_reason = 'final'
        open_short.pnl_pct = (open_short.entry_price - last_price) / open_short.entry_price - 2 * COMMISSION
        trades.append(open_short)

    # 计算 metrics
    if trades:
        pnls = [t.pnl_pct for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / len(pnls)
        total_return = (cash - INITIAL_CASH) / INITIAL_CASH
        avg_pnl = np.mean(pnls)
        max_pnl = max(pnls)
        min_pnl = min(pnls)
        # 简单 Sharpe (按交易笔数)
        if np.std(pnls) > 0:
            sharpe = avg_pnl / np.std(pnls) * np.sqrt(len(pnls))
        else:
            sharpe = 0.0
        long_trades = [t for t in trades if t.side == 'long']
        short_trades = [t for t in trades if t.side == 'short']
        return {
            'mode': mode,
            'symbol': sym_short,
            'n_trades': len(trades),
            'n_long': len(long_trades),
            'n_short': len(short_trades),
            'win_rate': win_rate,
            'total_return': total_return,
            'avg_pnl_pct': avg_pnl,
            'max_pnl_pct': max_pnl,
            'min_pnl_pct': min_pnl,
            'sharpe_approx': sharpe,
            'final_cash': cash,
            'trades_summary': [
                {'side': t.side, 'entry': str(t.entry_ts), 'exit': str(t.exit_ts) if t.exit_ts else None,
                 'pnl_pct': t.pnl_pct, 'reason': t.exit_reason}
                for t in trades[:30]
            ],
        }
    return {'mode': mode, 'symbol': sym_short, 'n_trades': 0, 'total_return': 0.0}


def main():
    print('=' * 70)
    print('bb_v3 + TG spike overlay 回测')
    print('=' * 70)

    signals = tg.load_tg_signals()
    signals['mapped_symbols'] = signals.apply(tg.map_signal_to_symbols, axis=1)

    # 预加载三币种 klines + TG context (用于 multi_sym 判断)
    all_klines_raw = {}
    for sym in tg.SYMBOLS:
        klines = tg.load_klines(sym)
        all_klines_raw[sym] = klines

    # 先把 spike 算到每个币种上 (轻量, 不重复 attach)
    all_klines = {}
    for sym in tg.SYMBOLS:
        sym_short = tg.SYM_KEY[sym]
        klines = tg.attach_vol_trigger_to_bars(all_klines_raw[sym], signals, sym_short)
        klines = tg.compute_spike(klines)
        all_klines[sym] = klines

    # 给每个币种加 multi_sym 和 dominant_event (需要其他币种的 spike 列, 所以全算完再做)
    all_klines_ready = {}
    for sym in tg.SYMBOLS:
        sym_short = tg.SYM_KEY[sym]
        # 用一个简化版的 attach: 不重算 vol_trigger, 只加 event/multi
        klines = all_klines[sym].copy()
        klines = klines[klines.index >= pd.Timestamp('2025-11-01', tz='UTC')]

        # dominant_event
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
        for i in klines.index[klines['spike'] == 1]:
            klines.at[i, 'dominant_event'] = dom_evt(i)

        # multi_sym: 本币 spike 时, 检查其他币种 ±1 bar (±4h) 是否也 spike
        other_syms = [s for s in tg.SYMBOLS if s != sym]
        other_spike_idx = []
        for s in other_syms:
            ok = all_klines[s]
            spike_ts_set = set(ok.index[ok['spike'] == 1])
            other_spike_idx.append(spike_ts_set)

        def is_multi(bar_ts):
            # 只算同 bar (±0) 同步, ±1 bar 太宽会假阳
            cnt = 1
            for sset in other_spike_idx:
                if bar_ts in sset:
                    cnt += 1
            return cnt >= 2

        klines['multi_sym'] = False
        for i in klines.index[klines['spike'] == 1]:
            klines.at[i, 'multi_sym'] = is_multi(i)

        all_klines_ready[sym] = klines

    # 4 种模式 × 3 币种 = 12 个回测
    modes = ['baseline', 'tg_filter', 'tg_short', 'tg_tier']
    all_results = {}
    for sym in tg.SYMBOLS:
        sym_short = tg.SYM_KEY[sym]
        klines = all_klines_ready[sym]
        print(f'\n========== {sym} ({sym_short}) ==========')
        print(f"区间: {klines.index[0]} -> {klines.index[-1]} ({len(klines)} bars)")
        print(f"spike 总数: {(klines['spike']==1).sum()}, multi_sym: {klines['multi_sym'].sum()}")
        for mode in modes:
            r = run_backtest(klines, mode, sym_short)
            all_results[f'{sym_short}_{mode}'] = r
            print(f"\n  [{mode}]")
            print(f"    n_trades: {r['n_trades']} (long={r.get('n_long',0)}, short={r.get('n_short',0)})")
            print(f"    win_rate: {r['win_rate']:.1%}" if 'win_rate' in r else "    win_rate: N/A")
            print(f"    total_return: {r['total_return']:+.2%}")
            print(f"    avg_pnl: {r.get('avg_pnl_pct', 0):+.2%}")
            print(f"    sharpe_approx: {r.get('sharpe_approx', 0):.2f}")

    # 汇总
    print('\n' + '=' * 70)
    print('汇总')
    print('=' * 70)
    print(f"{'配置':<25} {'n_trades':<10} {'win_rate':<10} {'total_return':<14} {'avg_pnl':<10}")
    for k, r in all_results.items():
        wr = f"{r['win_rate']:.1%}" if 'win_rate' in r else 'N/A'
        print(f"{k:<25} {r['n_trades']:<10} {wr:<10} {r['total_return']:+.2%}        {r.get('avg_pnl_pct', 0):+.2%}")

    out = REPO / '实验' / 'tg_overlay_backtest_2026-07-18.json'
    out.write_text(json.dumps(all_results, indent=2, default=str), encoding='utf-8')
    print(f'\n结果已保存: {out}')


if __name__ == '__main__':
    main()

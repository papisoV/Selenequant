"""
TG 因子分维度 alpha 诊断
按 event_type / direction / magnitude / urgency / 持有期 分别测 alpha,
找出 alpha 最强的子集, 作为策略落地依据.

口径: B (asterisk→3币种), strict spike (rolling 20, shift 1, z>2.0),
     cluster dedup gap>=24 bars
"""
from __future__ import annotations
import importlib.util
import sys
import json
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve()
REPO = HERE.parents[1]
spec = importlib.util.spec_from_file_location("tg_reval", REPO / "scripts" / "tg_revalidation_2026-07-18.py")
tg = importlib.util.module_from_spec(spec)
sys.modules["tg_reval"] = tg
spec.loader.exec_module(tg)  # type: ignore

signals = tg.load_tg_signals()
signals['mapped_symbols'] = signals.apply(tg.map_signal_to_symbols, axis=1)


def load_klines_with_vt(sym: str) -> pd.DataFrame:
    sym_short = tg.SYM_KEY[sym]
    klines = tg.load_klines(sym)
    klines = tg.attach_vol_trigger_to_bars(klines, signals, sym_short)
    klines = tg.compute_spike(klines)
    return klines[klines.index >= pd.Timestamp('2025-11-01', tz='UTC')]


def calc_alpha(spike_returns: list[float], nonspike_returns: list[float]) -> tuple[float | None, float | None, int, int]:
    if not spike_returns or not nonspike_returns:
        return None, None, 0, 0
    s_mean = float(np.mean(spike_returns))
    ns_mean = float(np.mean(nonspike_returns))
    return s_mean - ns_mean, s_mean, len(spike_returns), len(nonspike_returns)


def get_spike_ts(klines: pd.DataFrame, mask_fn=None) -> list[pd.Timestamp]:
    """所有 spike bar, 可选 mask (基于 klines 自定义列)."""
    if mask_fn:
        mask = klines['spike'] == 1
        sub = klines[mask & mask_fn(klines)]
    else:
        sub = klines[klines['spike'] == 1]
    return list(sub.index)


def compute_returns(klines: pd.DataFrame, ts_list: list[pd.Timestamp], hold_bars: int) -> list[float]:
    rs = []
    for ts in ts_list:
        idx = tg._find_pos(klines, ts)
        if idx is None or idx + hold_bars >= len(klines):
            continue
        entry = klines.iloc[idx]['close']
        exit_ = klines.iloc[idx + hold_bars]['close']
        rs.append(-(exit_ - entry) / entry)  # short return
    return rs


def nonspike_baseline(klines: pd.DataFrame, spike_ts_dedup: list[pd.Timestamp],
                      hold_bars: int, max_samples: int = 200) -> list[float]:
    ns_ts = tg.nonspike_sample(klines, set(spike_ts_dedup),
                                gap_bars=tg.DEDUP_GAP_BARS,
                                hold_bars=hold_bars, max_samples=max_samples)
    return compute_returns(klines, ns_ts, hold_bars)


def test_holding_periods(sym: str, klines: pd.DataFrame, spike_ts_dedup: list[pd.Timestamp]) -> dict:
    """测不同持有期的 alpha."""
    out = {}
    ns_pool_ts = tg.nonspike_sample(klines, set(spike_ts_dedup),
                                    gap_bars=tg.DEDUP_GAP_BARS,
                                    hold_bars=48, max_samples=300)  # 多留点, 后续短持有期能共用
    for hold in [6, 12, 24, 36, 48]:  # 1d, 2d, 4d, 6d, 8d (4h bars)
        s_rs = compute_returns(klines, spike_ts_dedup, hold)
        ns_rs = compute_returns(klines, ns_pool_ts, hold)
        alpha, s_mean, n_s, n_ns = calc_alpha(s_rs, ns_rs)
        out[f'hold_{hold}bars_{hold*4}h'] = {
            'alpha': alpha, 'spike_mean': s_mean, 'n_spike': n_s, 'n_nonspike': n_ns,
            'spike_win': float((np.array(s_rs) > 0).mean()) if s_rs else None,
        }
    return out


def main():
    print('=' * 70)
    print('TG 因子分维度 alpha 诊断')
    print('=' * 70)
    results = {}

    for sym in tg.SYMBOLS:
        print(f'\n========== {sym} ==========')
        klines = load_klines_with_vt(sym)
        spike_ts_all = list(klines[klines['spike'] == 1].index)
        spike_ts_dedup = tg.cluster_dedup_spikes(spike_ts_all, gap_bars=tg.DEDUP_GAP_BARS)
        print(f'独立 spike: {len(spike_ts_dedup)}')

        # 1. 持有期对比
        print('\n--- 持有期对比 (cluster dedup, short) ---')
        hold_results = test_holding_periods(sym, klines, spike_ts_dedup)
        print(f"{'hold':<12} {'alpha':<10} {'spike_net':<12} {'spike_win':<10} {'n_spike':<8}")
        for k, v in hold_results.items():
            print(f"{k:<12} {v['alpha']:+.4%}   {v['spike_mean']:+.4%}    {v['spike_win']:.1%}    {v['n_spike']}")
        results[f'{sym}_holding'] = hold_results

        # 2. spike 强度分层 (z-score)
        print('\n--- spike z-score 强度分层 (hold=24 bars) ---')
        for z_lo, z_hi, label in [(2.0, 2.5, 'z2-2.5'), (2.5, 3.0, 'z2.5-3'), (3.0, 99, 'z>=3')]:
            mask = (klines['spike_z'] > z_lo) & (klines['spike_z'] <= z_hi)
            ts_in_band = list(klines[mask & (klines['spike'] == 1)].index)
            ts_dedup_band = tg.cluster_dedup_spikes(ts_in_band, gap_bars=tg.DEDUP_GAP_BARS)
            s_rs = compute_returns(klines, ts_dedup_band, 24)
            ns_rs = nonspike_baseline(klines, ts_dedup_band, 24)
            alpha, s_mean, n_s, n_ns = calc_alpha(s_rs, ns_rs)
            print(f"  {label}: n_spike={n_s}, spike_net={s_mean:+.4%}, alpha={alpha:+.4%}")
            results.setdefault(f'{sym}_zband', {})[label] = {
                'alpha': alpha, 'spike_mean': s_mean, 'n_spike': n_s, 'n_nonspike': n_ns
            }

        # 3. 按 spike 当时 event_type 主导类别 (最近的非 noise event_type)
        # 用 spike bar 前 24h 内的信号聚合 event_type
        print('\n--- spike 时最近 event_type 分类 (hold=24 bars) ---')
        # 准备 event 索引
        sigs_all = signals.copy()
        sig_ts_ns = sigs_all['ts'].astype('int64').values
        sig_evt = sigs_all['event_type'].values
        evt_categories = ['regulatory', 'partnership', 'market', 'macro', 'whale', 'security', 'listing']
        counts_by_evt = {c: 0 for c in evt_categories}
        alpha_by_evt = {}
        for c in evt_categories:
            ts_this = []
            for st in spike_ts_dedup:
                # 找 spike 前 24h 内最近的 event_type
                st_ns = st.value  # ns
                mask = (sig_ts_ns >= st_ns - 24*3600*1e9) & (sig_ts_ns <= st_ns)
                if not mask.any():
                    continue
                evts = sig_evt[mask]
                if c in evts:
                    ts_this.append(st)
            ts_dedup_c = tg.cluster_dedup_spikes(ts_this, gap_bars=tg.DEDUP_GAP_BARS)
            s_rs = compute_returns(klines, ts_dedup_c, 24)
            ns_rs = nonspike_baseline(klines, ts_dedup_c, 24)
            alpha, s_mean, n_s, n_ns = calc_alpha(s_rs, ns_rs)
            counts_by_evt[c] = n_s
            alpha_by_evt[c] = {'alpha': alpha, 'spike_mean': s_mean, 'n_spike': n_s, 'n_nonspike': n_ns}
            print(f"  {c}: n_spike={n_s}, spike_net={s_mean:+.4%}, alpha={alpha:+.4%}")
        results[f'{sym}_by_event'] = alpha_by_evt

    # 4. 多币种组合 alpha (三个币种 spike 取并集)
    print('\n' + '=' * 70)
    print('多币种组合 spike (取三币种共同 spike 时点)')
    print('=' * 70)
    all_klines = {sym: load_klines_with_vt(sym) for sym in tg.SYMBOLS}
    # 取每个币种的 spike ts 集合
    spike_sets = {sym: set(tg.cluster_dedup_spikes(list(all_klines[sym][all_klines[sym]['spike']==1].index),
                                                   gap_bars=tg.DEDUP_GAP_BARS))
                  for sym in tg.SYMBOLS}
    # 至少 2 个币种同时 spike 的时点
    from collections import Counter
    all_ts = []
    for s in tg.SYMBOLS:
        all_ts.extend(spike_sets[s])
    counter = Counter(all_ts)
    multi_ts = [ts for ts, c in counter.items() if c >= 2]
    multi_ts = sorted(multi_ts)
    # dedup
    multi_dedup = tg.cluster_dedup_spikes(multi_ts, gap_bars=tg.DEDUP_GAP_BARS)
    print(f'≥2 币种同步 spike: {len(multi_dedup)} 个独立时点')
    if multi_dedup:
        # 在三币种各自算 short@96h
        avg_returns = []
        for ts in multi_dedup:
            rs_at_ts = []
            for sym in tg.SYMBOLS:
                klines = all_klines[sym]
                idx = tg._find_pos(klines, ts)
                if idx is None or idx + 24 >= len(klines):
                    continue
                entry = klines.iloc[idx]['close']
                exit_ = klines.iloc[idx + 24]['close']
                rs_at_ts.append(-(exit_ - entry) / entry)
            if rs_at_ts:
                avg_returns.append(np.mean(rs_at_ts))
        if avg_returns:
            print(f'  组合平均 short@96h net: {np.mean(avg_returns):+.4%}')
            print(f'  胜率: {(np.array(avg_returns) > 0).mean():.1%}')
            print(f'  n: {len(avg_returns)}')

    out = REPO / '实验' / 'tg_alpha_breakdown_2026-07-18.json'
    out.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    print(f'\n结果已保存: {out}')


if __name__ == '__main__':
    main()

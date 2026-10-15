"""
LOO 稳健性检验: 每次剔除一个独立 spike, 重算 alpha, 看 Δalpha 分布.

判断标准 (TG_FACTOR_ROBUSTNESS_LOO.md):
- STABLE: std(Δalpha) < 0.01 (1%)
- MODERATE: std 0.01-0.02
- UNSTABLE: std > 0.02
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
# 加载主脚本作为模块 (文件名带 - 不能直接 import)
spec = importlib.util.spec_from_file_location("tg_reval", REPO / "scripts" / "tg_revalidation_2026-07-18.py")
tg = importlib.util.module_from_spec(spec)
sys.modules["tg_reval"] = tg
spec.loader.exec_module(tg)  # type: ignore

# 重跑主 pipeline 拿 klines 和 spike_ts
signals = tg.load_tg_signals()
signals['mapped_symbols'] = signals.apply(tg.map_signal_to_symbols, axis=1)


def loo_one_symbol(sym: str) -> dict:
    sym_short = tg.SYM_KEY[sym]
    klines = tg.load_klines(sym)
    klines = tg.attach_vol_trigger_to_bars(klines, signals, sym_short)
    klines = tg.compute_spike(klines)
    klines = klines[klines.index >= pd.Timestamp('2025-11-01', tz='UTC')]

    spike_bars = klines[klines['spike'] == 1]
    spike_ts_all = list(spike_bars.index)
    spike_ts_dedup = tg.cluster_dedup_spikes(spike_ts_all, gap_bars=tg.DEDUP_GAP_BARS)

    # 完整 alpha (基线)
    full_returns = [tg.net_return_96h(klines, ts, tg.HOLD_BARS) for ts in spike_ts_dedup]
    full_returns = [r for r in full_returns if r is not None]
    nonspike_ts = tg.nonspike_sample(klines, set(spike_ts_dedup), gap_bars=tg.DEDUP_GAP_BARS,
                                     hold_bars=tg.HOLD_BARS, max_samples=200)
    nonspike_returns = [tg.net_return_96h(klines, ts, tg.HOLD_BARS) for ts in nonspike_ts]
    nonspike_returns = [r for r in nonspike_returns if r is not None]
    full_alpha = np.mean(full_returns) - np.mean(nonspike_returns) if full_returns and nonspike_returns else None

    # LOO: 剔除第 i 个 spike, 重算 alpha
    loo_alphas = []
    for i in range(len(full_returns)):
        r_loo = full_returns[:i] + full_returns[i+1:]
        if not r_loo:
            continue
        # nonspike 不变
        alpha_loo = np.mean(r_loo) - np.mean(nonspike_returns)
        loo_alphas.append(alpha_loo)

    loo_arr = np.array(loo_alphas) if loo_alphas else np.array([])
    deltas = (loo_arr - full_alpha) if full_alpha is not None else loo_arr

    return {
        'symbol': sym,
        'n_independent_spikes': len(spike_ts_dedup),
        'n_evaluable_spikes': len(full_returns),
        'full_alpha': float(full_alpha) if full_alpha is not None else None,
        'loo_mean_alpha': float(loo_arr.mean()) if len(loo_arr) else None,
        'loo_min_alpha': float(loo_arr.min()) if len(loo_arr) else None,
        'loo_max_alpha': float(loo_arr.max()) if len(loo_arr) else None,
        'loo_std_alpha': float(loo_arr.std()) if len(loo_arr) else None,
        'loo_std_delta': float(deltas.std()) if len(deltas) else None,
        'collapse_count': int((loo_arr < 0).sum() if len(loo_arr) else 0),  # alpha 转负的次数
        'loo_alphas': [float(a) for a in loo_arr],
        'spike_returns': full_returns,
        'nonspike_mean': float(np.mean(nonspike_returns)) if nonspike_returns else None,
    }


def main():
    print('=' * 70)
    print('LOO 稳健性检验 (2026-07-18)')
    print('=' * 70)
    results = {sym: loo_one_symbol(sym) for sym in tg.SYMBOLS}

    print(f"\n{'币种':<10} {'N_spk':<8} {'full_α':<12} {'loo_min':<12} {'loo_max':<12} {'std(α)':<12} {'std(Δα)':<12} {'collapse':<10} {'判定':<10}")
    for sym, r in results.items():
        std_d = r['loo_std_delta']
        if std_d is None:
            verdict = 'N/A'
        elif std_d < 0.01:
            verdict = 'STABLE'
        elif std_d < 0.02:
            verdict = 'MODERATE'
        else:
            verdict = 'UNSTABLE'
        fa = f"{r['full_alpha']:+.4%}" if r['full_alpha'] is not None else 'N/A'
        print(f"{sym:<10} {r['n_evaluable_spikes']:<8} {fa:<12} {r['loo_min_alpha']:+.4%}  {r['loo_max_alpha']:+.4%}  {r['loo_std_alpha']:.4%}    {std_d:.4%}    {r['collapse_count']:<10} {verdict:<10}")

    out = REPO / '实验' / 'tg_revalidation_loo_2026-07-18.json'
    payload = {
        'results': {sym: {k: v for k, v in r.items() if k not in ('loo_alphas', 'spike_returns')}
                    for sym, r in results.items()},
        'loo_alphas_detail': {sym: r['loo_alphas'] for sym, r in results.items()},
    }
    out.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(f"\n结果已保存: {out}")


if __name__ == '__main__':
    main()

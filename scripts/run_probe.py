"""B4 探针:IC sweep 数据探针 → 研究图谱账本。

把 funding_ic_sweep / vol_estimators_ic_sweep 的滚动 rank-IC 机器按单 symbol
薄封装为 probe runner,结果落 Episode(market/symbol) + Evidence(RECORDED),
evidence 经 addresses 边挂到指定问题,成为 recall 候选 kind —— 调查会经
recall 自动引用真实数据探针结果(CITED 校验零改动)。

用法(仓库根目录):
  python -X utf8 scripts/run_probe.py --probe funding_ic --symbol BTC/USDT \
      --interval 15m --question-id q-xxx
  python -X utf8 scripts/run_probe.py --probe entry_delay_ic --symbol BTC/USDT \
      --interval 1h --question-id q-xxx
  python -X utf8 scripts/run_probe.py --self-check

三条纪律:
- 诚实优先:只落 Evidence(RECORDED),不造 hypothesis,不写判断边
- 只增:重跑 = 新 Episode+Evidence,不去重
- 查库路径:写 ~/.selenespace/data/selenespace.db(API 账本库),绝不碰
  data/research_bridge.db

episode recorded 带 lifecycle 初始子键(DISCOVERED, transitions=[]):落账
不做认识论判断,状态流转经 EpisodeRepository 受控窄门;per-episode 观察
周期状态,非因子级状态。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# sweep 模块级 sys.path 已覆盖其依赖;这里补 storage 与 scripts 自身
sys.path.insert(0, str(REPO / "packages" / "storage"))
sys.path.insert(0, str(REPO / "scripts"))

from funding_ic_sweep import (  # noqa: E402
    HORIZON,
    IC_WINDOW,
    DB_PATH,
    load_funding,
    merge_funding,
    rolling_ic,
)
from vol_estimators_ic_sweep import CANDIDATES as VOL_CANDIDATES  # noqa: E402
from optuna_formula_search_v4 import load_multi_tf_df  # noqa: E402

# 多重检验武器复用 factor-research 包(research_bridge.py 同路径先例),不造轮子
sys.path.insert(0, str(REPO / "packages" / "factor-research"))
from selene_space_factor_research.mining.validation_extensions import (  # noqa: E402
    multiple_testing_correction,
    subperiod_sign_consistency,
)

import selene_space_alpha.factors.pandas_bridge as pb  # noqa: E402
from selene_space_storage import ALL_MIGRATIONS, MigrationRunner  # noqa: E402
from selene_space_storage.sqlite import (  # noqa: E402
    DEFAULT_DB_PATH,
    AsyncSQLiteManager,
)
from selene_space_storage.repositories.research_graph import (  # noqa: E402
    EpisodeRepository,
    EvidenceRepository,
    ResearchEdgeRepository,
    link_derived_from,
    recall,
)

# 与 recall.py 的 _ANCHOR_KEYS 对齐(fail-fast 判据的数据源)
ANCHOR_KEYS = ("strategy_ids", "intervals", "regimes")

# 候选构造复制自 funding_ic_sweep.py main() 单 symbol 段(L103-124),
# 不重构一次性脚本 —— 循环胶水三份并存是第一刀的可接受成本
FUNDING_CANDIDATES = ("funding_level", "funding_z", "funding_chg_24h", "funding_neg")

# entry_delay_ic:进场延迟面(吸收 external-research-project post10_factor_audit 的 delay 轴)
# 数学同构 post10 L171-173,但口径对齐 run_probe 既有 log(L85/L104);post10
# 实为 simple return —— payload target dict 的 return_type 如实写 "log"。
# NaN 策略:按 delay 各自丢尾部非重叠 bar,不 fillna(0)(既有支 fillna(0.0)
# 会伪造零收益污染长延迟档 IC,此处必须不同)。
ENTRY_DELAY_HOLD_BARS = 5
ENTRY_DELAY_DELAYS = (0, 1, 3, 5, 10, 15, 30, 60)

# 试错记账:过线阈值(|recent_90d| ≥ 此值算一个"显著"),与
# validation_extensions 测试同量级(0.02 极宽松起跑)。家族计数 = 同
# (probe, symbol) 的历史 episode 数 —— 阈值随家族变大的硬刹车是后续刀,
# 本刀先让"这个家族试过多少次、过线率是否超随机"成为账本里可查的事实。
IC_PASS_THRESHOLD = 0.02


def _family_stats(results: dict) -> dict:
    """按探针形状数本次过线并跑二项上尾 p(纯函数,self-check 可直测)。

    funding_ic/vol_estimators:候选键 = candidates.{factor} 除 n_bars/基准;
    entry_delay_ic:候选键 = delay_{N} 行,funding_z.ic 过线才计。
    """
    if "n_bars" not in results:
        return {}
    probe_shape = "delay" if any(k.startswith("delay_") for k in results) else "factor"
    n_candidates = 0
    n_sig = 0
    if probe_shape == "delay":
        for key, row in results.items():
            if not key.startswith("delay_") or not isinstance(row, dict):
                continue
            n_candidates += 1
            ic = row.get("funding_z", {}).get("ic")
            if ic is not None and abs(ic) >= IC_PASS_THRESHOLD:
                n_sig += 1
    else:
        bench = "TF_TREND(benchmark)"
        for key, v in results.items():
            if key in ("n_bars", bench) or not isinstance(v, dict):
                continue
            if "recent_90d_mean_ic" not in v:
                continue
            n_candidates += 1
            if abs(float(v["recent_90d_mean_ic"])) >= IC_PASS_THRESHOLD:
                n_sig += 1
    mtc = multiple_testing_correction(n_sig, n_candidates)
    return {
        "threshold_abs": IC_PASS_THRESHOLD,
        "n_candidates": n_candidates,
        "n_sig": n_sig,
        "binom_p": mtc["binom_p"],
        "exceeds_random_baseline": mtc["exceeds_random_baseline"],
    }


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def compute_funding_ic(symbol: str, interval: str) -> tuple[dict, str, str]:
    df, n = load_multi_tf_df(DB_PATH, symbol, "binance", interval, "1h", "futures")
    if df.empty:
        raise RuntimeError(f"NO KLINE DATA for {symbol} {interval} in {DB_PATH}")
    fr = load_funding(symbol)
    if fr.empty:
        raise RuntimeError(f"NO FUNDING DATA for {symbol}")
    df = merge_funding(df, fr)
    level = df["funding_rate"].fillna(0.0).astype(float)
    z = (
        (level - level.rolling(24, min_periods=8).mean())
        / (level.rolling(24, min_periods=8).std() + 1e-9)
    ).fillna(0.0)
    chg = level.diff(3).fillna(0.0)
    fwd_ret = np_log(df["close"].shift(-HORIZON) / df["close"]).fillna(0.0)
    ts = pd_to_dt(df["timestamp"])
    fwd_ret.index = ts
    bench = pb.compute_tf_trend(df).fillna(0.0)
    bench.index = ts
    series = {
        "TF_TREND(benchmark)": bench,
        "funding_level": level,
        "funding_z": z,
        "funding_chg_24h": chg,
        "funding_neg": -level,
    }
    return _score(series, fwd_ret, n, df)


def compute_vol_estimators(symbol: str, interval: str) -> tuple[dict, str, str]:
    df, n = load_multi_tf_df(DB_PATH, symbol, "binance", interval, "1h", "futures")
    if df.empty:
        raise RuntimeError(f"NO KLINE DATA for {symbol} {interval} in {DB_PATH}")
    fwd_ret = np_log(df["close"].shift(-HORIZON) / df["close"]).fillna(0.0)
    ts = pd_to_dt(df["timestamp"])
    fwd_ret.index = ts
    series = {name: fn(df).fillna(0.0) for name, fn in VOL_CANDIDATES.items()}
    return _score(series, fwd_ret, n, df)


def compute_entry_delay_ic(symbol: str, interval: str) -> tuple[dict, str, str]:
    """IC 进场延迟面:固定持有窗整体后移 delay bar,测信号迟到后还剩多少 IC。

    与既有两支的差异:IC 不再是"一个数",而是 delay 轴上的曲线(每档一个
    candidate);fwd_ret 按 delay 现算且**不 fillna** —— 尾部非重叠 bar 直接
    丢弃,长延迟档样本更少是真实事实,不伪造零收益。
    """
    df, n = load_multi_tf_df(DB_PATH, symbol, "binance", interval, "1h", "futures")
    if df.empty:
        raise RuntimeError(f"NO KLINE DATA for {symbol} {interval} in {DB_PATH}")
    fr = load_funding(symbol)
    if fr.empty:
        raise RuntimeError(f"NO FUNDING DATA for {symbol}")
    df = merge_funding(df, fr)
    ts = pd_to_dt(df["timestamp"])
    close = df["close"].astype(float)
    # 单因子族:同 funding 探针的候选(funding_z/funding_chg/funding_neg),便于对照衰减
    level = df["funding_rate"].fillna(0.0).astype(float)
    z = (level - level.rolling(24, min_periods=8).mean()) / (
        level.rolling(24, min_periods=8).std() + 1e-9
    )
    chg = level.diff(3)
    series = {
        "funding_z": z.set_axis(ts),
        "funding_chg_3": chg.set_axis(ts),
        "funding_neg": (-level).set_axis(ts),
    }
    results: dict = {"n_bars": int(n), "hold_bars": ENTRY_DELAY_HOLD_BARS}
    for delay in ENTRY_DELAY_DELAYS:
        # 固定持有窗整体后移:entry=close[t+delay], exit=close[t+delay+H]
        delayed_fwd = np_log(
            close.shift(-(delay + ENTRY_DELAY_HOLD_BARS)) / close.shift(-delay)
        )
        delayed_fwd.index = ts
        # 每档丢尾部 delay+H 根非重叠 bar(真实样本边界,不伪造)
        delayed_fwd = delayed_fwd.iloc[
            : max(0, len(delayed_fwd) - delay - ENTRY_DELAY_HOLD_BARS)
        ]
        if delayed_fwd.dropna().empty:
            results[f"delay_{delay}"] = {"error": "no valid bars for this delay"}
            continue
        row: dict = {"n_valid_bars": int(delayed_fwd.dropna().shape[0])}
        for name, raw in series.items():
            r = raw.reindex(delayed_fwd.index)
            valid = r.notna() & delayed_fwd.notna()
            if int(valid.sum()) < 10:
                row[name] = {"ic": None, "n": int(valid.sum())}
                continue
            # 滚动 rank-IC 日均,复用 _score 的滚动口径但单值化
            ic_daily = (
                rolling_ic(r[valid], delayed_fwd[valid]).resample("1D").mean().dropna()
            )
            ic_val = float(ic_daily.mean()) if len(ic_daily) else None
            row[name] = {
                "ic": ic_val,
                "n": int(valid.sum()),
                # 同 _score:分段符号一致率,诊断用不参与门禁
                "subperiod_sign_consistency": subperiod_sign_consistency(ic_daily),
            }
        results[f"delay_{delay}"] = row
    # 半衰:|IC| 首个 ≤ delay_0 半值的档位(无插值,诚实网格)
    base = results.get("delay_0", {}).get("funding_z", {}).get("ic")
    half_delay = None
    if base and abs(base) > 1e-12:
        for delay in ENTRY_DELAY_DELAYS:
            ic = results.get(f"delay_{delay}", {}).get("funding_z", {}).get("ic")
            if ic is not None and abs(ic) <= abs(base) * 0.5:
                half_delay = delay
                break
    results["ic_half_delay_funding_z"] = half_delay
    boundary_start = ts.iloc[0].isoformat()
    boundary_end = ts.iloc[-1].isoformat()
    return results, boundary_start, boundary_end


def _score(series: dict, fwd_ret, n: int, df) -> tuple[dict, str, str]:
    results: dict = {"n_bars": int(n)}
    for name, raw in series.items():
        raw = raw.fillna(0.0).astype(float)
        raw.index = pd_to_dt(df["timestamp"])
        ic_daily = rolling_ic(raw, fwd_ret).resample("1D").mean().dropna()
        recent_90 = float(ic_daily.iloc[-90:].mean())
        prior_90 = float(ic_daily.iloc[-180:-90].mean())
        results[name] = {
            "overall_mean_ic": float(ic_daily.mean()),
            "recent_90d_mean_ic": recent_90,
            "prior_90d_mean_ic": prior_90,
            "trend_90d_vs_prior": recent_90 - prior_90,
            # 分段符号一致率:区分"全程有效"与"只在某段行情成立"。
            # 诊断字段,不参与 family 阈值判定(同数据二次用作门禁会虚增置信度)。
            "subperiod_sign_consistency": subperiod_sign_consistency(ic_daily),
        }
    ts = pd_to_dt(df["timestamp"])
    boundary_start = ts.iloc[0].isoformat()
    boundary_end = ts.iloc[-1].isoformat()
    return results, boundary_start, boundary_end


def pd_to_dt(ms_series):
    import pandas as pd

    return pd.to_datetime(ms_series, unit="ms", utc=True)


def np_log(s):
    import numpy as np

    return np.log(s)


def build_summary(probe: str, symbol: str, interval: str, results: dict) -> str:
    """按 |recent_90d| 降序的单行摘要,benchmark 恒排最后。"""
    if probe == "entry_delay_ic":
        return build_delay_summary(probe, symbol, interval, results)
    bench_key = "TF_TREND(benchmark)"
    items = [(k, v) for k, v in results.items() if k not in ("n_bars", bench_key)]
    items.sort(key=lambda kv: abs(kv[1]["recent_90d_mean_ic"]), reverse=True)
    parts = [
        f"{k} last90d={v['recent_90d_mean_ic']:+.4f} "
        f"(overall {v['overall_mean_ic']:+.4f}, trend {v['trend_90d_vs_prior']:+.4f})"
        for k, v in items
    ]
    if bench_key in results:
        b = results[bench_key]
        parts.append(f"{bench_key} last90d={b['recent_90d_mean_ic']:+.4f} (benchmark)")
    return f"{probe} {symbol} {interval}: " + "; ".join(parts)


def build_delay_summary(probe: str, symbol: str, interval: str, results: dict) -> str:
    """delay 轴摘要:每档单因子 IC,按 delay 升序;半衰档收尾。"""
    parts = []
    for delay in ENTRY_DELAY_DELAYS:
        row = results.get(f"delay_{delay}")
        if not row or "error" in row:
            parts.append(f"d{delay}=n/a")
            continue
        ic = row.get("funding_z", {}).get("ic")
        n = row.get("n_valid_bars")
        parts.append(
            f"d{delay}={ic:+.4f}(n={n})" if ic is not None else f"d{delay}=None"
        )
    half = results.get("ic_half_delay_funding_z")
    parts.append(f"half_delay={half}")
    return f"{probe} {symbol} {interval}: " + "; ".join(parts)


async def _replay_overlap(
    manager,
    probe: str,
    symbol: str,
    interval: str,
    boundary_start: str,
    boundary_end: str,
) -> dict | None:
    """查同 (probe, symbol, interval) 历史窗口,算本次的重看比例(F3)。

    返回 ``None`` 表示"无从判断"(首次运行 / 无重叠 / 查询失败 / 数据脏)——
    此时 payload 不加键,保既有回归形状。有重叠才返回 dict。

    为什么只按 (probe, symbol, interval) 比对:同一探针同一标的同一周期
    才算同一"留出集";换 probe 或换周期是不同的问题,不构成重看。
    查询失败降级为 None —— 探针的主职责是产出结果,不能因为历史比对
    失败就跑挂。
    """
    from selene_space_storage.repositories.research_graph._shared import (
        window_replay_overlap,
    )

    try:
        rows = await manager.execute(
            "SELECT e.id AS ep_id, e.boundary_start AS bs, e.boundary_end AS be "
            "FROM research_episodes e "
            "WHERE e.subject_type = 'market' AND e.subject_id = ? "
            "AND json_extract(e.recorded_json, '$.probe') = ? "
            "AND json_extract(e.recorded_json, '$.interval') = ? "
            "ORDER BY e.created_at DESC",
            (symbol, probe, interval),
        )
    except Exception:  # noqa: BLE001 — 比对是附加信息,不该拖垮探针
        print("WARNING: replay overlap query failed; skipping", file=sys.stderr)
        return None

    priors = [(r["bs"], r["be"]) for r in rows] if rows else []
    ratio = window_replay_overlap(priors, boundary_start, boundary_end)
    if ratio is None:
        return None
    return {
        "ratio": round(float(ratio), 6),
        "prior_episode_count": len(priors),
        "latest_prior_episode_id": rows[0]["ep_id"],
    }


async def write_probe_results(
    manager,
    *,
    question_id: str,
    probe: str,
    symbol: str,
    interval: str,
    results: dict,
    boundary_start: str,
    boundary_end: str,
    horizon_bars: int = HORIZON,
    target: dict | None = None,
) -> tuple[str, str]:
    """落 Episode(market/symbol) + Evidence(RECORDED) + addresses 边。

    horizon_bars 参数化:既有两支默认 HORIZON=4(funding_ic_sweep.py)行为
    不变;entry_delay_ic 传自己的持有窗。target 仅 entry_delay_ic 携带
    (评估定义随数据走的血缘词汇;既有两支 payload 不加,保回归形状)。
    """
    recorded: dict = {
        "probe": probe,
        "interval": interval,
        "horizon_bars": horizon_bars,
        "ic_window_bars": IC_WINDOW,
        "n_bars": results.get("n_bars"),
        "lifecycle": {"status": "DISCOVERED", "transitions": []},
    }
    payload: dict = {
        "summary": build_summary(probe, symbol, interval, results),
        "probe": probe,
        "symbol": symbol,
        "interval": interval,
        "candidates": results,
    }
    # 家族试错记账:同 (probe, symbol) 历史 episode 数(N)拼进 family 键;
    # 本次 run 内的过线数/二项 p 由 _family_stats 从本次 results 数出。
    # 跨 run 累计过线数需解析历史 payload JSON,留给阈值随 N 抬高的后续刀。
    family = _family_stats(results)
    if family:
        rows_n = await manager.execute(
            "SELECT COUNT(*) AS n FROM research_episodes "
            "WHERE subject_type = 'market' AND subject_id = ? "
            "AND json_extract(recorded_json, '$.probe') = ?",
            (symbol, probe),
        )
        family["n_attempts"] = int(rows_n[0]["n"]) + 1  # 含本次
        payload["family"] = family
    if target is not None:
        recorded["target"] = target
        payload["target"] = target
    # 窗口重看检测(F3):比对同 (probe, symbol, interval) 的历史窗口。
    # 必在建 episode 之前查 —— 否则会把自己算进历史。
    # 诊断字段:只提示"这段历史已看过",不阻断、不改阈值。
    replay = await _replay_overlap(manager, probe, symbol, interval, boundary_start, boundary_end)
    if replay is not None:
        payload["replay_overlap"] = replay
    ep_id = await EpisodeRepository(manager).create(
        "market",
        symbol,
        f"IC probe {probe} {symbol} {interval} @ {_today()}",
        boundary_start,
        boundary_end,
        recorded=recorded,
    )
    ev_id = await EvidenceRepository(manager).create(
        ep_id,
        "RECORDED",
        payload=payload,
        source_ref="scripts/run_probe.py",
    )
    await ResearchEdgeRepository(manager).add_fact_edge(
        ("evidence", ev_id), ("question", question_id), "addresses"
    )
    # 同标的(symbol)重跑链:新探针从上一支派生(子→父,首跑无边;
    # 跨 probe 类型的混合链是有意的同标的研究时间线)
    await link_derived_from(
        manager,
        child_episode_id=ep_id,
        subject_type="market",
        subject_id=symbol,
    )
    return ep_id, ev_id


async def check_question_anchor(manager, question_id: str) -> dict:
    """fail-fast:问题不存在或 anchor 与 ANCHOR_KEYS 无 ≥1 非空交集键即拒绝。

    anchor 无可匹配键 = 证据永不召回(写进去是静默废数据)。
    """
    rows = await manager.execute(
        "SELECT anchor_json FROM research_questions WHERE id = ?", (question_id,)
    )
    if not rows:
        raise SystemExit(f"exit 2: question not found: {question_id}")
    try:
        anchor = json.loads(rows[0]["anchor_json"] or "{}") or {}
    except (TypeError, ValueError):
        anchor = {}
    overlap = [k for k in ANCHOR_KEYS if anchor.get(k)]
    if not overlap:
        raise SystemExit(
            f"exit 2: question {question_id} anchor has no usable keys; "
            f"need ≥1 non-empty of {ANCHOR_KEYS}, got {anchor!r}"
        )
    return anchor


async def _self_check() -> None:
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        await manager.execute(
            "INSERT INTO research_questions (id, title, body, anchor_json, status, "
            "opened_at, updated_at) VALUES "
            "('q-selfcheck', '自检问题', '', "
            "'{\"strategy_ids\": [\"probe-self-check\"]}', 'open', "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        results = {
            "n_bars": 1000,
            "TF_TREND(benchmark)": {
                "overall_mean_ic": 0.003,
                "recent_90d_mean_ic": 0.002,
                "prior_90d_mean_ic": 0.004,
                "trend_90d_vs_prior": -0.002,
            },
            "funding_z": {
                "overall_mean_ic": 0.0098,
                "recent_90d_mean_ic": 0.0121,
                "prior_90d_mean_ic": 0.0075,
                "trend_90d_vs_prior": 0.0046,
            },
        }
        ep_id, ev_id = await write_probe_results(
            manager,
            question_id="q-selfcheck",
            probe="funding_ic",
            symbol="BTC/USDT",
            interval="15m",
            results=results,
            boundary_start="2026-01-01T00:00:00+00:00",
            boundary_end="2026-02-01T00:00:00+00:00",
        )
        ep2_id, ev2_id = await write_probe_results(
            manager,
            question_id="q-selfcheck",
            probe="entry_delay_ic",
            symbol="BTC/USDT",
            interval="1h",
            results={
                "n_bars": 1000,
                "hold_bars": 5,
                "delay_0": {"funding_z": {"ic": 0.01, "n": 990}},
                "ic_half_delay_funding_z": 10,
            },
            boundary_start="2026-01-01T00:00:00+00:00",
            boundary_end="2026-02-01T00:00:00+00:00",
            horizon_bars=5,
            target={
                "entry": "close[t+delay]",
                "exit": "close[t+delay+5]",
                "horizon_bars": 5,
                "return_type": "log",
            },
        )
        rows2 = await manager.execute(
            "SELECT recorded_json FROM research_episodes WHERE id = ?", (ep2_id,)
        )
        recorded2 = json.loads(rows2[0]["recorded_json"] or "{}")
        assert (
            recorded2["horizon_bars"] == 5
        ), "entry_delay_ic recorded horizon must be 5"
        assert (
            recorded2["target"]["return_type"] == "log"
        ), "target dict must ride recorded"
        assert (
            recorded2["lifecycle"]["status"] == "DISCOVERED"
            and recorded2["lifecycle"]["transitions"] == []
        ), "probe episode must start lifecycle at DISCOVERED"
        # 试错记账:第一跑 n_attempts=1;fixture funding_z recent_90d=0.0121
        # 低于阈 0.02 → n_sig=0(低于阈不过线是正确行为)
        rows1 = await manager.execute(
            "SELECT payload_json FROM research_evidence WHERE id = ?", (ev_id,)
        )
        fam1 = json.loads(rows1[0]["payload_json"] or "{}").get("family", {})
        assert fam1.get("n_attempts") == 1, f"first run must be attempt 1: {fam1}"
        assert (
            fam1.get("n_candidates") == 1
        ), f"funding_ic has 1 non-bench candidate: {fam1}"
        assert fam1.get("n_sig") == 0, "0.0121 < 0.02 must not count as significant"
        assert 0.0 <= fam1.get("binom_p", -1) <= 1.0, "binom_p must be a probability"
        # 第二跑(同 probe+symbol)必须数到第一次:n_attempts 1→2
        ep3_id, _ = await write_probe_results(
            manager,
            question_id="q-selfcheck",
            probe="funding_ic",
            symbol="BTC/USDT",
            interval="15m",
            results=results,
            boundary_start="2026-01-01T00:00:00+00:00",
            boundary_end="2026-02-01T00:00:00+00:00",
        )
        rows3 = await manager.execute(
            "SELECT payload_json FROM research_evidence WHERE episode_id = ? "
            "AND mode = 'RECORDED'",
            (ep3_id,),
        )
        fam2 = json.loads(rows3[0]["payload_json"] or "{}").get("family", {})
        assert fam2.get("n_attempts") == 2, f"second run must see attempt 2: {fam2}"
        # F3 窗口重看检测:ep3 与 ep1 同 probe+symbol+interval+窗口 → 必须检出
        # 100% 重叠;首次跑(ep1)无历史 → 不得出现该键。
        replay = json.loads(rows3[0]["payload_json"] or "{}").get("replay_overlap")
        assert replay is not None, "same window rerun must flag replay_overlap"
        assert replay.get("ratio") == 1.0, f"identical window must be 1.0: {replay}"
        assert (
            replay.get("latest_prior_episode_id") == ep_id
        ), f"prior episode must be the first run: {replay}"
        first_payload = json.loads(rows1[0]["payload_json"] or "{}")
        assert (
            "replay_overlap" not in first_payload
        ), "first run has no history — key must be absent, not null"
        # entry_delay_ic 形状:delay_0 有行但 ic=0.01 < 阈 → n_sig=0 且 n_candidates≥1
        ev2_row = await manager.execute(
            "SELECT payload_json FROM research_evidence WHERE id = ?", (ev2_id,)
        )
        fam_delay = json.loads(ev2_row[0]["payload_json"] or "{}").get("family", {})
        assert (
            fam_delay.get("n_candidates") == 1
        ), f"delay probe 1 candidate row: {fam_delay}"
        assert fam_delay.get("n_sig") == 0, "ic 0.01 below 0.02 must not count"
        out = await recall(manager, question_id="q-selfcheck", kinds=("evidence",))
        ids = {c["id"] for c in out}
        assert (
            ev_id in ids and ev2_id in ids
        ), f"both probe evidences must be recallable, got {ids}"
        by_id = {c["id"]: c for c in out}
        cand = by_id[ev_id]
        assert cand["recall_class"] == "learned_experience"
        assert "funding_z" in cand["text"]
        assert cand["evidence_chain"] == [f"evidence:{ev_id}", f"episode:{ep_id}"]
        print(f"SELF-CHECK PASS (ep={ep_id} ev={ev_id} ep2={ep2_id} ev2={ev2_id})")


def main() -> None:
    parser = argparse.ArgumentParser(description="IC probe → research ledger")
    parser.add_argument(
        "--probe",
        choices=("funding_ic", "vol_estimators", "entry_delay_ic"),
        default=None,
    )
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--question-id", default=None)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()

    if args.self_check:
        asyncio.run(_self_check())
        return
    if args.probe is None or args.question_id is None:
        parser.error("--probe and --question-id are required (unless --self-check)")

    async def run() -> None:
        compute = {
            "funding_ic": compute_funding_ic,
            "vol_estimators": compute_vol_estimators,
            "entry_delay_ic": compute_entry_delay_ic,
        }[args.probe]
        results, boundary_start, boundary_end = compute(args.symbol, args.interval)
        target = None
        if args.probe == "entry_delay_ic":
            target = {
                "entry": "close[t+delay]",
                "exit": f"close[t+delay+{ENTRY_DELAY_HOLD_BARS}]",
                "horizon_bars": ENTRY_DELAY_HOLD_BARS,
                "return_type": "log",
                "note": "delay swept over ENTRY_DELAY_DELAYS; per-delay tail bars dropped, not fillna(0)",
            }
        runner = MigrationRunner(ALL_MIGRATIONS)
        async with AsyncSQLiteManager(args.db, migration_runner=runner) as manager:
            await check_question_anchor(manager, args.question_id)
            ep_id, ev_id = await write_probe_results(
                manager,
                question_id=args.question_id,
                probe=args.probe,
                symbol=args.symbol,
                interval=args.interval,
                results=results,
                boundary_start=boundary_start,
                boundary_end=boundary_end,
                horizon_bars=(
                    ENTRY_DELAY_HOLD_BARS if args.probe == "entry_delay_ic" else HORIZON
                ),
                target=target,
            )
            summary = build_summary(args.probe, args.symbol, args.interval, results)
            print(f"episode: {ep_id}")
            print(f"evidence: {ev_id}")
            print(summary)
            out = await recall(
                manager, question_id=args.question_id, kinds=("evidence",)
            )
            ids = [c["id"] for c in out]
            print(f"recall self-proof: {len(out)} candidate(s) {ids}")
            if ev_id not in ids:
                print("WARNING: new evidence not recallable — check anchor overlap")

    asyncio.run(run())


if __name__ == "__main__":
    main()

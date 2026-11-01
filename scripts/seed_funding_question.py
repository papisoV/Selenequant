"""Seed the funding-research line as Selene's first recallable Question.

The four funding studies (2026-09-22 → 2026-09-26) produced committed JSON
artifacts and verdicts, but they lived only in files and session memory —
the exact "Research Archive" failure mode in PRODUCT.md (stored, never
recalled). This script registers them into the real research graph
(~/.selenespace/data/selenespace.db) as one Question with:

  4 Episodes + 4 RECORDED Evidence (one per study, source_ref -> artifact)
  addresses edges evidence -> question
  2 Hypotheses (refuted / retired) with judgment edges
  4 Experiments (one per study, hypothesis-linked)
  1 Revision citing all 4 evidence ids

The question is left OPEN on purpose: the terminal call belongs to the
Director on the Question surface, not to a seed script.

Anchor contract: recall() matches on set overlap of the anchor keys
(strategy_ids / intervals / regimes). This question carries
strategy_ids=["V52"], intervals=["15m", "1d"] so any future research on
V52 at these intervals recalls the funding verdicts.

Usage:
    python scripts/seed_funding_question.py [--db PATH]

Without --db it writes the canonical ~/.selenespace/data/selenespace.db
(same default as local-api's AsyncSQLiteManager, main.py:804).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _pkg in ("storage", "shared-types"):
    sys.path.insert(0, str(_REPO_ROOT / "packages" / _pkg))

from selene_space_storage.migrations import MigrationRunner  # noqa: E402
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS  # noqa: E402
from selene_space_storage.repositories.research_graph import (  # noqa: E402
    EpisodeRepository,
    EvidenceRepository,
    ExperimentRepository,
    HypothesisRepository,
    ResearchEdgeRepository,
    ResearchQuestionRepository,
)
from selene_space_storage.sqlite import (  # noqa: E402
    DEFAULT_DB_PATH,
    AsyncSQLiteManager,
)

QUESTION_TITLE = "funding 拥挤度对 BTC/ETH/SOL 收益有预测力吗?"

QUESTION_BODY = (
    "资金费率拥挤度作为慢变量,对 V52(BTC/ETH/SOL)的收益是否有预测力?"
    "四段研究:0922 4-bar IC sweep(NO-GO)→ 0925 多日视界翻案(7d |IC| 0.11-0.17,"
    "信号真实)→ 0926 门1 入场否决实验(veto 不成立)→ 0926 门2 carry+反转模拟"
    "(INCONCLUSIVE,不立多日赛道)。终局:信号真实、尺度错配、操作化不过关。"
)

ANCHOR = {"strategy_ids": ["V52"], "intervals": ["15m", "1d"]}

REVISION_CONTENT = (
    "四连判决(2026-09-22 → 2026-09-26):funding 拥挤度对短视界(4-bar/1h)收益"
    "无预测力 —— 0922 全族 |IC|≤0.02,0926 门1 极端分位入场质量无分层,veto 不成立;"
    "但对 7d 多日视界信号真实(funding_level |IC| 0.11-0.17,方向与拥挤度假说一致,"
    "ETH 最强且仍在增强),carry 机制成立但量级太小;carry+反转模拟池化 CI 跨零,"
    "不立多日持仓赛道。结论:V52 不加 funding 规则;若未来因其他理由开多日赛道,"
    "ETH 侧 funding 反转是唯一残存候选。信号真实、尺度错配、操作化不过关 —— "
    "证据链闭环,问题留 open 供 Director 终判。"
)

REVISION_RATIONALE = [
    "0922 4-bar sweep: funding_level/z/chg_24h/neg 全族 |IC|≤0.02,不进词表",
    "0925 多日视界: 1d/3d/7d |IC| 单调增长,7d 全 symbol 过 0.10 线 —— 翻案",
    "0926 门1: 3243 笔交易按 trailing 分位分档,极端档无质量差,cond_a/cond_b 全 False",
    "0926 门2: ETH+SOL 池化 n=203 meanPnL +0.34% CI95 [-1.36%,+1.92%] 跨零",
]

# (study date, title, payload, source artifact, experiment kind)
STUDIES: list[dict[str, Any]] = [
    {
        "date": "2026-09-22",
        "title": "funding 因子族 4-bar IC sweep(NO-GO)",
        "verdict": "NO-GO",
        "key_numbers": {
            "family_max_abs_ic": 0.02,
            "benchmark_tf_trend_ic": 0.30,
        },
        "conclusion": (
            "funding_level/z/chg_24h/neg 全族 |IC|≤0.02,弱 TF_TREND 一个数量级,"
            "不进 V52 词表。caveat:4-bar 视界可能错配 8h 慢变量。"
        ),
        "source_ref": "scripts/funding_ic_sweep_2026-09-22.json",
        "experiment_kind": "funding_ic_sweep",
        "hypothesis": "H1",
    },
    {
        "date": "2026-09-25",
        "title": "funding 多日视界 IC sweep(7d 信号翻案)",
        "verdict": "信号真实但规格错配(悬置不进词表)",
        "key_numbers": {
            "ic_7d": {"BTC": -0.113, "ETH": -0.171, "SOL": -0.136},
            "eth_recent90d_ic": -0.234,
            "direction_consistency_months": {"SOL": 13, "ETH": 12, "BTC": 8},
        },
        "conclusion": (
            "funding_level |IC| 随视界单调增长,7d 全 symbol 过 0.10 线,方向与"
            "拥挤度假说一致。信号真实,但 7d 与 4-bar 内核尺度错配,不进词表。"
        ),
        "source_ref": "scripts/funding_ic_sweep_multi_horizon_2026-09-25.json",
        "experiment_kind": "funding_ic_sweep_multi_horizon",
        "hypothesis": "H2",
    },
    {
        "date": "2026-09-26",
        "title": "funding 入场否决实验 门1(veto 不成立)",
        "verdict": "VETO 不成立",
        "key_numbers": {
            "n_trades": 3243,
            "extreme_avg_pnl": 7.50,
            "mid_avg_pnl": 8.10,
            "cond_a": False,
            "cond_b_per_symbol": [False, False, False],
        },
        "conclusion": (
            "3243 笔 tagged 交易按 trailing funding 分位分 7 档,极端档与中段"
            "入场质量无实质差,WR 全档平坦。1h 尺度下 funding 对入场时机无鉴别力,"
            "V52 不加否决规则。"
        ),
        "source_ref": "scripts/funding_veto_test_2026-09-26.json",
        "experiment_kind": "funding_veto_test",
        "hypothesis": "H1",
    },
    {
        "date": "2026-09-26",
        "title": "funding carry+反转模拟 门2(INCONCLUSIVE)",
        "verdict": "INCONCLUSIVE",
        "key_numbers": {
            "pooled_n": 203,
            "pooled_mean_pnl": 0.0034,
            "pooled_ci95": [-0.0136, 0.0192],
            "eth_mean_pnl": 0.0118,
            "sol_mean_pnl": -0.006,
            "carry_leg_range": [0.002, 0.004],
        },
        "conclusion": (
            "4.2 年 1d 结算事件模拟:ETH+SOL 池化 CI 跨零,carry 腿全正但量级小;"
            "不立多日持仓赛道。ETH 侧反转是唯一残存候选,需更多数据。"
        ),
        "source_ref": "scripts/funding_carry_reversal_sim_2026-09-26.json",
        "experiment_kind": "funding_carry_reversal_sim",
        "hypothesis": "H2",
    },
]

H1_STATEMENT = "极端 funding 拥挤度对 4-bar 短视界收益有预测力(可用作入场否决)"
H2_STATEMENT = "funding 慢变量在多日(7d)视界有预测力且可操作化为独立策略"


async def seed(db: Any) -> dict[str, Any]:
    questions = ResearchQuestionRepository(db)
    edges = ResearchEdgeRepository(db)
    episodes = EpisodeRepository(db)
    evidence = EvidenceRepository(db)
    hypotheses = HypothesisRepository(db)
    experiments = ExperimentRepository(db)

    question_id = await questions.create(
        title=QUESTION_TITLE, body=QUESTION_BODY, anchor=ANCHOR
    )

    hyp_ids: dict[str, str] = {}
    for key, statement in (("H1", H1_STATEMENT), ("H2", H2_STATEMENT)):
        hyp_ids[key] = await hypotheses.create(statement)
        await edges.add_fact_edge(
            from_ref=("hypothesis", hyp_ids[key]),
            to_ref=("question", question_id),
            kind="addresses",
        )
    # hypotheses carry their evidence verdicts; create() makes them 'proposed'
    await hypotheses.set_status(hyp_ids["H1"], "refuted")
    await hypotheses.set_status(hyp_ids["H2"], "retired")

    evidence_ids: dict[str, str] = {}
    for s in STUDIES:
        ep_id = await episodes.create(
            subject_type="factor",
            subject_id="funding_level",
            title=s["title"],
            boundary_start=f"{s['date']}T00:00:00+00:00",
            boundary_end=f"{s['date']}T23:59:59+00:00",
            recorded={"verdict": s["verdict"], "scope": "BTC/ETH/SOL futures"},
            occurred_at=f"{s['date']}T00:00:00+00:00",
        )
        await edges.add_fact_edge(
            from_ref=("episode", ep_id),
            to_ref=("question", question_id),
            kind="addresses",
        )
        ev_id = await evidence.create(
            episode_id=ep_id,
            mode="RECORDED",
            payload={
                "verdict": s["verdict"],
                "key_numbers": s["key_numbers"],
                "conclusion": s["conclusion"],
            },
            source_ref=s["source_ref"],
            observed_at=f"{s['date']}T00:00:00+00:00",
        )
        await edges.add_fact_edge(
            from_ref=("evidence", ev_id),
            to_ref=("question", question_id),
            kind="addresses",
        )
        evidence_ids[s["experiment_kind"]] = ev_id

        exp_id = await experiments.create(
            kind=s["experiment_kind"],
            hypothesis_id=hyp_ids[s["hypothesis"]],
            config={"source": s["source_ref"], "verdict": s["verdict"]},
        )
        await edges.add_fact_edge(
            from_ref=("experiment", exp_id),
            to_ref=("question", question_id),
            kind="addresses",
        )
        await experiments.mark_ran(exp_id, ran_at=f"{s['date']}T00:00:00+00:00")
        s["_exp_id"] = exp_id

    # judgment edges: evidence speaks on the hypothesis it tested
    judgments: list[tuple[str, str, str, str]] = [
        (
            "funding_ic_sweep",
            "H1",
            "contradicts",
            "4-bar 视界全族 |IC|≤0.02,与'有预测力'方向相反",
        ),
        (
            "funding_veto_test",
            "H1",
            "contradicts",
            "极端分位入场质量与中段无分层,veto 预注册条件全 False",
        ),
        (
            "funding_ic_sweep_multi_horizon",
            "H2",
            "supports",
            "7d |IC| 0.11-0.17 全 symbol 过线,方向与拥挤度假说一致",
        ),
        (
            "funding_carry_reversal_sim",
            "H2",
            "qualifies",
            "信号真实但操作化不过关:池化 CI 跨零,carry 量级太小,不立赛道",
        ),
    ]
    for ev_key, h_key, kind, rationale in judgments:
        await edges.add_judgment_edge(
            from_ref=("evidence", evidence_ids[ev_key]),
            to_ref=("hypothesis", hyp_ids[h_key]),
            kind=kind,
            rationale=rationale,
            provenance=[
                next(s["source_ref"] for s in STUDIES if s["experiment_kind"] == ev_key)
            ],
            actor="selene",
        )

    revision = await questions.add_revision(
        question_id,
        REVISION_CONTENT,
        actor="selene",
        rationale=REVISION_RATIONALE,
        cited=list(evidence_ids.values()),
    )

    return {
        "question_id": question_id,
        "hypotheses": hyp_ids,
        "evidence": evidence_ids,
        "experiments": {s["experiment_kind"]: s["_exp_id"] for s in STUDIES},
        "revision": revision,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    db_path = str(Path(args.db).expanduser())

    async def run() -> dict[str, Any]:
        runner = MigrationRunner(ALL_MIGRATIONS)
        async with AsyncSQLiteManager(db_path, migration_runner=runner) as db:
            return await seed(db)

    out = asyncio.run(run())
    print(f"question_id : {out['question_id']}")
    print(f"hypotheses  : {out['hypotheses']}")
    print(f"evidence    : {out['evidence']}")
    print(f"experiments : {out['experiments']}")
    print(f"revision    : {out['revision'].get('id')}")
    print(f"db          : {db_path}")


if __name__ == "__main__":
    main()

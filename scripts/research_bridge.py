"""真实验证 → 研究图谱桥接(US-202 生产写入路径首根真数据)。

流程:top_formulas.json 的挖矿产出 → validate_formulas(真实 1d K线) →
每条 FormulaValidation 落一个假设+实验 → verdict/reason 喂 classify_error →
ErrorTypeRepository.upsert(layer) → set_outcome。全 INVALID 也沉淀真实错误文本。

用法(仓库根目录):
  PYTHONPATH="packages/core;packages/alpha-core;packages/shared-types;packages/factor-research;packages/storage" \
  python -X utf8 scripts/research_bridge.py [--mining-out internal-archive/top_formulas.json] [--n-trials 60]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "backtest-engine"))
sys.path.insert(0, str(REPO / "packages" / "core"))
sys.path.insert(0, str(REPO / "packages" / "alpha-core"))
sys.path.insert(0, str(REPO / "packages" / "shared-types"))
sys.path.insert(0, str(REPO / "packages" / "factor-research"))
sys.path.insert(0, str(REPO / "packages" / "storage"))

from selene_space_factor_research.mining.runner import load_klines, build_returns
from selene_space_factor_research.mining.validation_protocol import validate_formulas
from selene_space_storage import ALL_MIGRATIONS, MigrationRunner
from selene_space_storage.repositories.research_graph import (
    ErrorTypeRepository,
    ExperimentRepository,
    HypothesisRepository,
    ResearchEdgeRepository,
    classify_error,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

HYPOTHESIS = (
    "挖矿词表在 BTC/ETH/SOL 1d 真实 K线上的 top 公式能通过 T4 走前验证(DSR≥0.95)"
)


def _verdict_text(fv) -> str:
    """FormulaValidation → 喂 classify_error 的反馈文本。

    只取 verdict: reason 作为错误签名 —— 同失败模式跨公式去重;公式标识
    留在实验 config,不进错误字典。
    """
    return f"{fv.verdict}: {fv.reason}"


async def run(mining_out: Path, n_trials: int) -> None:
    payload = json.loads(mining_out.read_text(encoding="utf-8"))
    symbols: list[str] = payload["symbols"]
    interval: str = payload["interval"]
    records = payload["top_formulas"]
    if not records:
        print("挖矿产出为空,无事可桥接")
        return

    db_path = REPO / "data" / "klines.db"
    data_by_symbol = load_klines(
        db_path,
        symbols,
        interval,
        max_bars=payload.get("bars_per_symbol", 1000),
        market_type="futures",
    )
    ret_by_symbol = build_returns(data_by_symbol)
    datasets = {"crypto_1d": (data_by_symbol, ret_by_symbol)}

    entries = [{"tokens": r["tokens"]} for r in records]
    print(f"验证 {len(entries)} 条公式 × 1 数据集, n_trials={n_trials} …")
    report = validate_formulas(entries, datasets, n_trials=n_trials)
    print("完成。verdict 分布: ", end="")
    dist: dict[str, int] = {}
    for fv in report.formulas:
        dist[fv.verdict] = dist.get(fv.verdict, 0) + 1
    print(json.dumps(dist, ensure_ascii=False))

    bridge_db = REPO / "data" / "research_bridge.db"
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(bridge_db, migration_runner=runner) as manager:
        hyp_repo = HypothesisRepository(manager)
        exp_repo = ExperimentRepository(manager)
        edge_repo = ResearchEdgeRepository(manager)
        err_repo = ErrorTypeRepository(manager)
        hypothesis_id = await hyp_repo.create(HYPOTHESIS)
        await hyp_repo.set_status(hypothesis_id, "testing")

        layer_dist: dict[str, int] = {}
        for i, fv in enumerate(report.formulas):
            eid = await exp_repo.create(
                "backtest",
                hypothesis_id=hypothesis_id,
                config={
                    "readable": fv.readable,
                    "tokens": fv.tokens,
                    "rank": records[i].get("rank"),
                },
            )
            await exp_repo.mark_ran(eid)
            feedback = _verdict_text(fv)
            contents = classify_error(feedback)
            outcome = "success" if fv.decision == "confirm" else "failed"
            if outcome == "failed":
                error_ids = [
                    await err_repo.upsert(content, layer=layer)
                    for content, layer in contents
                ]
                await exp_repo.set_outcome(eid, "failed", error_ids)
                for _, layer in contents:
                    layer_dist[layer] = layer_dist.get(layer, 0) + 1
            else:
                await exp_repo.set_outcome(eid, "success")

        # resolve 边:成功实验解掉前面的同假设失败(rd-agent resolved_by 语义)
        rows = await manager.execute(
            "SELECT id, outcome FROM research_experiments "
            "WHERE hypothesis_id = ? ORDER BY created_at ASC, rowid ASC",
            (hypothesis_id,),
        )
        last_failed = next(
            (r["id"] for r in reversed(rows) if r["outcome"] == "failed"), None
        )
        for r in rows:
            if r["outcome"] == "success" and last_failed:
                await edge_repo.add_fact_edge(
                    ("experiment", last_failed), ("experiment", r["id"]), "resolved_by"
                )
                break

        print(f"图层分布(classify_error): {json.dumps(layer_dist, ensure_ascii=False)}")
        for row in await err_repo.list_all():
            print(
                f"  [{row['layer']}] x{row['occurrence_count']}: "
                f"{row['content'][:80]!r}"
            )
        print(f"假设 {hypothesis_id} 实验数 {len(rows)}, 桥接库 {bridge_db}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--mining-out",
        type=Path,
        default=REPO / "实验" / "alpha-mining" / "top_formulas.json",
    )
    ap.add_argument("--n-trials", type=int, default=60)
    args = ap.parse_args()
    asyncio.run(run(args.mining_out, args.n_trials))


if __name__ == "__main__":
    main()

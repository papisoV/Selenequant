"""Backfill decision outcomes with LLM reflection (B1 Phase B).

手动触发(plan v3 定案: 首轮唯一触发入口, 不自动跑):
    python scripts/backfill_outcomes.py [--account-dir DIR] [--days 5]

数据源按 Day 0.5 FAIL 判决: paper.db fills 是 wipe-and-restore 语义, 不可信;
改走 runner 部署流水(本地 deployments JSON + slot status 快照)取 outcome 区间。

本脚本是 B1 批次唯一的 LLM 触点——DecisionOutcomeMemory 本体是纯存储。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure packages are importable when run from repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for pkg in (
    "packages/agent-runtime",
    "packages/llm-adapter",
    "packages/shared-types",
):
    path = str(_REPO_ROOT / pkg)
    if path not in sys.path:
        sys.path.insert(0, path)

from selene_space_agent.memory.decision_outcomes import (  # noqa: E402
    DecisionOutcomeMemory,
)

_REFLECTION_PROMPT = (
    "You are a trading analyst reviewing your own past decision now that the "
    "outcome is known.\n"
    "The outcome covers a fixed window of trading days after the analysis "
    "date, which may be shorter than the horizon the decision was written for.\n"
    "Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown).\n\n"
    "Cover in order:\n"
    "1. Was the directional call correct? If the outcome metrics include a "
    "realized-return figure, cite it; if they do not, say the figure is "
    "unavailable — do not estimate, infer, or invent any return, alpha, or P&L "
    "number.\n"
    "2. Which part of the investment thesis held or failed? If the window is "
    "too short to judge the thesis (a thesis written for months is not "
    "disproved by a week), say so plainly instead of treating a short move as "
    "its verdict.\n"
    "3. One concrete lesson to apply to the next similar analysis.\n\n"
    "Be specific and terse. Your output will be stored verbatim in a decision log "
    "and re-read by future analysts, so every word must earn its place, and a made-up "
    "number would be quoted back as fact."
)


def _collect_outcome(entry: dict, deploy_record: dict | None) -> dict:
    """从部署流水拼 outcome 指标(无 LLM)。deploy_record 缺失 → 最小占位。

    诚实契约:slot_status 快照**不含**已实现收益/alpha/P&L,故此处显式声明
    ``*_available=False``,绝不填 0.0(0.0 会被读成"持平"这个从未发生的判断)。
    LLM 侧据此诚实降级(见 ``_REFLECTION_PROMPT``),不编造数字。
    """
    slot = (deploy_record or {}).get("slot_status") or {}
    return {
        "summary": slot.get("summary", "no runner slot snapshot available"),
        "signals_emitted": slot.get("signals_emitted", 0),
        "orders_placed": slot.get("orders_placed", 0),
        "errors": slot.get("errors", 0),
        "deploy_record_found": deploy_record is not None,
        # No realized-return source is wired into the deploy pipeline today;
        # declare it unavailable rather than leave the key absent (absent reads
        # as zero) or fabricate one.
        "return_available": False,
        "alpha_available": False,
    }


async def _reflect(llm, decision_text: str, outcome: dict) -> str:
    from selene_space_llm.base import LLMMessage

    user_content = (
        f"Decision (deploy-time thesis):\n{decision_text}\n\n"
        f"Outcome metrics over the last {os.getenv('SELENE_BACKFILL_DAYS', '5')} "
        f"trading days (the evaluation window):\n"
        f"{json.dumps(outcome, ensure_ascii=False, indent=2)}"
    )
    return str(
        await llm.chat(
            [
                LLMMessage(role="system", content=_REFLECTION_PROMPT),
                LLMMessage(role="user", content=user_content),
            ]
        )
    )


def _build_llm():
    """与 main.py 同源: settings.json 的 selenespace.llm 段 → OpenAICompatProvider。"""
    from selene_space_llm.base import LLMConfig
    from selene_space_llm.openai_compat import OpenAICompatProvider

    settings_path = Path.home() / ".selenespace" / "settings.json"
    llm_section: dict = {}
    api_key = ""
    if settings_path.exists():
        section = json.loads(settings_path.read_text(encoding="utf-8"))
        llm_section = section.get("llm", {})
        api_key = os.environ.get("SELENE_LLM_API_KEY") or llm_section.get("api_key", "")
    config = LLMConfig(
        model=llm_section.get("model") or "",
        api_key=api_key,
        base_url=llm_section.get("base_url") or None,
    )
    return OpenAICompatProvider(config)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--deployments",
        type=Path,
        default=Path.home() / ".selenespace" / "deployments.json",
        help="runner 部署流水 JSON(默认 ~/.selenespace/deployments.json)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印计划,不调 LLM 不回填"
    )
    args = parser.parse_args()

    memory = DecisionOutcomeMemory()
    pending = memory.pending_deployments()
    if not pending:
        print("No pending decisions to backfill.")
        return 0

    deploy_records: dict[str, dict] = {}
    if args.deployments.exists():
        try:
            raw = json.loads(args.deployments.read_text(encoding="utf-8"))
            deploy_records = {d.get("id", ""): d for d in raw if isinstance(d, dict)}
        except (OSError, json.JSONDecodeError):
            print(f"WARNING: unreadable deployments file: {args.deployments}")

    if args.dry_run:
        for dep_id in pending:
            found = dep_id in deploy_records
            print(f"[dry-run] {dep_id} deploy_record={'yes' if found else 'MISSING'}")
        return 0

    llm = _build_llm()
    filled = 0
    for dep_id in pending:
        entry = next(
            (e for e in memory._entries if e.get("deployment_id") == dep_id), None
        )
        if entry is None:
            continue
        outcome = _collect_outcome(entry, deploy_records.get(dep_id))
        try:
            reflection = asyncio.run(
                _reflect(llm, str(entry.get("decision_text", "")), outcome)
            )
        except Exception as exc:
            print(f"SKIP {dep_id}: LLM reflection failed: {exc}")
            continue
        if memory.backfill(dep_id, outcome, reflection):
            filled += 1
            print(f"FILLED {dep_id}: {reflection[:80]}...")
    print(f"Done. {filled}/{len(pending)} backfilled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

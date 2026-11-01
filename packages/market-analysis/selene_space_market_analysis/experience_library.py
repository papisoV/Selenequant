"""经验库检索与案例注入（US-202）。

复用 storage ``AnalysisRecordRepository.list_records`` / ``list_failures``
（零迁移），对反序列化 dict 做 Python 侧过滤（interval / market_regime），
按配额组装 stage1 prompt 注入上下文。合规口径见包内 PROVENANCE.md。
"""

from __future__ import annotations

import json
from typing import Any

from selene_space_storage.repositories.analysis_records import (
    AnalysisRecordRepository,
)

# 可参考案例配额上限（与 token 预算共同约束注入体积）。
_CASE_LIMIT = 3
# 单个案例/失败样本注入的最大字符数（超出截断）。
_CASE_MAX_CHARS = 800
# 注入内容总 token 硬顶（保守估算，见 _estimate_tokens）。
_TOKEN_CAP = 2400
# 历史记录纵向对比配额上限（不分成败，仅跳过异常记录）。
_HISTORY_LIMIT = 5
# 历史记录注入 token 硬顶（独立于案例库 _TOKEN_CAP）。
_HISTORY_TOKEN_CAP = 1500


def _is_failure_record(record: dict[str, Any]) -> bool:
    """顶层 ``exception`` 字段 → 失败记录（不可作参考案例）。"""
    return bool(record.get("exception"))


def _is_referenceable_record(record: dict[str, Any]) -> bool:
    """可参考：entry_gate=proceed 且 stage2 decision != no_trade。"""
    stage1 = record.get("stage1") or {}
    stage2 = record.get("stage2") or {}
    return stage1.get("entry_gate") == "proceed" and stage2.get("decision") not in (
        None,
        "no_trade",
    )


def _excerpt_record(record: dict[str, Any]) -> dict[str, Any]:
    """抽取案例关键字段（避免整条记录注入导致 prompt 膨胀）。"""
    input_data = record.get("input_data") or {}
    stage1 = record.get("stage1") or {}
    stage2 = record.get("stage2") or {}
    excerpt = {
        "symbol": input_data.get("symbol"),
        "interval": input_data.get("interval"),
        "market_regime": stage1.get("market_regime"),
        "entry_gate": stage1.get("entry_gate"),
        "diagnosis_summary": stage1.get("diagnosis_summary"),
        "decision": stage2.get("decision"),
        "decision_summary": stage2.get("decision_summary"),
    }
    # T3a: 已判定的结局是先例最有信息量的字段——透出紧凑三元组。
    outcome = record.get("outcome")
    if isinstance(outcome, dict):
        excerpt["outcome"] = {
            "status": outcome.get("status"),
            "pnl_r": outcome.get("pnl_r"),
            "entry_triggered": outcome.get("entry_triggered"),
        }
    return excerpt


def _estimate_tokens(text: str) -> int:
    """保守 token 估算：CJK 每字符 1 token，其余按 4 字符/token（宁可高估）。"""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def _render_injection(
    cases: list[dict[str, Any]], failures: list[dict[str, Any]]
) -> str:
    """渲染注入块：配额截断 + 逐块累计 token 预算硬顶（超限丢弃靠后块）。"""
    blocks: list[str] = []
    for i, case in enumerate(cases, 1):
        text = json.dumps(_excerpt_record(case), ensure_ascii=False)
        if len(text) > _CASE_MAX_CHARS:
            text = text[:_CASE_MAX_CHARS]
        blocks.append(f"【历史案例 {i}】\n{text}")
    for i, inp in enumerate(failures, 1):
        text = json.dumps(inp, ensure_ascii=False)
        if len(text) > _CASE_MAX_CHARS:
            text = text[:_CASE_MAX_CHARS]
        blocks.append(f"【历史失败样本 {i}】\n{text}")

    out: list[str] = []
    budget = 0
    for block in blocks:
        cost = _estimate_tokens(block)
        if budget + cost > _TOKEN_CAP:
            break
        out.append(block)
        budget += cost
    return "\n\n".join(out)


def render_history_context(
    records: list[dict[str, Any]],
    *,
    limit: int = _HISTORY_LIMIT,
    token_cap: int = _HISTORY_TOKEN_CAP,
) -> str:
    """渲染最近 N 条历史分析的纵向对比上下文（不分成败，跳过异常记录）。

    供 LLM 做前后窗口对照（B1：纵向对比意图的独立实现，见包内 PROVENANCE.md）。逐块
    累计 token 预算，超限丢弃靠后块；无记录或全部被跳过后返回空串。
    """
    blocks: list[str] = []
    for record in records:
        if _is_failure_record(record):
            continue
        text = json.dumps(_excerpt_record(record), ensure_ascii=False)
        if len(text) > _CASE_MAX_CHARS:
            text = text[:_CASE_MAX_CHARS]
        blocks.append(f"【历史记录对比 {len(blocks) + 1}】\n{text}")
        if len(blocks) >= limit:
            break

    out: list[str] = []
    budget = 0
    for block in blocks:
        cost = _estimate_tokens(block)
        if budget + cost > token_cap:
            break
        out.append(block)
        budget += cost
    return "\n\n".join(out)


class ExperienceLibrary:
    """经验库：按市场状态检索历史案例并组装 stage1 注入上下文。"""

    def __init__(self, repo: AnalysisRecordRepository) -> None:
        self._repo = repo

    async def list_reference_cases(
        self,
        *,
        symbol: str,
        interval: str,
        market_regime: str | None = None,
        limit: int = _CASE_LIMIT,
        max_records: int = 50,
    ) -> list[dict[str, Any]]:
        """按 symbol+interval+market_regime 命中可参考成功案例。

        ``list_records`` 仅按 symbol 过滤（签名只含 limit/offset/symbol），
        interval 与 market_regime 在 Python 侧对反序列化结果过滤。

        T3a: 已判定 win 的案例优先（同分时保持列表时间序稳定）——
        collect 全量命中再按 (win 优先, 时戳新优先) 稳定排序后截断，
        而非遇到前 3 条就早断。
        """
        records = await self._repo.list_records(
            limit=max_records, offset=0, symbol=symbol
        )
        matched: list[tuple[int, str, dict[str, Any]]] = []
        for rec in records:
            if _is_failure_record(rec):
                continue
            if (rec.get("input_data") or {}).get("interval") != interval:
                continue
            if market_regime is not None:
                if (rec.get("stage1") or {}).get("market_regime") != market_regime:
                    continue
            if not _is_referenceable_record(rec):
                continue
            outcome = rec.get("outcome") or {}
            is_win = 1 if outcome.get("status") == "win" else 0
            ts = str((rec.get("meta") or {}).get("timestamp") or "")
            matched.append((is_win, ts, rec))
        # win 优先,同分时 ISO 时戳字典序(反序 = 新在前)在前;稳定排序保留平手相对序
        matched.sort(key=lambda m: (m[0], m[1]), reverse=True)
        return [m[2] for m in matched[:limit]]

    async def list_failure_inputs(
        self, *, limit: int = _CASE_LIMIT
    ) -> list[dict[str, Any]]:
        """最近失败样本的 input_data（真实 LLM 失败入库，作调优输入）。

        ``list_failures`` 无 symbol/interval 参数且每条返回的 record 嵌套在
        ``item["record"]`` 键下，此处取 ``item["record"]["input_data"]``。
        """
        items = await self._repo.list_failures(limit=limit, offset=0)
        out: list[dict[str, Any]] = []
        for item in items:
            record = item.get("record") or {}
            input_data = record.get("input_data")
            if isinstance(input_data, dict):
                out.append(input_data)
            if len(out) >= limit:
                break
        return out

    async def build_stage1_context(
        self,
        *,
        symbol: str,
        interval: str,
        market_regime: str | None = None,
    ) -> str:
        """组装 stage1 注入上下文。

        可参考案例不足 3 条 → 退化（返回空串，prompt 不注入）；
        否则注入 ≤3 条成功案例 + 最近失败样本，受 token 硬顶约束。
        """
        cases = await self.list_reference_cases(
            symbol=symbol, interval=interval, market_regime=market_regime
        )
        if len(cases) < _CASE_LIMIT:
            return ""
        failures = await self.list_failure_inputs(limit=_CASE_LIMIT)
        return _render_injection(cases, failures)


__all__ = ["ExperienceLibrary"]

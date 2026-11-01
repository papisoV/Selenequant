"""Decision-workflow service: assembles TwoStageWorkflow from package primitives.

Phase 2 (v1) — canned trees use CannedLLMProvider + InMemoryPersistence.
The trading_decision tree additionally exposes ``analyze_trading``: a real
LLM run through the full pipeline builder (trading_pipeline) — global rules,
stats baseline, four windows, overlay hits, experiences, optional stance /
HTF — identical to scripts/run_pa_diagnosis.py.

Per-tree LLM isolation is load-bearing for the canned path: CannedLLMProvider
mutates ``last_messages`` on every call (llm_provider.py), so each tree's
workflow gets its own provider instance to avoid cross-tree races. The real
LLM path builds a fresh workflow per request instead (no shared cursor).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from selene_space_decision_workflow.llm_provider import CannedLLMProvider, LLMMessage
from selene_space_decision_workflow.models import (
    AnalysisRecord,
    DecisionTreeGraph,
    DecisionTreeNode,
    NodeAnswer,
)
from selene_space_decision_workflow.orchestrator import TwoStageWorkflow
from selene_space_decision_workflow.parser import parse_decision_tree
from selene_space_decision_workflow.persistence import InMemoryPersistence
from selene_space_decision_workflow.program_nodes import ProgramNodeRegistry
from selene_space_decision_workflow.routing import (
    AML_ROUTING_TABLE,
    CREDIT_ROUTING_TABLE,
    TRADING_ROUTING_TABLE,
    RoutingTable,
)
from selene_space_decision_workflow.validator import ValidatorConfig

logger = logging.getLogger(__name__)

# Per-tree routing tables (NOT global).
_ROUTING_TABLES: dict[str, RoutingTable] = {
    "credit_approval": CREDIT_ROUTING_TABLE,
    "aml_monitoring": AML_ROUTING_TABLE,
    "trading_decision": TRADING_ROUTING_TABLE,
}

# bar_range used in every synthesized NodeAnswer. K1-K10 satisfies the
# validator's ^K\d+-K\d+$ format and forbids K0 (validator.py:103).
_BAR_RANGE = "K1-K10"

# Neutral reason not in the validator's blocklist (validator.py:26-37).
_REASON = "synthesized canned response"

# Anchored to branch-prefixed lines only (M3 fix). Matches both
# "是：终止 → X" and "否：终止 → X" style lines while avoiding false matches
# in comments or free-text questions.
_TERMINAL_RE = re.compile(
    r"^[是否中性是\[否\[][：:]\s*终止\s*→\s*(\S+)",
    re.MULTILINE,
)

# Anchored follow-up (Phase D): the context is assembled from the frozen
# record only. Both the stage summaries and the accumulated Q&A grow without
# bound, so each section is clipped and the history is capped by turn count.
_FOLLOWUP_SECTION_CHARS = 1200
_FOLLOWUP_HISTORY_TURNS = 4


def _clip(value: Any, limit: int = _FOLLOWUP_SECTION_CHARS) -> str:
    """Render a payload value as text, or "" when there is nothing to show.

    An absent field must not become the literal "null" in the prompt — the
    model reads that as a stated value rather than a missing one.
    """
    if value is None or value == "":
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


def _section(label: str, value: Any) -> str:
    """``label`` + rendered value, or "" when there is no value to show."""
    text = _clip(value)
    return f"{label}{text}" if text else ""


def _build_followup_prompt(
    record: AnalysisRecord, question: str, live_context: dict[str, Any] | None = None
) -> str:
    """Build the interrogation prompt from a record's frozen snapshot.

    The question is answered against what this record captured (RECORDED).
    ``live_context`` — when the caller could read one fresh bar — is injected
    as a clearly labeled separate section (RECONSTRUCTED, "now" reading) so
    the model can address "what about now" without passing it off as what
    the frozen record saw. Zero *unlabeled* live data: without live_context
    the prompt is byte-identical to the fully-anchored form.
    """
    input_data = record.input_data or {}
    stage1 = record.stage1.diagnosis_or_decision or {}
    stage2 = (
        record.stage2.diagnosis_or_decision if record.stage2 is not None else None
    ) or {}
    parts = [
        f"【诊断记录 {record.meta.input_id}】",
        f"品种 {input_data.get('symbol')} / 周期 {input_data.get('interval')}",
        _section("\n【第一阶段诊断】状态:", stage1.get("state")),
        _clip(stage1.get("diagnosis_summary")),
    ]
    if stage2:
        parts += [
            _section("\n【第二阶段决策】", stage2.get("decision")),
            _clip(stage2.get("decision_summary")),
        ]
        order = stage2.get("order")
        if order:
            parts.append(f"【订单】{_clip(order)}")
    if record.outcome:
        parts.append(f"【事后结果】{_clip(record.outcome)}")
    if live_context:
        window_end = input_data.get("summary", {}).get("window_end_iso", "?")
        interval = input_data.get("interval") or "?"
        bars_since = live_context.get("bars_since_end")
        parts.append(
            "\n【现在读数 · 与诊断时点无关,RECONSTRUCTED】\n"
            f"现价 {live_context.get('last_close')} · 自诊断窗口末({window_end})"
            f"已过 {bars_since if bars_since is not None else '?'} 根 {interval} K线\n"
            "回答时:对【诊断记录】的历史判断锚定记录本身;涉及「现在」的判断"
            "可引用现在读数,但必须说明依据哪个时点。"
        )
    history = record.followups or []
    if history:
        lines = ["\n【此前追问】"]
        for item in history[-_FOLLOWUP_HISTORY_TURNS:]:
            lines.append(f"Q: {_clip(item.get('question'))}")
            lines.append(f"A: {_clip(item.get('answer'))}")
        parts.append("\n".join(lines))
    parts.append(f"\n【新的追问】\n{question}")
    return "\n".join(p for p in parts if p)


def _demo_env_enabled() -> bool:
    """Global degraded gate: ``SELENE_PA_DEMO_MODE``.

    Back-compat only. It routes ``analyze_trading`` to a canned walk-through
    when the caller did not explicitly ask for a demo — an explicit
    ``demo=True`` means the opposite (a real pipeline run, badged), so the
    env var must never override it.
    """
    return os.environ.get("SELENE_PA_DEMO_MODE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


@dataclass(frozen=True)
class TreeInfo:
    """Public metadata for a loaded decision tree."""

    tree_id: str
    title: str
    description: str
    node_count: int


class DecisionWorkflowService:
    """Singleton service that assembles TwoStageWorkflow per tree.

    Loaded once at first request (lazy via dependencies.get_decision_workflow_service).
    Trees parsed at __init__ from package data (2 small .txt files, ~5ms each).
    """

    def __init__(
        self,
        settings_loader: Any = None,
        db_loader: Any = None,
    ) -> None:
        # Lazy LLM-config loader (settings service) for the real-LLM path;
        # None is fine when only the canned trees are exercised (tests).
        self._settings_loader = settings_loader
        # Lazy SQLite loader (dependencies.get_db_manager) for the A1
        # persistence path (analyze_trading lands in the v009
        # analysis_records table); None keeps tests in-memory only.
        self._db_loader = db_loader
        self._record_repo: Any = None
        self._trees: dict[str, TwoStageWorkflow] = {}
        self._tree_meta: dict[str, TreeInfo] = {}
        # Read-side cache. RecordPersistence Protocol only has save() (persistence.py:10-13),
        # so the service owns the read-side dict.
        self._records: dict[str, AnalysisRecord] = {}
        # Shared persistence across trees (InMemoryPersistence is stateless + thread-safe
        # for synchronous use — append-only list).
        self._persistence = InMemoryPersistence()
        # Save original LLMs so test-helper set_llm_provider can restore.
        self._original_llms: dict[str, Any] = {}
        # Real-LLM pipeline state (lazy): adapter + module import cached on
        # first analyze_trading call; adapter holds a private daemon loop so
        # it must be long-lived, not per-request.
        self._trading_adapter: Any = None
        self._tp_mod: Any = None
        # Serializes every call that reaches the shared trading adapter (the
        # heartbeat round, analyze_trading's real path, anchored follow-ups).
        # The adapter funnels all coroutines onto one private event loop with
        # a shared AsyncClient, so overlapping callers interleave there and
        # leave last_usage ambiguous. Built here (no running loop required on
        # 3.10+) so the lock outlives any single request.
        self._llm_lock = asyncio.Lock()
        self._load_trees()

    async def _get_record_repo(self) -> Any:
        """Lazily build the v009 analysis_records repo over the shared db.

        Same wiring as MarketAnalysisService._get_flow (db_loader →
        AnalysisRecordRepository). Returns None when no db is wired
        (tests / canned-only), keeping the service in-memory only.
        """
        if self._record_repo is not None:
            return self._record_repo
        if self._db_loader is None:
            return None
        db = self._db_loader()
        if db is None:
            return None
        from selene_space_storage.repositories.analysis_records import (
            AnalysisRecordRepository,
        )

        self._record_repo = AnalysisRecordRepository(db)
        return self._record_repo

    # ------------------------------------------------------------------
    # Real-LLM trading pipeline (service counterpart of run_pa_diagnosis)
    # ------------------------------------------------------------------

    async def _get_trading_adapter(self) -> Any:
        """Build the sync adapter over the settings-configured LLM once.

        Uses the same settings chain as market_analysis_service
        (resolve_llm_full_config -> LLMConfig -> OpenAICompatProvider) and
        wraps it in AsyncBackendToSyncAdapter for the sync orchestrator.
        """
        if self._trading_adapter is not None:
            return self._trading_adapter
        from selene_space_decision_workflow.llm_compat import (
            AsyncBackendToSyncAdapter,
        )
        from selene_space_llm.base import LLMConfig
        from selene_space_llm.openai_compat import OpenAICompatProvider

        if self._settings_loader is None:
            raise RuntimeError(
                "LLM not configured — no settings service bound to "
                "DecisionWorkflowService (wire it in main.py lifespan)"
            )
        settings = self._settings_loader()
        cfg = await settings.resolve_llm_full_config()
        api_key = cfg.get("api_key")
        if not api_key:
            raise RuntimeError(
                "LLM not configured — set base_url/model/api_key in settings first"
            )
        # OpenAICompatProvider appends /chat/completions itself, so the stored
        # base_url must end at the version segment; tolerate a missing /v1
        # (same normalization as the CLI's build_async_backend).
        base_url = str(cfg.get("base_url") or "").rstrip("/")
        if base_url and not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        config = LLMConfig(
            model=cfg.get("model") or "default",
            api_key=api_key,
            base_url=base_url or None,
            temperature=0.0,
            # Matches the CLI: reasoning models burn budget before content;
            # 16384 was validated on the full 120-bar window.
            max_tokens=16384,
        )
        self._trading_adapter = AsyncBackendToSyncAdapter(OpenAICompatProvider(config))
        return self._trading_adapter

    async def analyze_trading(
        self,
        input_data: dict[str, Any],
        *,
        input_id: str,
        session_id: str,
        trading_stance: str | None = None,
        htf_summary_data: dict[str, Any] | None = None,
        direction_signal: dict[str, Any] | None = None,
        demo: bool = False,
    ) -> AnalysisRecord:
        """Run the full trading pipeline (real LLM) — CLI parity.

        ``input_data`` must already be assembled and enriched (caller: the
        POST /analyze-trading handler via trading_pipeline.build_full_input_data
        / enrich_input_data). A fresh workflow is built per request so
        concurrent requests cannot share stance/HTF state; only the LLM
        adapter is cached (it owns a private daemon loop).

        Two demo routes, deliberately opposite in what they do:

        * ``demo=True`` (explicit, Phase D) — the REAL two-stage pipeline
          runs and the record is badged ``meta.demo=True``, but the run is
          kept OUT of the audit table. The outcome review picks the newest
          reviewable record per symbol (:func:`pa_outcome.review_prior_decision`
          → ``_wants_review``), so a persisted demo order would be judged
          against real bars and enter the experience library as if a real
          decision had been made.
        * ``SELENE_PA_DEMO_MODE`` (back-compat) — canned walk-through, no LLM
          call, no bar data used, ``stage2.order`` is None by design (residual
          card, zero fakery). Only reached when ``demo`` was not passed.

        Budget: this method holds no reference to any scheduler's attempt
        counter, so a manually triggered demo structurally cannot consume the
        heartbeat's daily budget. Real token spend is still recorded in the
        record's ``usage_total`` (the orchestrator's sum over every LLM call
        made), as for any other real run.
        """
        if not demo and _demo_env_enabled():
            demo_record = self.analyze(
                "trading_decision",
                input_data,
                input_id=input_id,
                session_id=session_id,
            )
            demo_record = demo_record.model_copy(
                update={"meta": demo_record.meta.model_copy(update={"demo": True})}
            )
            self._records[input_id] = demo_record
            return demo_record
        if self._tp_mod is None:
            import selene_space_decision_workflow.trading_pipeline as tp

            self._tp_mod = tp
        # Serialized against every other caller of the shared adapter (the
        # heartbeat round, anchored follow-ups): it funnels all coroutines
        # onto one private event loop, so overlapping callers interleave
        # there and leave last_usage ambiguous.
        async with self._llm_lock:
            adapter = await self._get_trading_adapter()
            wf, _ = self._tp_mod.build_trading_workflow(
                llm=adapter,
                persistence=self._persistence,
                symbol=str(input_data.get("symbol", "BTC/USDT")),
                interval=str(input_data.get("interval", "1h")),
                trading_stance=trading_stance,
                htf_summary_data=htf_summary_data,
                direction_signal=direction_signal,
            )
            # wf.analyze blocks on 2 real LLM calls (up to max_retry each) —
            # run it off the event loop like the CLI does (asyncio.to_thread).
            record: AnalysisRecord = await asyncio.to_thread(
                wf.analyze, input_data, input_id=input_id, session_id=session_id
            )
        if demo:
            record = record.model_copy(
                update={"meta": record.meta.model_copy(update={"demo": True})}
            )
        elif record.usage_total:
            # 记真实用量(整个两阶段 + 重试的和)。只记不拦:这条路径既服务
            # 心跳的无人值守诊断(在 _round_body 里另有预算闸门),也服务用户
            # 手动发起的诊断 —— 后者不该被当日上限截断。
            from app.core.llm_usage import get_ledger

            get_ledger().record(
                source="pa_analysis",
                prompt_tokens=int(record.usage_total.get("prompt_tokens", 0) or 0),
                completion_tokens=int(
                    record.usage_total.get("completion_tokens", 0) or 0
                ),
                total_tokens=int(record.usage_total.get("total_tokens", 0) or 0),
            )
        self._records[input_id] = record
        if not demo:
            await self._persist_record(record)
        return record

    async def _persist_record(self, record: AnalysisRecord) -> None:
        """Land a real-LLM record in the v009 analysis_records table (A1).

        Only ``analyze_trading`` (real LLM, 1-3 min + real API cost) calls
        this; the canned ``analyze()`` path stays memory-only by design —
        persisting synthesized answers would pollute the audit table.
        Best-effort: a storage failure must not fail the analysis response
        (the in-memory copy still serves this process).
        """
        repo = await self._get_record_repo()
        if repo is None:
            return
        try:
            await repo.save(record.model_dump(mode="json"))
        except Exception:  # noqa: BLE001 - persistence must not break analysis
            logger.warning(
                "failed to persist analysis record input_id=%s",
                record.meta.input_id,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        tree_id: str,
        input_data: dict[str, Any],
        *,
        input_id: str,
        session_id: str,
    ) -> AnalysisRecord:
        """Run the two-stage workflow for the given tree."""
        wf = self._trees.get(tree_id)
        if wf is None:
            raise KeyError(tree_id)
        # Reset cursor so Stage1 → response[0] and Stage2 → response[1].
        if hasattr(wf.llm, "reset"):
            wf.llm.reset()
        record = wf.analyze(input_data, input_id=input_id, session_id=session_id)
        self._records[input_id] = record
        return record

    async def get_record(self, record_id: str) -> AnalysisRecord | None:
        """Resolve a record by input_id: memory first, then the v009 table.

        ``record_id`` is the caller's input_id (the read-side cache is keyed
        by input_id). After a restart the in-memory dict is empty, so fall
        back to the latest persisted row for that input_id (A1).
        """
        cached = self._records.get(record_id)
        if cached is not None:
            return cached
        repo = await self._get_record_repo()
        if repo is None:
            return None
        payload = await repo.get_by_input_id(record_id)
        if not payload:
            return None
        record = AnalysisRecord.model_validate(payload)
        self._records[record_id] = record
        return record

    async def list_records(
        self,
        symbol: str | None = None,
        interval: str | None = None,
        limit: int = 50,
    ) -> list[AnalysisRecord]:
        """Persisted diagnosis records, newest first, for the review panel.

        Reuses the repo's list_records (deserialized payloads + record_id
        backfilled into meta) — same shapes as the single-record read path,
        so projection stays single-source. Records whose payload no longer
        validates against the current schema are skipped, not fatal.
        """
        repo = await self._get_record_repo()
        if repo is None:
            return []
        payloads = await repo.list_records(symbol=symbol, limit=limit)
        out: list[AnalysisRecord] = []
        for payload in payloads:
            try:
                record = AnalysisRecord.model_validate(payload)
            except Exception:  # noqa: BLE001 — legacy/malformed rows must not kill the list
                continue
            if interval is not None:
                summary = record.input_data.get("summary") or {}
                if summary.get("interval") != interval:
                    continue
            out.append(record)
        return out

    async def attach_backtest(self, input_id: str, payload: dict[str, Any]) -> bool:
        """Persist a backtest result onto the record's backtest_verification.

        The read path (get_record) is cache-first, so after repo.append_payload
        succeeds we must refresh ``self._records[input_id]`` — otherwise the
        attachment lives in the db but the API keeps serving the stale cache.
        """
        repo = await self._get_record_repo()
        if repo is None:
            return False
        try:
            ok = await repo.append_payload(input_id, "backtest_verification", payload)
        except Exception:  # noqa: BLE001 - attachment must not break the caller
            logger.warning(
                "attach_backtest repo error input_id=%s", input_id, exc_info=True
            )
            return False
        if not ok:
            return False
        cached = self._records.get(input_id)
        if cached is not None:
            refreshed = await repo.get_by_input_id(input_id)
            if refreshed is not None:
                self._records[input_id] = AnalysisRecord.model_validate(refreshed)
        return True

    async def record_followup(
        self,
        record_id: str,
        question: str,
        live_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        """Answer one anchored follow-up question against a frozen record.

        ``record_id`` is the caller's input_id (the same key get_record uses).
        The question is never free-floating: it is answered against this
        record's own snapshot, and the Q&A pair is appended to that same
        record so the audit trail stays with the decision it interrogates.

        ``live_context`` (W3 hybrid) — optional freshly-read bar snapshot
        assembled by the endpoint layer. When present it is injected as the
        labeled 【现在读数 · RECONSTRUCTED】 section and the persisted Q&A
        entry is marked ``used_live: true``; the audit trail therefore
        records *which* answers saw data beyond the record. Absent → the
        fully-anchored form, unchanged from Phase D.

        Returns the record's full follow-up list on success (the caller
        renders it directly — no local appending that could drift from what
        was persisted), or None when no record exists. Raises RuntimeError
        when no storage is wired: "we cannot record your question" is not the
        same failure as "unknown record", and the caller asked for something
        that must not be silently dropped.
        """
        record = await self.get_record(record_id)
        if record is None:
            return None
        repo = await self._get_record_repo()
        if repo is None:
            raise RuntimeError(
                "storage not configured — anchored follow-ups cannot be "
                "persisted (wire db_loader in main.py lifespan)"
            )
        adapter = await self._get_trading_adapter()
        messages = [
            LLMMessage(
                role="user",
                content=_build_followup_prompt(record, question, live_context),
            )
        ]
        # Same lock as the heartbeat path: both reach the one cached adapter.
        async with self._llm_lock:
            response = await asyncio.to_thread(adapter.call, messages)
        entry: dict[str, Any] = {
            "question": question,
            "answer": response.content,
            "at": time.time(),
            "usage": dict(getattr(adapter, "last_usage", None) or {}),
            "used_live": live_context is not None,
        }
        if entry["usage"]:
            from app.core.llm_usage import get_ledger

            get_ledger().record(
                source="pa_followup",
                prompt_tokens=int(entry["usage"].get("prompt_tokens", 0) or 0),
                completion_tokens=int(entry["usage"].get("completion_tokens", 0) or 0),
                total_tokens=int(entry["usage"].get("total_tokens", 0) or 0),
            )
        followups: list[dict[str, Any]] | None = await repo.append_followup(
            record_id, entry
        )
        if followups is None:
            return None
        # Re-read rather than patching the pre-call copy: the heartbeat may
        # have attached an outcome to this record while the LLM was running.
        refreshed = await repo.get_by_input_id(record_id)
        if refreshed is not None:
            self._records[record_id] = AnalysisRecord.model_validate(refreshed)
        return followups

    def list_trees(self) -> list[TreeInfo]:
        return list(self._tree_meta.values())

    def get_tree_graph(self, tree_id: str) -> dict[str, Any] | None:
        """Serialize a tree's parsed graph for read-only visualization (B5).

        Single source of truth: the same DecisionTreeGraph the workflow
        validates against, not a re-parse of the txt.
        """
        wf = self._trees.get(tree_id)
        if wf is None:
            return None
        graph: DecisionTreeGraph = wf.parser_graph
        nodes: dict[str, Any] = {}
        for nid, node in graph.nodes.items():
            nodes[nid] = {
                "node_id": node.node_id,
                "section": node.section,
                "question": node.question,
                "node_type": node.node_type,
                "branch_type": node.branch_type,
                "branches": [
                    {
                        "label": b.label,
                        "target_ref": b.target_ref,
                        "terminal_outcome": b.terminal_outcome,
                        "tag": b.tag,
                        "tag_action": b.tag_action,
                    }
                    for b in node.branches
                ],
            }
        return {
            "tree_id": tree_id,
            "entry_node_id": graph.entry_node_id,
            "terminals": sorted(graph.terminals),
            "nodes": nodes,
        }

    # ------------------------------------------------------------------
    # Test helpers (error-path tests override per-tree LLM)
    # ------------------------------------------------------------------

    def set_llm_provider(self, tree_id: str, provider: Any) -> None:
        """Override a tree's LLM provider (test-only). Saves the original for restore."""
        wf = self._trees.get(tree_id)
        if wf is None:
            raise KeyError(tree_id)
        if tree_id not in self._original_llms:
            self._original_llms[tree_id] = wf.llm
        # TwoStageWorkflow.llm is a plain attribute (orchestrator.py:51).
        wf.llm = provider

    def reset_llm_provider(self, tree_id: str) -> None:
        """Restore a tree's original LLM provider (test-only)."""
        wf = self._trees.get(tree_id)
        if wf is None:
            raise KeyError(tree_id)
        if tree_id in self._original_llms:
            wf.llm = self._original_llms[tree_id]
            del self._original_llms[tree_id]

    # ------------------------------------------------------------------
    # Tree loading
    # ------------------------------------------------------------------

    def _load_trees(self) -> None:
        tree_dir = Path(__file__).resolve().parent.parent.parent.parent
        # Navigate to packages/decision-workflow/selene_space_decision_workflow/decision_trees/
        # This service lives at services/local-api/app/core/ — use package-relative path.
        import selene_space_decision_workflow as _pkg

        tree_dir = Path(_pkg.__file__).resolve().parent / "decision_trees"
        if not tree_dir.is_dir():
            logger.warning("decision_trees dir not found: %s", tree_dir)
            return
        for txt in sorted(tree_dir.glob("*.txt")):
            try:
                self._load_one_tree(txt)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to load decision tree %s: %s", txt.name, exc)

    def _load_one_tree(self, txt: Path) -> None:
        tree_text = txt.read_text(encoding="utf-8")
        allowed = self._scan_terminals(tree_text)
        graph = parse_decision_tree(tree_text, allowed_terminals=allowed)
        tree_id = txt.stem
        routing_table = _ROUTING_TABLES.get(tree_id, RoutingTable(entries=()))
        # Per-tree LLM isolation: instantiate inside the loop (R3).
        canned_stage1 = self._synthesize_stage1_response(graph)
        canned_stage2 = self._synthesize_stage2_response(graph, canned_stage1)
        llm = CannedLLMProvider(responses=[canned_stage1, canned_stage2])
        wf = TwoStageWorkflow(
            llm=llm,
            parser_graph=graph,
            validator_config=ValidatorConfig(),
            routing_table=routing_table,
            program_registry=ProgramNodeRegistry(),
            persistence=self._persistence,
            tree_text=tree_text,
        )
        self._trees[tree_id] = wf
        self._tree_meta[tree_id] = TreeInfo(
            tree_id=tree_id,
            title=tree_id.replace("_", " ").title(),
            description=f"Decision tree: {tree_id}",
            node_count=len(graph.nodes),
        )

    # ------------------------------------------------------------------
    # Synthesis helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _scan_terminals(tree_text: str) -> frozenset[str]:
        """Pre-scan for '是/否/中性：终止 → X' patterns to build allowed_terminals."""
        matches = _TERMINAL_RE.findall(tree_text)
        return frozenset(matches) if matches else frozenset()

    @staticmethod
    def _walk_graph(
        graph: DecisionTreeGraph,
    ) -> list[tuple[DecisionTreeNode, str, str]]:
        """Walk from entry, picking the first 是-labeled branch at each node.

        Returns a list of (node, branch_label, answer) tuples where answer is
        the terminal_outcome of the final branch (or "unknown" if no terminal).
        Stops at the first terminal encountered.
        """
        visited: list[tuple[DecisionTreeNode, str, str]] = []
        seen_ids: set[str] = set()
        current_id: str | None = graph.entry_node_id
        terminal = "unknown"
        while current_id is not None and current_id in graph.nodes:
            if current_id in seen_ids:
                break  # cycle guard
            seen_ids.add(current_id)
            node = graph.nodes[current_id]
            # Pick first branch whose label starts with "是" (plain or multi-form).
            chosen = None
            for b in node.branches:
                if b.label.startswith("是"):
                    chosen = b
                    break
            if chosen is None:
                # No 是 branch — take the first branch as fallback.
                chosen = node.branches[0] if node.branches else None
            if chosen is None:
                break
            visited.append((node, chosen.label, terminal))
            if chosen.terminal_outcome is not None:
                terminal = chosen.terminal_outcome
                break
            current_id = chosen.target_ref
        # Backfill the terminal into all visited entries (they share the final terminal).
        return [(n, lbl, terminal) for n, lbl, _ in visited]

    @staticmethod
    def _synthesize_stage1_response(graph: DecisionTreeGraph) -> str:
        """Build a canned Stage1Result JSON that passes 3-layer validation."""
        walked = DecisionWorkflowService._walk_graph(graph)
        terminal = walked[-1][2] if walked else "unknown"
        # gate_result = "proceed" if terminal is an approval-like outcome, else "wait".
        # Routing table keys map to states like "normal" / "clear" / "high_risk".
        # For a demo can, we map terminal → state conservatively.
        state_map = {
            "approve": "normal",
            "clear": "clear",
            "trade": "normal",
            "proceed": "normal",
            "wait": "unknown",
            "reject": "high_risk",
            "high_risk": "high_risk",
            "extreme_risk": "high_risk",
            "suspicious": "suspicious",
            "malicious": "suspicious",
            "manual_review": "unknown",
        }
        state = state_map.get(terminal, "unknown")
        # gate_result = "proceed" only if state is known (non-unknown).
        gate_result = "proceed" if state != "unknown" else "wait"
        trace = [
            NodeAnswer(
                node_id=node.node_id,
                question=node.question,
                answer=lbl,
                bar_range=_BAR_RANGE,
                reason=_REASON,
                branch=lbl,
                program_prefill_nodes=[],
            ).model_dump()
            for node, lbl, _ in walked
        ]
        diagnosis_summary = {"state": state, "terminal": terminal}
        payload = {
            "state": state,
            "gate_result": gate_result,
            "trace": trace,
            "diagnosis_summary": diagnosis_summary,
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _synthesize_stage2_response(graph: DecisionTreeGraph, stage1_json: str) -> str:
        """Build a canned Stage2Result JSON.

        CRITICAL (R3): Stage2 must use the SAME bar_range as Stage1 to avoid
        cross-stage cheat detection (orchestrator.py:329-333).
        """
        stage1 = json.loads(stage1_json)
        # Stage 2 decision_summary MUST carry diagnosis_summary unchanged
        # (validator.py:289-302 cross-stage cheat check).
        diagnosis_summary = stage1.get("diagnosis_summary", {})
        terminal = diagnosis_summary.get("terminal", "wait")
        # Map terminal → valid terminal_outcome (trade/wait/reject/proceed).
        outcome_map = {
            "approve": "proceed",
            "clear": "proceed",
            "trade": "trade",
            "proceed": "proceed",
            "wait": "wait",
            "reject": "reject",
            "high_risk": "reject",
            "extreme_risk": "reject",
            "suspicious": "reject",
            "malicious": "reject",
            "manual_review": "wait",
        }
        terminal_outcome = outcome_map.get(terminal, "wait")
        decision = terminal_outcome  # decision mirrors terminal_outcome for the demo.
        walked = DecisionWorkflowService._walk_graph(graph)
        trace = [
            NodeAnswer(
                node_id=node.node_id,
                question=node.question,
                answer=lbl,
                bar_range=_BAR_RANGE,
                reason=_REASON,
                branch=lbl,
                program_prefill_nodes=[],
            ).model_dump()
            for node, lbl, _ in walked
        ]
        decision_summary: dict[str, Any] = {
            "decision": decision,
            "diagnosis_summary": diagnosis_summary,
        }
        # Validator requires reject_reason when decision=reject (validator.py:176-188).
        if decision == "reject":
            decision_summary["reject_reason"] = (
                f"terminal '{terminal}' mapped to reject per synthesized canned response"
            )
        payload = {
            "decision": decision,
            "terminal_outcome": terminal_outcome,
            "order": None,
            "trace": trace,
            "decision_summary": decision_summary,
        }
        return json.dumps(payload, ensure_ascii=False)

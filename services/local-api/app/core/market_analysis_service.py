"""两阶段行情分析服务（US-008）。

聚合 MarketAnalysisFlow + SQLite 落盘 + 后台任务注册表（提交 / 查询 / 取消）。
LLM 配置与 db 均延迟解析：db 未接线或 LLM 未配置时不阻塞应用启动，
首次分析时才构建编排器（并复用 httpx client）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from selene_space_llm.base import LLMConfig

from selene_space_market_analysis.experience_library import ExperienceLibrary
from selene_space_market_analysis.flow import MarketAnalysisFlow
from selene_space_market_analysis.llm_provider import HttpLLMProvider
from selene_space_market_analysis.persistence_sqlite import SqliteRecordPersistence
from selene_space_storage.repositories.analysis_records import AnalysisRecordRepository

logger = logging.getLogger(__name__)

_JOB_RUNNING = "running"
_JOB_DONE = "done"
_JOB_FAILED = "failed"
_JOB_CANCELLED = "cancelled"


class MarketAnalysisService:
    """编排器门面：同步分析 + 后台任务（submit/get/cancel）。"""

    def __init__(
        self,
        *,
        db_loader: Callable[[], Any],
        settings_loader: Callable[[], Any],
        flow: MarketAnalysisFlow | None = None,
    ) -> None:
        self._db_loader = db_loader
        self._settings_loader = settings_loader
        self._flow = flow
        self._jobs: dict[str, dict[str, Any]] = {}
        # US-302: 延迟构建的 LLM / 持久化 / 会话服务（复用编排器配置面）。
        self._llm: HttpLLMProvider | None = None
        self._persistence: SqliteRecordPersistence | None = None
        self._chat_service: Any = None

    # ------------------------------------------------------------------
    # 编排器构建（延迟）
    # ------------------------------------------------------------------

    async def _resolve_llm_config(self) -> LLMConfig:
        settings = self._settings_loader()
        cfg = await settings.resolve_llm_full_config()
        return LLMConfig(
            model=cfg.get("model") or "default",
            api_key=cfg.get("api_key") or "",
            base_url=cfg.get("base_url") or None,
        )

    async def _get_flow(self) -> MarketAnalysisFlow:
        """首次调用时用真实 LLM 配置 + SQLite 落盘构建编排器。"""
        if self._flow is not None:
            return self._flow
        db = self._db_loader()
        if db is None:
            raise RuntimeError("storage db not available")
        llm_config = await self._resolve_llm_config()
        self._llm = HttpLLMProvider(llm_config)
        self._persistence = SqliteRecordPersistence(db)
        self._flow = MarketAnalysisFlow(
            llm=self._llm,
            persistence=self._persistence,
            experience_library=self._build_experience_library(db),
        )
        return self._flow

    @staticmethod
    def _build_experience_library(db: Any) -> ExperienceLibrary | None:
        """经验库构建失败不得拖垮分析主路径（降级为不注入历史案例）。"""
        try:
            return ExperienceLibrary(AnalysisRecordRepository(db))
        except Exception as exc:  # noqa: BLE001
            logger.debug("experience library unavailable: %s", exc)
            return None

    # ------------------------------------------------------------------
    # 同步分析
    # ------------------------------------------------------------------

    async def analyze(
        self,
        input_data: dict[str, Any],
        *,
        input_id: str,
        session_id: str,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        trade_temperament: str | None = None,
        incremental: bool = False,
        history_n: int = 0,
    ) -> dict[str, Any]:
        """两阶段分析（支持 SSE 事件回调 / 交易倾向入参 / 增量复用上次结论）。

        ``incremental=True`` 时查找最近同 symbol+interval 的成功记录作为先验，
        无记录时退化为完整分析（不报错）。

        ``history_n>0`` 时查找最近同 symbol+interval 的 N 条记录（不分成败），
        注入 stage1 prompt 供 LLM 纵向对比（仅参考）。
        """
        flow = await self._get_flow()
        record: dict[str, Any]
        if incremental:
            prev = await self._find_prev_record(input_data)
            record = await flow.analyze_incremental(
                input_data,
                prev_record=prev,
                input_id=input_id,
                session_id=session_id,
                on_event=on_event,
                trade_temperament=trade_temperament,
                history_n=history_n,
            )
        else:
            record = await flow.analyze(
                input_data,
                input_id=input_id,
                session_id=session_id,
                on_event=on_event,
                trade_temperament=trade_temperament,
                history_n=history_n,
            )
        return record

    async def _find_prev_record(
        self, input_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """最近同 symbol+interval 的成功记录（增量先验）；无则 None。"""
        symbol = input_data.get("symbol")
        interval = input_data.get("interval")
        if not symbol:
            return None
        persistence = self._persistence
        if persistence is None:
            await self._get_flow()
            persistence = self._persistence
        if persistence is None:
            return None
        records: list[dict[str, Any]] = await persistence.list_recent(
            symbol=symbol, limit=50
        )
        for rec in records:
            if "exception" in rec:
                continue
            if (rec.get("input_data") or {}).get("interval") == interval:
                return rec
        return None

    # ------------------------------------------------------------------
    # Free Chat（US-302）
    # ------------------------------------------------------------------

    async def _ensure_chat_service(self) -> Any:
        """延迟构建 ChatService：复用编排器的 LLM provider 与 SQLite 落盘。"""
        if self._chat_service is None:
            await self._get_flow()  # 确保 _llm / _persistence 已就绪
            if self._llm is None or self._persistence is None:
                raise RuntimeError(
                    "chat service requires lazily-built llm/persistence "
                    "(flow must not be injected)"
                )
            from selene_space_market_analysis.chat_service import ChatService

            self._chat_service = ChatService(
                llm=self._llm,
                persistence=self._persistence,
            )
        return self._chat_service

    async def chat(self, record_id: str, message: str) -> dict[str, Any]:
        """以记录为种子的自由追问：注入最新 K 线快照，回复并落盘 jsonl。"""
        svc = await self._ensure_chat_service()
        reply: dict[str, Any] = await svc.send(record_id, message)
        return reply

    async def chat_history(self, record_id: str) -> dict[str, Any]:
        """读回某记录的聊天历史（不触发 LLM）。"""
        svc = await self._ensure_chat_service()
        return {"record_id": record_id, "messages": svc.history(record_id)}

    # ------------------------------------------------------------------
    # 演示回放（US-304，复用 list_recent）
    # ------------------------------------------------------------------

    async def list_records(
        self, *, symbol: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """最近完整记录（倒序），供演示回放列表。"""
        await self._get_flow()
        if self._persistence is None:
            return []
        records: list[dict[str, Any]] = await self._persistence.list_recent(
            symbol=symbol, limit=limit
        )
        return records

    async def get_record(self, record_id: str) -> dict[str, Any] | None:
        """按 id 取回完整记录（演示回放 / CSV 导出 / 通知）。"""
        await self._get_flow()
        if self._persistence is None:
            return None
        record: dict[str, Any] | None = await self._persistence.get(record_id)
        return record

    # ------------------------------------------------------------------
    # 后台任务（提交 / 查询 / 取消）
    # ------------------------------------------------------------------

    def submit(
        self,
        input_data: dict[str, Any],
        *,
        input_id: str,
        session_id: str,
    ) -> str:
        """提交后台分析任务，立即返回 job_id。"""
        job_id = f"ma-{uuid.uuid4().hex[:12]}"
        task = asyncio.create_task(
            self._run_job(job_id, input_data, input_id=input_id, session_id=session_id)
        )
        self._jobs[job_id] = {
            "status": _JOB_RUNNING,
            "task": task,
            "result": None,
            "error": None,
        }
        return job_id

    async def _run_job(
        self,
        job_id: str,
        input_data: dict[str, Any],
        *,
        input_id: str,
        session_id: str,
    ) -> None:
        entry = self._jobs[job_id]
        try:
            record = await self.analyze(
                input_data, input_id=input_id, session_id=session_id
            )
            entry["result"] = record
            entry["status"] = _JOB_DONE
        except asyncio.CancelledError:
            entry["status"] = _JOB_CANCELLED
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("market-analysis job %s failed: %s", job_id, exc)
            entry["status"] = _JOB_FAILED
            entry["error"] = {"type": type(exc).__name__, "message": str(exc)}

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        entry = self._jobs.get(job_id)
        if entry is None:
            return None
        return {
            "job_id": job_id,
            "status": entry["status"],
            "result": entry["result"],
            "error": entry["error"],
        }

    def cancel(self, job_id: str) -> bool:
        """取消运行中的后台任务。返回是否发出取消。"""
        entry = self._jobs.get(job_id)
        if entry is None or entry["status"] != _JOB_RUNNING:
            return False
        entry["task"].cancel()
        return True


__all__ = ["MarketAnalysisService"]

"""Free Chat 自由追问服务（US-302）。

以分析记录（record_id）为会话种子，每轮注入最新 K 线快照，聊天记录独立
jsonl 落盘（data/chat_logs/），不写 analysis_records（防 schema 迁移 +
审计混流）。Prompt 由 selene 自撰，不含任何参考实现 prompt 文本。

合规口径见包内 PROVENANCE.md：独立重实现 + 无复制声明。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .data_source import KlineSource
from .llm_provider import HttpLLMProvider, LLMMessage

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_CHAT_LOG_DIR = _PROJECT_ROOT / "data" / "chat_logs"

# 单轮快照 token 预算硬顶：K 线快照超限时逐根丢最旧（保守估算，见 _estimate_tokens）。
SNAPSHOT_TOKEN_CAP = 1800
# 会话上下文保留轮数上限（超出丢最旧，防上下文膨胀）。
MAX_HISTORY_TURNS = 12
# 单条用户消息最大长度（防 prompt 滥用）。
MAX_MESSAGE_CHARS = 4000

_SYSTEM_PROMPT = (
    "你是一个严谨的行情分析对话助手。基于给定的两阶段分析记录与最新 K 线快照，"
    "用中文回答用户的追问。请保持谨慎：市场存在不确定性，回答仅供参考，不构成投资建议。"
    "若所需信息不在上下文中，如实说明，不要编造。"
)


def _estimate_tokens(text: str) -> int:
    """保守 token 估算：CJK 每字符 1 token，其余按 4 字符/token（宁可高估）。"""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatLogStore:
    """聊天记录 jsonl 落盘（每个 record_id 一个文件）。"""

    def __init__(self, log_dir: str | Path | None = None) -> None:
        self._dir = Path(log_dir) if log_dir else DEFAULT_CHAT_LOG_DIR

    @property
    def log_dir(self) -> Path:
        return self._dir

    def _path(self, record_id: str) -> Path:
        # record_id 形如 ar-xxxx；隔离目录避免与 analysis_records 混流。
        safe = record_id.replace("/", "_").replace("..", "_")
        return self._dir / f"{safe}.jsonl"

    def load(self, record_id: str) -> list[dict[str, Any]]:
        """读回历史消息（损坏行跳过，重启可续）。"""
        path = self._path(record_id)
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("chat_log 损坏行跳过: %s", record_id)
        except OSError:
            logger.warning("chat_log 读取失败: %s", record_id)
        return out

    def append(self, record_id: str, entry: dict[str, Any]) -> None:
        """追加一条消息（原子 append）。"""
        path = self._path(record_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("chat_log 写入失败: %s", record_id)


class ChatService:
    """以分析记录为种子的自由追问会话服务（US-302）。

    - record_id 为会话种子：加载完整记录（含 stage1/stage2 摘要）
    - 每轮注入最新 K 线快照（实时重载，受 token 预算截断）
    - 聊天记录 jsonl 独立落盘，不写 analysis_records
    - prompt 自撰，系统提示词固定、用户消息含预算上限
    """

    def __init__(
        self,
        *,
        llm: HttpLLMProvider,
        persistence: Any,
        kline_source: KlineSource | None = None,
        log_store: ChatLogStore | None = None,
        snapshot_cap: int = SNAPSHOT_TOKEN_CAP,
        max_history_turns: int = MAX_HISTORY_TURNS,
        max_message_chars: int = MAX_MESSAGE_CHARS,
    ) -> None:
        self._llm = llm
        self._persistence = persistence
        self._kline_source = kline_source or KlineSource()
        self._log_store = log_store or ChatLogStore()
        self._snapshot_cap = snapshot_cap
        self._max_history_turns = max_history_turns
        self._max_message_chars = max_message_chars

    async def send(self, record_id: str, message: str) -> dict[str, Any]:
        """发送一轮追问，返回助手回复并落盘（user + assistant 两条）。"""
        record = await self._persistence.get(record_id)
        if record is None:
            raise KeyError(f"record '{record_id}' not found")
        if not message or not message.strip():
            raise ValueError("empty message")
        if len(message) > self._max_message_chars:
            raise ValueError(f"message too long (>{self._max_message_chars} chars)")

        history = self._log_store.load(record_id)
        seed = self._build_seed_context(record)
        snapshot = self._build_snapshot(record)
        user_prompt = self._build_user_prompt(seed, snapshot, history, message)

        response = await self._llm.call_async(
            [
                LLMMessage(role="system", content=_SYSTEM_PROMPT),
                LLMMessage(role="user", content=user_prompt),
            ]
        )

        now = _now_iso()
        self._log_store.append(
            record_id, {"role": "user", "content": message, "ts": now}
        )
        self._log_store.append(
            record_id, {"role": "assistant", "content": response.content, "ts": now}
        )
        return {
            "record_id": record_id,
            "reply": response.content,
            "messages": self._log_store.load(record_id),
        }

    def history(self, record_id: str) -> list[dict[str, Any]]:
        """返回该会话全部聊天消息（不触发 LLM）。"""
        return self._log_store.load(record_id)

    # ------------------------------------------------------------------
    # Prompt 组装
    # ------------------------------------------------------------------

    def _build_seed_context(self, record: dict[str, Any]) -> dict[str, Any]:
        """从完整记录抽取会话种子摘要（避免整条记录膨胀 prompt）。"""
        input_data = record.get("input_data") or {}
        stage1 = record.get("stage1") or {}
        stage2 = record.get("stage2") or {}
        return {
            "symbol": input_data.get("symbol"),
            "interval": input_data.get("interval"),
            "exchange": input_data.get("exchange"),
            "market_type": input_data.get("market_type"),
            "market_regime": stage1.get("market_regime"),
            "entry_gate": stage1.get("entry_gate"),
            "signal_patterns": stage1.get("signal_patterns"),
            "diagnosis_summary": stage1.get("diagnosis_summary"),
            "decision": stage2.get("decision"),
            "decision_summary": stage2.get("decision_summary"),
            "record_id": (record.get("meta") or {}).get("record_id"),
        }

    def _build_snapshot(self, record: dict[str, Any]) -> str:
        """最新 K 线快照（实时重载 + token 预算截断，超限丢最旧）。"""
        input_data = record.get("input_data") or {}
        exchange = input_data.get("exchange") or "binance"
        market_type = input_data.get("market_type") or "futures"
        symbol = input_data.get("symbol") or ""
        interval = input_data.get("interval") or "1h"
        if not symbol:
            return "（无 K 线数据）"
        bars = self._kline_source.build_input_data(
            exchange, market_type, symbol, interval, max_klines=30
        )
        klines = bars["klines"]
        text = json.dumps(
            {
                "symbol": symbol,
                "interval": interval,
                "klines": klines,
                "bar_features": bars["bar_features"],
            },
            ensure_ascii=False,
        )
        while _estimate_tokens(text) > self._snapshot_cap and len(klines) > 1:
            klines = klines[1:]
            text = json.dumps(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "klines": klines,
                    "bar_features": bars["bar_features"],
                },
                ensure_ascii=False,
            )
        return text

    def _build_user_prompt(
        self,
        seed: dict[str, Any],
        snapshot: str,
        history: list[dict[str, Any]],
        message: str,
    ) -> str:
        parts = [
            "【分析记录摘要】",
            json.dumps(seed, ensure_ascii=False, indent=2),
            "",
            "【最新 K 线快照】",
            snapshot,
            "",
        ]
        recent = history[-self._max_history_turns * 2 :]  # 每轮 2 条
        if recent:
            parts.append("【对话历史】")
            for entry in recent:
                parts.append(f"{entry.get('role')}: {entry.get('content')}")
            parts.append("")
        parts += [
            "【用户追问】",
            message,
            "",
            "请用中文直接回答。",
        ]
        return "\n".join(parts)


__all__ = [
    "ChatLogStore",
    "ChatService",
    "DEFAULT_CHAT_LOG_DIR",
    "MAX_HISTORY_TURNS",
    "SNAPSHOT_TOKEN_CAP",
]

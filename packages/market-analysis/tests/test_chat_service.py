"""US-302 — Free Chat 自由追问单测（假 provider，不依赖网络/DB）。

覆盖验收标准：
- record_id 为会话种子：记录不存在 → KeyError；空消息/超长消息 → ValueError
- 每轮注入最新 K 线快照：受 token 预算截断（丢最旧 bar）
- 回复落盘 jsonl（user + assistant 两条），history() 可读回
- integration marker：真实链路（复用 HttpLLMProvider + SqliteRecordPersistence），
  无 key / 无 klines.db 时 skip
测试不使用任何参考实现字段名。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from selene_space_llm.base import LLMConfig

from selene_space_market_analysis.chat_service import (
    ChatLogStore,
    ChatService,
    _estimate_tokens,
)
from selene_space_market_analysis.data_source import DEFAULT_KLINES_DB, KlineSource
from selene_space_market_analysis.flow import MarketAnalysisFlow
from selene_space_market_analysis.llm_provider import HttpLLMProvider, LLMMessage, LLMResponse
from selene_space_market_analysis.persistence_sqlite import SqliteRecordPersistence
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager


def _bar(i: int) -> dict:
    return {
        "timestamp": 1_700_000_000 + i * 3600,
        "open": 60_000 + i,
        "high": 60_010 + i,
        "low": 59_990 + i,
        "close": 60_005 + i,
        "volume": 100 + i,
    }


def _record(record_id: str = "ar-test1") -> dict:
    return {
        "meta": {"record_id": record_id, "input_id": "in1", "session_id": "s1"},
        "input_data": {
            "exchange": "binance",
            "market_type": "futures",
            "symbol": "BTC/USDT",
            "interval": "1h",
            "klines": [_bar(i) for i in range(10)],
        },
        "stage1": {
            "market_regime": "trend_up",
            "entry_gate": "proceed",
            "signal_patterns": [{"name": "trend-follow", "bias": "bullish", "confidence": 0.8}],
            "evidence": [{"kind": "kline", "key": "higher-highs", "statement": "close rising"}],
            "diagnosis_summary": {"bias": "bullish"},
            "bar_features": {},
            "trend_context": {},
        },
        "stage2": {
            "decision": "market",
            "dispatch_playbooks": [],
            "evidence": [],
            "decision_summary": {"bias": "bullish"},
            "trade_setup": {
                "direction": "long",
                "entry_price": 100.0,
                "stop_loss_price": 95.0,
                "take_profit_price": 110.0,
                "risk_reward_ratio": 2.0,
                "estimated_win_rate": 0.5,
                "confidence": 0.7,
                "trader_equation_passed": True,
                "decision_path": ["gate:proceed", "趋势确认", "入场", "止损设置"],
            },
        },
    }


class _FakeLLM:
    """记录最后收到的 messages，返回固定回复。"""

    def __init__(self, reply: str = "ok") -> None:
        self._reply = reply
        self.last_messages: list[LLMMessage] | None = None

    async def call_async(self, messages: list[LLMMessage]) -> LLMResponse:
        self.last_messages = messages
        return LLMResponse(content=self._reply, reasoning_content=None, usage={})


class _FakePersistence:
    def __init__(self, record: dict) -> None:
        self._record = record

    async def get(self, record_id: str) -> dict | None:
        return self._record if record_id == self._record["meta"]["record_id"] else None


class _FakeKlineSource:
    """固定返回 n 根 K 线；记录 build_input_data 收到的参数。"""

    def __init__(self, n_bars: int = 30) -> None:
        self.bars = [_bar(i) for i in range(n_bars)]
        self.last_args: tuple = ()

    def build_input_data(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        max_klines: int = 30,
    ) -> dict:
        self.last_args = (exchange, market_type, symbol, interval)
        return {"klines": self.bars[:max_klines], "bar_features": {}}


@pytest.fixture
def service(tmp_path: Path) -> tuple[ChatService, _FakeLLM]:
    llm = _FakeLLM(reply="已收到追问")
    svc = ChatService(
        llm=llm,  # type: ignore[arg-type]
        persistence=_FakePersistence(_record()),
        kline_source=_FakeKlineSource(),  # type: ignore[arg-type]
        log_store=ChatLogStore(tmp_path),
    )
    return svc, llm


def test_estimate_tokens_cjk_and_ascii() -> None:
    assert _estimate_tokens("中") == 1
    assert _estimate_tokens("abcd") == 1
    assert _estimate_tokens("中文ab") == 3  # 2 CJK + 2 ascii → 2 + 1


@pytest.mark.asyncio
async def test_send_replies_and_persists_two_entries(service: tuple[ChatService, _FakeLLM]) -> None:
    svc, llm = service
    resp = await svc.send("ar-test1", "为什么看多？")

    assert resp["record_id"] == "ar-test1"
    assert resp["reply"] == "已收到追问"
    assert [m["role"] for m in resp["messages"]] == ["user", "assistant"]
    # 落盘 jsonl 可读回
    assert svc.history("ar-test1") == resp["messages"]
    # user 消息体包含【用户追问】
    user_msg = next(m.content for m in llm.last_messages if m.role == "user")
    assert "为什么看多？" in user_msg
    assert "【最新 K 线快照】" in user_msg
    assert "【分析记录摘要】" in user_msg


@pytest.mark.asyncio
async def test_send_injects_latest_kline_snapshot(service: tuple[ChatService, _FakeLLM]) -> None:
    svc, _ = service
    await svc.send("ar-test1", "有什么信号")
    # 快照块是【最新 K 线快照】标题后的下一行单行 JSON
    user_msg = next(m.content for m in svc._llm.last_messages if m.role == "user")  # type: ignore[union-attr]
    snapshot = user_msg.split("【最新 K 线快照】\n", 1)[1].split("\n", 1)[0]
    parsed = json.loads(snapshot)
    assert parsed["symbol"] == "BTC/USDT"
    assert parsed["interval"] == "1h"
    # 快照实时重载（假源固定 30 根，max_klines=30），未超预算时不截断
    assert len(parsed["klines"]) == 30


@pytest.mark.asyncio
async def test_snapshot_truncates_to_token_budget(tmp_path: Path) -> None:
    """快照超预算时丢最旧 bar，直到 _estimate_tokens <= cap。"""
    llm = _FakeLLM()
    svc = ChatService(
        llm=llm,  # type: ignore[arg-type]
        persistence=_FakePersistence(_record()),
        kline_source=_FakeKlineSource(n_bars=30),  # type: ignore[arg-type]
        log_store=ChatLogStore(tmp_path),
        snapshot_cap=80,
    )
    await svc.send("ar-test1", "hi")
    user_msg = next(m.content for m in llm.last_messages if m.role == "user")
    snapshot = user_msg.split("【最新 K 线快照】\n", 1)[1].split("\n", 1)[0]
    parsed = json.loads(snapshot)
    assert 0 < len(parsed["klines"]) < 30, "超预算快照应被截断"
    assert _estimate_tokens(snapshot) <= 80


@pytest.mark.asyncio
async def test_send_unknown_record_raises_keyerror(tmp_path: Path) -> None:
    svc = ChatService(
        llm=_FakeLLM(),  # type: ignore[arg-type]
        persistence=_FakePersistence(_record()),
        kline_source=_FakeKlineSource(),  # type: ignore[arg-type]
        log_store=ChatLogStore(tmp_path),
    )
    with pytest.raises(KeyError):
        await svc.send("ar-missing", "hi")


@pytest.mark.asyncio
async def test_send_empty_message_raises_valueerror(tmp_path: Path) -> None:
    svc = ChatService(
        llm=_FakeLLM(),  # type: ignore[arg-type]
        persistence=_FakePersistence(_record()),
        kline_source=_FakeKlineSource(),  # type: ignore[arg-type]
        log_store=ChatLogStore(tmp_path),
    )
    with pytest.raises(ValueError):
        await svc.send("ar-test1", "   ")


@pytest.mark.asyncio
async def test_send_too_long_message_raises_valueerror(tmp_path: Path) -> None:
    svc = ChatService(
        llm=_FakeLLM(),  # type: ignore[arg-type]
        persistence=_FakePersistence(_record()),
        kline_source=_FakeKlineSource(),  # type: ignore[arg-type]
        log_store=ChatLogStore(tmp_path),
        max_message_chars=16,
    )
    with pytest.raises(ValueError):
        await svc.send("ar-test1", "x" * 17)


@pytest.mark.asyncio
async def test_history_returns_persisted_messages(service: tuple[ChatService, _FakeLLM]) -> None:
    svc, _ = service
    await svc.send("ar-test1", "round one")
    history = svc.history("ar-test1")
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"


# ---------------------------------------------------------------------------
# integration：真实链路（复用编排器配置面），无 key / 无 DB 时 skip
# ---------------------------------------------------------------------------

_TEST_KEY_ENV = "SELENE_MA_TEST_API_KEY"
_PROBE = ("binance", "futures", "BTC/USDT", "1h")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_chat_with_real_provider() -> None:
    api_key = os.environ.get(_TEST_KEY_ENV)
    if not api_key:
        pytest.skip(f"no {_TEST_KEY_ENV} set — set to run real-LLM integration")
    if not DEFAULT_KLINES_DB.exists():
        pytest.skip("klines.db not present at services/local-api/data")

    input_data = KlineSource().build_input_data(*_PROBE)
    assert input_data["klines"], "probe symbol should yield bars"

    llm = HttpLLMProvider(LLMConfig(model="default", api_key=api_key, base_url=None))
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        persistence = SqliteRecordPersistence(db)
        flow = MarketAnalysisFlow(llm=llm, persistence=persistence)
        record = await flow.analyze(input_data, input_id="integration-chat", session_id="chat-a")
        rid = record["meta"]["record_id"]

        svc = ChatService(llm=llm, persistence=persistence, kline_source=KlineSource())
        resp = await svc.send(rid, "用一句话总结当前市场状态")
        assert resp["reply"]
        assert len(resp["messages"]) == 2
        assert svc.history(rid) == resp["messages"]

"""Agent chat endpoint: SSE envelope, session continuity, workspace, parsing."""

from __future__ import annotations

import json
from decimal import Decimal

import app.api.v1.agent as agent_mod
import pytest
from app.core.dependencies import get_agent_runtime, get_settings_service
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from selene_space_types.trading import MarginMode, OrderSide, Position, PositionSide


@pytest.fixture(autouse=True)
def _wipe_sessions():
    agent_mod._session_contexts.clear()
    agent_mod._active_turns.clear()
    yield
    agent_mod._session_contexts.clear()
    agent_mod._active_turns.clear()


def _app(runtime=None):
    app = create_app()
    app.dependency_overrides[get_agent_runtime] = lambda: runtime
    if runtime is None:
        app.dependency_overrides[get_settings_service] = lambda: None
    return app


async def _chat(message: str, extra: dict | None = None):
    transport = ASGITransport(app=_app(None))
    payload = {"message": message, **(extra or {})}
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.post("/api/v1/agent/chat", json=payload)


def _data_events(text: str) -> list[dict]:
    return [
        json.loads(line[6:]) for line in text.split("\n") if line.startswith("data: ")
    ]


@pytest.mark.asyncio
async def test_chat_streams_sse_with_session_header():
    resp = await _chat("Hello")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert "X-Session-ID" in resp.headers


@pytest.mark.asyncio
async def test_stream_starts_with_text_and_ends_with_done():
    resp = await _chat("Hi there")
    events = _data_events(resp.text)
    assert len(events) > 0
    # Last event should be "done"
    assert events[-1]["type"] == "done"
    # Earlier events should be "text_delta"
    assert events[0]["type"] == "text_delta"


@pytest.mark.asyncio
async def test_session_id_is_reusable_across_messages():
    first = await _chat("First")
    session_id = first.headers["x-session-id"]

    second = await _chat("Second", {"session_id": session_id})
    assert second.headers["x-session-id"] == session_id


@pytest.mark.asyncio
async def test_backtest_explain_context_still_streams():
    context_payload = {
        "intent": "backtest_explain",
        "backtest_id": "bt-123",
        "strategy_id": "strat-123",
        "strategy_name": "MACD Momentum",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "metrics": {
            "total_return": 0.12,
            "sharpe_ratio": 1.8,
            "max_drawdown": -0.08,
            "win_rate": 0.55,
            "profit_factor": 1.6,
            "total_trades": 42,
        },
    }
    resp = await _chat("Explain this backtest", {"context": context_payload})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    events = _data_events(resp.text)
    assert events[-1]["type"] == "done"


# --- trading workspace -------------------------------------------------------


def _make_position() -> Position:
    return Position(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        pos_side=PositionSide.LONG,
        quantity=Decimal("1.5"),
        quantity_base=Decimal("0.15"),
        avg_entry_price=Decimal("65000.0"),
        leverage=10,
        margin_mode=MarginMode.CROSS,
        margin=Decimal("1000.0"),
        unrealized_pnl=Decimal("150.0"),
        opened_at=1711000000000,
        updated_at=1711000050000,
    )


@pytest.mark.asyncio
async def test_workspace_pulls_positions_from_engine(monkeypatch):
    """F15 regression: positions come from engine.get_positions(), non-empty."""
    from app.core import dependencies

    class FakeAccount:
        id = "acc-1"

    class FakeAccountManager:
        def list_accounts(self):
            return [FakeAccount()]

    class FakeEngine:
        def __init__(self) -> None:
            self.called_account: str | None = None

        async def get_positions(self, account_id: str):
            self.called_account = account_id
            return [_make_position()]

    fake_engine = FakeEngine()
    monkeypatch.setattr(dependencies, "get_execution_engine", lambda: fake_engine)
    monkeypatch.setattr(
        dependencies, "get_paper_account_manager", lambda: FakeAccountManager()
    )

    transport = ASGITransport(app=_app(None))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/agent/trading/workspace")
    assert resp.status_code == 200
    body = resp.json()
    assert body[
        "positions"
    ], "positions should be non-empty when engine has an open position"
    assert isinstance(body["positions"], list)
    assert isinstance(body["positions"][0], dict)
    assert body["positions"][0]["symbol"] == "BTC-USDT-SWAP"
    assert body["positions"][0]["pos_side"] == "long"
    assert fake_engine.called_account == "acc-1"


@pytest.mark.asyncio
async def test_workspace_falls_back_to_position_manager(monkeypatch):
    """Fallback path: engine None -> paper PositionManager.get_open_positions."""
    from app.core import dependencies

    class FakePositionManager:
        def get_open_positions(self, account_id: str):
            return [_make_position()]

    monkeypatch.setattr(dependencies, "get_execution_engine", lambda: None)
    monkeypatch.setattr(
        dependencies, "get_paper_position_manager", lambda: FakePositionManager()
    )

    transport = ASGITransport(app=_app(None))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/agent/trading/workspace")
    assert resp.status_code == 200
    body = resp.json()
    assert body["positions"]
    assert body["positions"][0]["symbol"] == "BTC-USDT-SWAP"
    assert isinstance(body["positions"][0], dict)


@pytest.mark.asyncio
async def test_workspace_empty_when_nothing_configured(monkeypatch):
    """No engine and no position manager -> positions stays empty, no crash."""
    from app.core import dependencies

    monkeypatch.setattr(dependencies, "get_execution_engine", lambda: None)
    monkeypatch.setattr(dependencies, "get_paper_position_manager", lambda: None)

    transport = ASGITransport(app=_app(None))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/agent/trading/workspace")
    assert resp.status_code == 200
    assert resp.json()["positions"] == []


# --- parse_decision_block / _fetch_analysis_klines (kline_price_action) ------


class TestParseDecisionBlock:
    def test_reads_fenced_json(self) -> None:
        fenced = (
            "分析文本...\n"
            "```json\n"
            '{"direction": "long", "entry": 60000, "stop_loss": 58000, '
            '"take_profit": 64000, "confidence": 0.7, "reason": "突破"}\n'
            "```"
        )
        d = agent_mod.parse_decision_block(fenced)
        assert d is not None
        assert d["direction"] == "long"
        assert d["entry"] == 60000
        assert d["stop_loss"] == 58000
        assert d["take_profit"] == 64000
        assert d["confidence"] == 0.7

    def test_last_valid_block_wins(self) -> None:
        two_blocks = (
            '```json\n{"direction": "short"}\n```\n\n'
            '```json\n{"direction": "long", "entry": 1}\n```'
        )
        d = agent_mod.parse_decision_block(two_blocks)
        assert d is not None
        assert d["direction"] == "long"
        assert d["entry"] == 1

    def test_broken_block_is_skipped_for_valid_one(self) -> None:
        mixed = '```json\n{broken\n```\n\n```json\n{"direction": "neutral"}\n```'
        d = agent_mod.parse_decision_block(mixed)
        assert d is not None
        assert d["direction"] == "neutral"

    def test_without_json_yields_none(self) -> None:
        assert agent_mod.parse_decision_block("纯文本没有 JSON") is None
        assert agent_mod.parse_decision_block("") is None
        assert agent_mod.parse_decision_block("```python\nprint(1)\n```") is None

    def test_bracket_fence_also_parses(self) -> None:
        bracketed = (
            "分析文本…\n"
            '[json]{"direction": "long", "entry": 77500, "stop_loss": 77280, '
            '"take_profit": 77850, "confidence": 0.65, "reason": "多头"}[/json]'
        )
        d = agent_mod.parse_decision_block(bracketed)
        assert d is not None
        assert d["direction"] == "long"
        assert d["entry"] == 77500
        assert d["stop_loss"] == 77280

    def test_json_fence_takes_priority_over_bracket(self) -> None:
        both = (
            '[json]{"direction": "neutral"}[/json]\n'
            '```json\n{"direction": "short", "entry": 1}\n```'
        )
        d = agent_mod.parse_decision_block(both)
        assert d is not None
        assert d["direction"] == "short"


@pytest.mark.asyncio
async def test_analysis_klines_use_local_tail_100(monkeypatch):
    """Local K-lines available -> returns formatted compact lines (tail 100)."""
    import pandas as pd

    class FakeKM:
        async def get_klines(self, **kwargs):
            assert kwargs["source"] == "auto"
            assert kwargs["interval"] == "1h"
            assert kwargs["symbol"] == "BTCUSDT"
            # 120 bars; only the most recent 100 should surface.
            rows = [
                {
                    "timestamp": 1711000000000 + i * 3600_000,
                    "open": 100.0 + i,
                    "high": 101.0 + i,
                    "low": 99.0 + i,
                    "close": 100.5 + i,
                    "volume": 1000 + i,
                }
                for i in range(120)
            ]
            return pd.DataFrame(rows)

    from app.core import dependencies

    monkeypatch.setattr(dependencies, "get_kline_manager", lambda: FakeKM())

    text = await agent_mod._fetch_analysis_klines(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "exchange": "binance",
            "market_type": "futures",
        }
    )
    assert text
    lines = text.split("\n")
    assert len(lines) == 100, f"expected 100 lines, got {len(lines)}"
    # tail(100) keeps bars i=20..119 → first line open=120.0.
    assert "120.0" in lines[0]
    assert "219.0" in lines[-1]
    # Each line has 6 comma-separated columns.
    assert len(lines[0].split(",")) == 6


@pytest.mark.asyncio
async def test_analysis_klines_fall_back_to_remote(monkeypatch):
    """Local empty -> falls back to remote; both empty -> empty string."""
    import pandas as pd

    class FakeKM:
        async def get_klines(self, **kwargs):
            if kwargs["source"] == "auto":
                return pd.DataFrame()  # local insufficient
            assert kwargs["source"] == "remote"
            return pd.DataFrame(
                [
                    {
                        "timestamp": 1711000000000,
                        "open": 1,
                        "high": 2,
                        "low": 0.5,
                        "close": 1.5,
                        "volume": 10,
                    }
                ]
            )

    from app.core import dependencies

    monkeypatch.setattr(dependencies, "get_kline_manager", lambda: FakeKM())

    text = await agent_mod._fetch_analysis_klines(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "exchange": "binance",
            "market_type": "futures",
        }
    )
    assert text
    assert "1.5" in text


@pytest.mark.asyncio
async def test_analysis_klines_blank_without_kline_manager(monkeypatch):
    """No kline manager -> empty string (prompt degrades, no crash)."""
    from app.core import dependencies

    monkeypatch.setattr(dependencies, "get_kline_manager", lambda: None)

    text = await agent_mod._fetch_analysis_klines(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "exchange": "binance",
            "market_type": "futures",
        }
    )
    assert text == ""


@pytest.mark.asyncio
async def test_analysis_klines_blank_without_symbol():
    text = await agent_mod._fetch_analysis_klines({})
    assert text == ""


# --- objective-signal arbitration helpers (P2-4) -----------------------------


def _rising_frame(n=60, *, start=100.0, step=1.0):
    import pandas as pd

    rows = []
    for i in range(n):
        o = start + i * step
        c = o + step * 0.5
        rows.append(
            {
                "timestamp": 1711000000000 + i * 3600_000,
                "open": o,
                "high": c + step * 0.3,
                "low": o - step * 0.1,
                "close": c,
                "volume": 1000 + i,
            }
        )
    return pd.DataFrame(rows)


@pytest.mark.asyncio
async def test_fetch_arbitration_klines_grabs_main_and_adjacent_tfs(monkeypatch):
    """Main TF uses auto; 1d/4h/1h locals are added when long enough."""
    import pandas as pd

    calls: list[dict] = []

    class FakeKM:
        async def get_klines(self, **kwargs):
            calls.append(dict(kwargs))
            if kwargs["interval"] == "1h":
                return _rising_frame()
            if kwargs["interval"] == "4h":
                return _rising_frame(n=40)
            if kwargs["interval"] == "1d":
                return _rising_frame(n=20)  # too short -> excluded
            return pd.DataFrame()

    from app.core import dependencies

    monkeypatch.setattr(dependencies, "get_kline_manager", lambda: FakeKM())

    frames = await agent_mod._fetch_arbitration_klines(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "exchange": "binance",
            "market_type": "futures",
        }
    )
    assert set(frames) == {"1h", "4h"}  # 1d dropped for being too short
    main_call = next(c for c in calls if c["interval"] == "1h")
    assert main_call["source"] == "auto"
    adj_call = next(c for c in calls if c["interval"] == "4h")
    assert adj_call["source"] == "local"


@pytest.mark.asyncio
async def test_fetch_arbitration_klines_blank_without_symbol():
    frames = await agent_mod._fetch_arbitration_klines({})
    assert frames == {}


def test_apply_arbitration_overrides_conflicting_strong_consensus():
    """LLM long vs strong short consensus -> direction flips, raw_llm kept."""
    frames = {"1h": _rising_frame(), "4h": _rising_frame(), "1d": _rising_frame()}
    # Consensus long, LLM short -> objective override to long.
    decision = {
        "direction": "short",
        "entry": 100.0,
        "stop_loss": 99.0,
        "take_profit": 105.0,
        "confidence": 0.8,
    }
    import asyncio

    asyncio.run(agent_mod._apply_arbitration({"_arbitration_klines": frames}, decision))
    assert decision["raw_llm"]["direction"] == "short"
    assert decision["direction"] == "long"
    assert decision["arbitration"]["forced_hold"] is False


def test_apply_arbitration_noop_when_decision_not_arbitrable():
    decision = {"direction": "weird", "confidence": "high"}
    import asyncio

    asyncio.run(agent_mod._apply_arbitration({}, decision))
    assert decision == {"direction": "weird", "confidence": "high"}  # unchanged


def test_apply_arbitration_noop_when_no_klines_keeps_llm_direction():
    """No frames (empty context) -> quality_ok=False but no crash, LLM preserved."""
    decision = {"direction": "long", "confidence": 0.7}
    import asyncio

    asyncio.run(agent_mod._apply_arbitration({}, decision))
    assert decision["direction"] == "long"
    assert "raw_llm" not in decision


@pytest.mark.asyncio
async def test_decision_lessons_returns_list(monkeypatch):
    """B1 human view: endpoint returns memory entries, latest first."""
    from selene_space_agent.memory import decision_outcomes as dom_mod

    class _FakeMemory:
        _entries = [
            {"deployment_id": "d1", "status": "pending", "strategy_id": "s1"},
            {
                "deployment_id": "d2",
                "status": "filled",
                "strategy_id": "s2",
                "reflection_text": "broke support",
            },
        ]

        def __init__(self, memory_dir=None):
            pass

    monkeypatch.setattr(dom_mod, "DecisionOutcomeMemory", _FakeMemory)
    transport = ASGITransport(app=_app(None))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/agent/decision-lessons")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"][0]["deployment_id"] == "d2"  # latest first
    assert len(body["data"]) == 2


@pytest.mark.asyncio
async def test_decision_lessons_flags_review_sentinel(monkeypatch):
    """B2 端到端:REVIEW 哨兵必须在人类可读面显式标出。

    评级读不出的决策不是 Hold;调用方(前端)要能凭 ``is_review`` 把它渲染成
    醒目警告,而不是当普通评级列出。空白 rating 也在响应里归一到 REVIEW。
    """
    from selene_space_agent.memory import decision_outcomes as dom_mod

    class _FakeMemory:
        _entries = [
            {"deployment_id": "d1", "status": "filled", "rating": ""},
            {"deployment_id": "d2", "status": "filled", "rating": "Buy"},
            {"deployment_id": "d3", "status": "filled", "rating": "REVIEW"},
        ]

        def __init__(self, memory_dir=None):
            pass

    monkeypatch.setattr(dom_mod, "DecisionOutcomeMemory", _FakeMemory)
    transport = ASGITransport(app=_app(None))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/agent/decision-lessons")
    assert resp.status_code == 200
    rows = {r["deployment_id"]: r for r in resp.json()["data"]}
    assert rows["d1"]["is_review"] is True
    assert rows["d1"]["rating"] == "REVIEW"
    assert rows["d3"]["is_review"] is True
    assert rows["d2"]["is_review"] is False
    assert rows["d2"]["rating"] == "Buy"

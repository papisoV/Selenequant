"""The ``qd:`` backtest branch.

A QuantDinger export is not a strategy in the repository -- it is a script
that binds its own instrument. What the branch must therefore get right is
that the *source's* declaration drives the run: symbol and interval come
from the export (not the request), the venue comes from the export, history
is deep enough not to silently become a different experiment, and the
engine is configured for the weight-target semantics the runtime emits.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import app.api.v1.backtests as backtests_mod
import pandas as pd
import pytest
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_backtest.engine import BacktestEngine
from selene_space_backtest.slippage import FixedSlippage
from selene_space_strategy.qd import QDStrategyRuntime
from selene_space_types.trading import OrderSide

_SRC48 = Path(
    r"<local-path-redacted>"
)
_needs_backup = pytest.mark.skipif(
    not _SRC48.exists(), reason="QuantDinger strategy backup library not installed"
)
_PILOT_CACHE = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "data"
    / "xlm_usdt_swap_1h.parquet"
)


@pytest.fixture(autouse=True)
def _wipe_tasks():
    from selene_space_agent.tools.strategy_tools import get_results_store

    backtests_mod._tasks.clear()
    get_results_store().clear()
    yield
    backtests_mod._tasks.clear()
    get_results_store().clear()


def _frame(symbol: str, bars: int) -> pd.DataFrame:
    """A deterministic oscillating OHLCV series (no exchange, no clock)."""
    prices = [100.0 + (i % 40) - 20 + (i % 7) for i in range(bars)]
    return pd.DataFrame(
        {
            "timestamp": [1_700_000_000_000 + i * 3_600_000 for i in range(bars)],
            "open": prices,
            "high": [p + 1.5 for p in prices],
            "low": [p - 1.5 for p in prices],
            "close": [p + 0.5 for p in prices],
            "volume": [10.0 + i % 5 for i in range(bars)],
            "symbol": symbol,
            "interval": "1h",
        }
    )


@pytest.fixture
def engine_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture the config and strategy the branch hands to the engine."""
    captured: dict[str, Any] = {}
    real_run = BacktestEngine.run

    def spy(self, strategy, data):
        captured["config"] = self._config
        captured["strategy"] = strategy
        captured["bars"] = len(data)
        return real_run(self, strategy, data)

    monkeypatch.setattr(BacktestEngine, "run", spy)
    return captured


@pytest.fixture
def stub_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve a local frame instead of reaching an exchange."""

    async def _qd(strategy_id, symbol, interval):
        return _frame(symbol, 400)

    async def _generic(config, body):
        symbol = body.symbol or (config.symbols[0] if config.symbols else "BTC/USDT")
        return _frame(symbol, 400)

    monkeypatch.setattr(backtests_mod, "_load_qd_kline_data", _qd)
    monkeypatch.setattr(backtests_mod, "_load_kline_data", _generic)


async def _post(c: AsyncClient, strategy_id: str) -> str:
    made = await c.post("/api/v1/backtests", json={"strategy_id": strategy_id})
    assert made.status_code == 202
    return str(made.json()["data"]["task_id"])


async def _settle(c: AsyncClient, task_id: str) -> dict[str, Any]:
    """Poll until the background task reaches a terminal state.

    Paced to stay inside the API's per-IP rate limit (120 req/60s), and a
    429 is treated as backoff rather than failure -- a real client polling
    a minutes-long backtest would do the same.
    """
    data: dict[str, Any] = {}
    for _ in range(120):
        resp = await c.get(f"/api/v1/backtests/{task_id}")
        if resp.status_code == 429:
            await asyncio.sleep(1.0)
            continue
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        if data["status"] in ("completed", "failed"):
            return data
        await asyncio.sleep(0.6)
    raise AssertionError(f"task {task_id} never settled: {data}")


def _api():
    return AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    )


# -- declaration drives the run ---------------------------------------------


@pytest.mark.asyncio
@_needs_backup
async def test_qd_branch_derives_symbol_and_interval_from_the_source(
    stub_data: None,
) -> None:
    async with _api() as c:
        task_id = await _post(c, "qd:src48")
        data = await _settle(c, task_id)

    assert data["status"] == "completed", data.get("error")
    assert data["symbol"] == "XLM/USDT"
    assert data["interval"] == "1h"
    assert data["strategy_id"] == "qd:src48"


@pytest.mark.asyncio
@_needs_backup
async def test_qd_branch_builds_the_script_runtime(
    stub_data: None, engine_calls: dict[str, Any]
) -> None:
    async with _api() as c:
        task_id = await _post(c, "qd:src48")
        await _settle(c, task_id)

    assert isinstance(engine_calls["strategy"], QDStrategyRuntime)
    assert engine_calls["strategy"].symbol == "XLM/USDT"


@pytest.mark.asyncio
@_needs_backup
async def test_qd_branch_configures_the_engine_for_weight_targets(
    stub_data: None, engine_calls: dict[str, Any]
) -> None:
    async with _api() as c:
        task_id = await _post(c, "qd:src48")
        await _settle(c, task_id)

    config = engine_calls["config"]
    assert config.position_sizing == "signal_quantity"
    assert config.allow_pyramiding is True
    assert isinstance(config.slippage, FixedSlippage)
    # 5 bp of slippage: a buy at 100 fills at 100.05.
    assert config.slippage.apply(100.0, OrderSide.BUY) == pytest.approx(100.05)
    # 0.1% commission: a 100 x 10 fill costs 1.0.
    assert config.commission.calculate(100.0, 10.0) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_non_qd_strategy_keeps_the_engine_defaults(
    stub_data: None, engine_calls: dict[str, Any]
) -> None:
    """The QD overrides are scoped to the qd branch, not the endpoint."""
    async with _api() as c:
        task_id = await _post(c, "bb_v3")
        await _settle(c, task_id)

    config = engine_calls["config"]
    assert config.position_sizing == "risk_based"
    assert config.allow_pyramiding is False


# -- virtual stubs must not shadow the real strategy -------------------------


def _virtual_stub(strategy_id: str) -> Any:
    """The row BacktestRepository.save() backfills to satisfy the FK.

    Its symbols are empty; treating it as the real strategy killed every
    backtest after the first one for that id.
    """
    from selene_space_types.strategy import StrategyConfig

    return StrategyConfig(
        id=strategy_id,
        name=strategy_id,
        type="custom",
        symbols=[],
        interval="1h",
        source="virtual",
    )


@pytest.mark.asyncio
async def test_template_id_ignores_a_virtual_stub(
    stub_data: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A poisoned YAML-template id still runs, resolved from STRATEGY_TEMPLATES.

    Before the fix `_get_strategy("sma_cross")` returned the empty-symbol stub
    BacktestRepository.save() backfills, the template-synthesis branch was
    skipped (config was not None), and the run died on "must specify at least
    one symbol". ``sma_cross`` is a YAML factory template (not a Python preset),
    so it goes through the compile path where the symbol check actually fires.
    """

    async def _poisoned(strategy_id: str, *, user_id: str = "local") -> Any:
        return _virtual_stub(strategy_id)

    # _get_strategy is imported inside _run_backtest from app.api.v1.strategies,
    # so patch it at that module.
    import app.api.v1.strategies as strategies_mod

    monkeypatch.setattr(strategies_mod, "_get_strategy", _poisoned)

    async with _api() as c:
        task_id = await _post(c, "sma_cross")
        data = await _settle(c, task_id)

    assert data["status"] == "completed", data.get("error")
    assert data["symbol"] == "BTC/USDT"  # from the template, not the empty stub


@pytest.mark.asyncio
async def test_virtual_stub_with_real_symbols_is_still_used(
    stub_data: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only symbols-empty stubs are dropped; a user strategy is untouched."""
    from selene_space_types.strategy import StrategyConfig

    real = StrategyConfig(
        id="my-user-strategy",
        name="Mine",
        type="custom",
        symbols=["ETH/USDT"],
        interval="1h",
        source="user",
        entry_rules={"long": [{"indicator": "sma"}]},
        exit_rules={"close_long": [{"indicator": "sma"}]},
    )

    async def _get(strategy_id: str, *, user_id: str = "local") -> Any:
        return real

    import app.api.v1.strategies as strategies_mod

    monkeypatch.setattr(strategies_mod, "_get_strategy", _get)

    async with _api() as c:
        task_id = await _post(c, "my-user-strategy")
        data = await _settle(c, task_id)

    assert data["status"] == "completed", data.get("error")
    assert data["symbol"] == "ETH/USDT"


# -- deep history from the declared venue -----------------------------------


@pytest.mark.asyncio
@_needs_backup
async def test_qd_loader_asks_for_deep_history_on_the_declared_venue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeMarket:
        async def fetch_klines_batch(self, **kwargs: Any) -> list[Any]:
            calls.append(kwargs)
            return []

    monkeypatch.setattr(backtests_mod, "get_market_service", lambda: FakeMarket())

    with pytest.raises(FileNotFoundError):
        await backtests_mod._load_qd_kline_data("qd:src48", "XLM/USDT", "1h")

    assert calls == [
        {
            "symbol": "XLM/USDT",
            "exchange": "okx",
            "market_type": "futures",
            "interval": "1h",
            "total": 6200,
        }
    ]
    assert backtests_mod.QD_KLINES_TOTAL > 1500, "must exceed the REST single-call cap"


@pytest.mark.asyncio
@_needs_backup
async def test_qd_loader_reports_a_missing_market_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backtests_mod, "get_market_service", lambda: None)
    with pytest.raises(FileNotFoundError, match="Market service unavailable"):
        await backtests_mod._load_qd_kline_data("qd:src48", "XLM/USDT", "1h")


# -- failures stay legible ---------------------------------------------------


@pytest.mark.asyncio
@_needs_backup
async def test_unknown_qd_id_fails_the_task_with_a_named_error(
    stub_data: None,
) -> None:
    async with _api() as c:
        task_id = await _post(c, "qd:src999999")
        data = await _settle(c, task_id)

    assert data["status"] == "failed"
    assert "qd:src999999" in str(data.get("error"))


@pytest.mark.asyncio
@_needs_backup
async def test_undrivable_qd_export_fails_the_task_with_its_reason(
    stub_data: None,
) -> None:
    """A portfolio export declares no ``handle_data``; say so, don't 500."""
    async with _api() as c:
        task_id = await _post(c, "qd:src41")
        data = await _settle(c, task_id)

    assert data["status"] == "failed"
    assert "handle_data" in str(data.get("error"))


# -- the run actually trades on the pilot's own history ----------------------


@pytest.mark.asyncio
@pytest.mark.skipif(
    not (_SRC48.exists() and _PILOT_CACHE.exists()),
    reason="needs the backup library and the pilot's cached OKX history",
)
async def test_qd_branch_produces_trades_on_real_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end over real OKX bars -- the pilot's own cached history.

    The source recomputes its indicators over a trailing window on every
    bar, so the full 6200-bar pilot frame takes minutes. A recent 600-bar
    slice keeps this a test rather than a job while still running the real
    file (real timestamps, real prices) through the real source.
    """
    frame = pd.read_parquet(_PILOT_CACHE).tail(600).reset_index(drop=True)

    async def _qd(strategy_id, symbol, interval):
        return frame

    monkeypatch.setattr(backtests_mod, "_load_qd_kline_data", _qd)

    async with _api() as c:
        task_id = await _post(c, "qd:src48")
        data = await _settle(c, task_id)

    assert data["status"] == "completed", data.get("error")
    assert data["total_trades"] > 0
    assert data["symbol"] == "XLM/USDT"

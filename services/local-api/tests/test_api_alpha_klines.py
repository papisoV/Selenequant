"""Functional tests for alpha.py real-klines data closure (R5).

Covers:
- TrainRequest new-field defaults (symbol/exchange/market_type/interval,
  use_synthetic_data default False)
- _run_training loads real klines from a temp SQLite DB; falls back to
  synthetic when the partition has no local data (marking data_source)
- backtest_formula returns source='real' with real bars when klines exist;
  source='synthetic' with 500 bars when the partition is empty

Real klines.db is gitignored and absent from the worktree, so all tests
build a temp SQLite DB via KlineDB.put_klines and monkeypatch the db-path
helper in alpha.py.
"""

from __future__ import annotations


import pytest

from selene_space_market.kline_db import KlineDB
from selene_space_types.market import KlineEvent

from app.api.v1 import alpha


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_klines(
    exchange: str = "binance",
    market_type: str = "futures",
    symbol: str = "BTC/USDT",
    interval: str = "1h",
    n: int = 200,
    base_price: float = 60_000.0,
) -> list[KlineEvent]:
    """Build a realistic OHLCV series (smooth random walk)."""
    import random

    rng = random.Random(42)
    bars: list[KlineEvent] = []
    price = base_price
    step_ms = {"1m": 60_000, "15m": 900_000, "1h": 3_600_000}[interval]
    for i in range(n):
        drift = rng.uniform(-0.004, 0.004)
        close = price * (1 + drift)
        open_ = price
        high = max(open_, close) * (1 + rng.uniform(0, 0.003))
        low = min(open_, close) * (1 - rng.uniform(0, 0.003))
        bars.append(
            KlineEvent(
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                timestamp=1_700_000_000_000 + i * step_ms,
                interval=interval,
                open=round(open_, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(close, 2),
                volume=round(rng.uniform(1e3, 1e5), 2),
                closed=True,
            )
        )
        price = close
    return bars


@pytest.fixture()
def klines_db(tmp_path):
    """Temp SQLite DB seeded with a real BTC/USDT futures 1h partition."""
    db_path = tmp_path / "klines.db"
    db = KlineDB(db_path=str(db_path))
    db.put_klines(_make_klines(n=200))
    db.close()
    return db_path


@pytest.fixture()
def patch_db_path(monkeypatch):
    """Point alpha._default_klines_db_path at the temp DB."""

    def _patch(db_path):
        monkeypatch.setattr(alpha, "_default_klines_db_path", lambda: db_path)

    return _patch


@pytest.fixture()
def empty_db(tmp_path):
    """Temp SQLite DB with the klines schema but zero rows (empty partition)."""
    db_path = tmp_path / "klines.db"
    db = KlineDB(db_path=str(db_path))
    db.open()
    db.close()
    return db_path


# ---------------------------------------------------------------------------
# US-002: TrainRequest defaults
# ---------------------------------------------------------------------------


class TestTrainRequestDefaults:
    def test_new_fields_present_and_defaulted(self) -> None:
        req = alpha.TrainRequest()
        assert req.symbol == "BTC/USDT"
        assert req.exchange == "binance"
        assert req.market_type == "futures"
        assert req.interval == "1h"

    def test_use_synthetic_data_defaults_false(self) -> None:
        req = alpha.TrainRequest()
        assert req.use_synthetic_data is False

    def test_fields_are_overridable(self) -> None:
        req = alpha.TrainRequest(
            symbol="ETH/USDT",
            exchange="okx",
            market_type="spot",
            interval="15m",
            use_synthetic_data=True,
        )
        assert req.symbol == "ETH/USDT"
        assert req.exchange == "okx"
        assert req.market_type == "spot"
        assert req.interval == "15m"
        assert req.use_synthetic_data is True


# ---------------------------------------------------------------------------
# US-003: _run_training real-klines + synthetic fallback
# ---------------------------------------------------------------------------


class TestRunTraining:
    @pytest.mark.asyncio
    async def test_training_with_real_klines(
        self, klines_db, patch_db_path, monkeypatch
    ) -> None:
        patch_db_path(klines_db)
        session_id = "test_real"

        # Keep the training loop tiny so the test stays fast.
        class _FakeEngine:
            def __init__(self, **kwargs):
                self.best_score = 0.42
                self.best_formula = [0, 6, 3, 1]
                self.training_history = {"step": [1], "avg_reward": [0.1]}

            def train(
                self,
                on_step=None,
                start_step=0,
                checkpoint_path=None,
                checkpoint_interval=10,
            ):
                return None

            def save_checkpoint(self, path, step, extra=None):
                pass

            def load_checkpoint(self, path):
                return 0

        monkeypatch.setattr(alpha, "_sessions", {})
        alpha._sessions[session_id] = {
            "status": "starting",
            "step": 0,
            "best_score": -float("inf"),
            "train_steps": 10,
            "config": {},
            "started_at": 0.0,
        }
        monkeypatch.setattr("selene_space_alpha.mining.AlphaEngine", _FakeEngine)

        req = alpha.TrainRequest(
            train_steps=10,
            batch_size=8,
            use_synthetic_data=False,
            symbol="BTC/USDT",
            exchange="binance",
            market_type="futures",
            interval="1h",
        )
        await alpha._run_training(session_id, req)

        session = alpha._sessions[session_id]
        assert session["status"] == "completed"
        assert session["data_source"] == "real"
        assert session["best_score"] == 0.42

    @pytest.mark.asyncio
    async def test_training_falls_back_to_synthetic_when_no_data(
        self, empty_db, patch_db_path, monkeypatch
    ) -> None:
        patch_db_path(empty_db)
        session_id = "test_fallback"

        class _FakeEngine:
            def __init__(self, **kwargs):
                self.best_score = -0.1
                self.best_formula = [1]
                self.training_history = {}

            def train(
                self,
                on_step=None,
                start_step=0,
                checkpoint_path=None,
                checkpoint_interval=10,
            ):
                return None

            def save_checkpoint(self, path, step, extra=None):
                pass

            def load_checkpoint(self, path):
                return 0

        monkeypatch.setattr(alpha, "_sessions", {})
        alpha._sessions[session_id] = {
            "status": "starting",
            "step": 0,
            "best_score": -float("inf"),
            "train_steps": 10,
            "config": {},
            "started_at": 0.0,
        }
        monkeypatch.setattr("selene_space_alpha.mining.AlphaEngine", _FakeEngine)

        req = alpha.TrainRequest(
            train_steps=10,
            batch_size=8,
            use_synthetic_data=False,
            symbol="BTC/USDT",
            exchange="binance",
            market_type="futures",
            interval="1h",
        )
        await alpha._run_training(session_id, req)

        session = alpha._sessions[session_id]
        assert session["status"] == "completed"
        assert session["data_source"] == "synthetic"
        assert session["fallback_reason"]


# ---------------------------------------------------------------------------
# US-004: backtest_formula real-klines + synthetic fallback
# ---------------------------------------------------------------------------


class TestBacktestFormula:
    @pytest.fixture()
    def formula(self, tmp_path, monkeypatch):
        """Write a formula JSON into a temp results dir and point alpha at it."""
        import json

        res_dir = tmp_path / "alpha_results"
        res_dir.mkdir(parents=True, exist_ok=True)
        (res_dir / "best_meme_strategy_bt_f1.json").write_text(
            # Valid RPN: 2 feature tokens + 1 binary operator — the VM requires
            # a single-item final stack, all-feature formulas raise ValueError.
            json.dumps([0, 6, 97]),
            encoding="utf-8",
        )
        monkeypatch.setattr(alpha, "_results_dir", res_dir)
        return "bt_f1"

    @pytest.mark.asyncio
    async def test_real_klines(self, klines_db, patch_db_path, formula) -> None:
        patch_db_path(klines_db)
        resp = await alpha.backtest_formula(
            formula,
            symbol="BTC/USDT",
            exchange="binance",
            market_type="futures",
            interval="1h",
        )
        assert resp.error is None
        data = resp.data
        assert data["source"] == "real"
        assert data["total_bars"] == 200
        assert data["formula_id"] == formula
        assert data["latest_score"] is not None

    @pytest.mark.asyncio
    async def test_synthetic_fallback_empty_partition(
        self, empty_db, patch_db_path, formula
    ) -> None:
        patch_db_path(empty_db)
        resp = await alpha.backtest_formula(
            formula,
            symbol="BTC/USDT",
            exchange="binance",
            market_type="futures",
            interval="1h",
        )
        assert resp.error is None
        data = resp.data
        assert data["source"] == "synthetic"
        assert data["total_bars"] == 500  # synthetic random-walk default

    @pytest.mark.asyncio
    async def test_formula_not_found(self) -> None:
        with pytest.raises(Exception):
            await alpha.backtest_formula(
                "nonexistent",
                symbol="BTC/USDT",
                exchange="binance",
                market_type="futures",
                interval="1h",
            )


# ---------------------------------------------------------------------------
# kline-coverage 读语义 = GET(回归锁:曾为 POST,挂载期掐死同页验证报告 GET)
# ---------------------------------------------------------------------------


class TestKlineCoverageMethod:
    def _route(self):
        for r in alpha.router.routes:
            if getattr(r, "path", "").endswith("/kline-coverage"):
                return r
        raise AssertionError("no /kline-coverage route")

    def test_is_get_not_post(self) -> None:
        methods = {m.upper() for m in self._route().methods}  # type: ignore[attr-defined]
        assert "GET" in methods, methods
        assert "POST" not in methods, methods

    @pytest.mark.asyncio
    async def test_reads_partition_and_returns_shape(
        self, klines_db, patch_db_path
    ) -> None:
        patch_db_path(klines_db)
        resp = await alpha.kline_coverage(
            exchange="binance",
            market_type="futures",
            symbol="BTC/USDT",
            interval="1h",
        )
        assert resp.error is None
        assert resp.data["count"] == 200
        assert resp.data["latest_timestamp"] == 1_700_000_000_000 + 199 * 3_600_000
        assert resp.data["latest_iso"] is not None

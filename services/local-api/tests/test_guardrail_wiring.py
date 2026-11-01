"""Guardrail wiring tests: regime funnel, paper-order gates, stream filters.

Covers:
1. StrategyRunner._check_regime_guard — BLOCKED on DANGEROUS, pass on
   RESTRICTED, fail-open on alpha source / unknown type / probe failure.
2. deploy_saved_strategy response carries market_regime + regime_warning.
3. PaperPlaceOrderTool — user_confirmed hard gate + overtrading detector.
4. StrategyDeployTool — user_confirmed hard gate.
5. Hallucination stream filters — secret redaction + promise disclaimer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from app.core.strategy_runner import StrategyRunner
    from selene_space_agent.tools.paper_tools import PaperPlaceOrderTool

from selene_space_types.agent import MarketRegime, MarketState
from selene_space_types.risk import RiskLevel
from selene_space_security.guardrails.overtrading import (
    OvertradingConfig,
    OvertradingDetector,
)
from selene_space_security.guardrails.regime_mismatch import (
    RegimeMismatchDetector,
)


def _state(regime: MarketRegime, trend: float = 0.5, vol: float = 0.3) -> MarketState:
    return MarketState(
        symbol="BTC/USDT",
        regime=regime,
        trend_strength=trend,
        volatility=vol,
        timestamp=1,
    )


def _yaml_config(strategy_type: str = "sma_cross") -> dict:
    return {
        "id": "strat-1",
        "name": "Test SMA",
        "type": strategy_type,
        "symbols": ["BTC/USDT"],
        "interval": "1h",
        "entry_rules": [{"kind": "sma_cross", "params": {}}],
        "exit_rules": [{"kind": "sma_cross", "params": {}}],
    }


# ---------------------------------------------------------------------------
# 1. regime funnel in StrategyRunner
# ---------------------------------------------------------------------------


def _runner() -> "StrategyRunner":
    from app.core.strategy_runner import StrategyRunner

    return StrategyRunner.__new__(StrategyRunner)


class TestRegimeFunnel:
    @pytest.mark.asyncio
    async def test_dangerous_mismatch_blocks(self) -> None:
        from selene_space_types.strategy import StrategyType

        runner = _runner()
        # severity grading wiring sanity: the same detector grades weak-trend
        # ranging + high vol on a trend strategy as DANGEROUS
        alert = RegimeMismatchDetector().check_mismatch(
            StrategyType.SMA_CROSS,
            MarketRegime.RANGING,
            trend_strength=0.1,
            volatility=0.85,
        )
        assert alert is not None and alert.severity == RiskLevel.DANGEROUS

        async def _fake_state(symbol, interval, **kw):
            return _state(MarketRegime.RANGING, trend=0.1, vol=0.85)

        with patch("app.core.regime_guard.get_market_state", new=_fake_state):
            err = await runner._check_regime_guard(_yaml_config())
        assert err is not None and err.startswith("BLOCKED")

    @pytest.mark.asyncio
    async def test_restricted_mismatch_passes(self) -> None:
        runner = _runner()

        async def _fake_state(symbol, interval, **kw):
            return _state(MarketRegime.RANGING, trend=0.5, vol=0.3)

        with patch("app.core.regime_guard.get_market_state", new=_fake_state):
            err = await runner._check_regime_guard(_yaml_config())
        assert err is None

    @pytest.mark.asyncio
    async def test_alpha_source_skips_matrix(self) -> None:
        runner = _runner()
        config = _yaml_config()
        config["source"] = "alpha:mined"

        async def _fail(*a, **kw):  # pragma: no cover - must not be called
            raise AssertionError("probe should not run for alpha source")

        with patch("app.core.regime_guard.get_market_state", new=_fail):
            err = await runner._check_regime_guard(config)
        assert err is None

    @pytest.mark.asyncio
    async def test_unknown_type_skips(self) -> None:
        runner = _runner()
        config = _yaml_config()
        config["type"] = "nonexistent_type"

        async def _fail(*a, **kw):  # pragma: no cover
            raise AssertionError("probe should not run for unknown type")

        with patch("app.core.regime_guard.get_market_state", new=_fail):
            err = await runner._check_regime_guard(config)
        assert err is None

    @pytest.mark.asyncio
    async def test_fail_open_no_state(self) -> None:
        runner = _runner()

        async def _fake_state(symbol, interval, **kw):
            return None

        with patch("app.core.regime_guard.get_market_state", new=_fake_state):
            err = await runner._check_regime_guard(_yaml_config())
        assert err is None


# ---------------------------------------------------------------------------
# 2. alpha deploy response regime hint
# ---------------------------------------------------------------------------


class TestAlphaDeployRegimeHint:
    @pytest.mark.asyncio
    async def test_response_fields_exist(self) -> None:
        import inspect

        from app.api.v1.alpha import deploy_saved_strategy

        src = inspect.getsource(deploy_saved_strategy)
        assert '"market_regime"' in src
        assert '"regime_warning"' in src


# ---------------------------------------------------------------------------
# 3. PaperPlaceOrderTool gates
# ---------------------------------------------------------------------------


class _FakeOrders:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def place_order(self, account_id, **kw) -> object:
        self.calls.append({"account_id": account_id, **kw})

        class _Placed:
            id = "ord-1"
            status = "filled"

        return _Placed()


class _Detector(OvertradingDetector):
    def __init__(self, trigger: bool) -> None:
        super().__init__(config=OvertradingConfig(max_orders_per_hour=999_999))
        self._trigger = trigger
        self.recorded: list[tuple[str, str]] = []

    def evaluate(self, symbol, side, now=None):
        if self._trigger:
            from selene_space_security.guardrails.overtrading import OvertradingAlert

            return [
                OvertradingAlert(
                    rule="hourly_rate",
                    severity=RiskLevel.RESTRICTED,
                    message="测试告警：频率超限",
                )
            ]
        return []

    def record_order(self, symbol, side, timestamp=None) -> None:
        self.recorded.append((symbol, side))


class TestPaperPlaceOrderGates:
    def _tool(self, detector=None) -> "PaperPlaceOrderTool":
        from selene_space_agent.tools.paper_tools import PaperPlaceOrderTool

        return PaperPlaceOrderTool(_FakeOrders(), overtrading_detector=detector)

    def _args(self, **over) -> dict:
        base = {
            "account_id": "acc-1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "order_type": "market",
            "quantity": 100,
            "user_confirmed": True,
        }
        base.update(over)
        return base

    def test_missing_confirmation_rejected(self) -> None:
        result = self._tool().execute(self._args(user_confirmed=False))
        assert result.error
        assert "user_confirmed" in result.error

    def test_overtrading_alert_blocks(self) -> None:
        detector = _Detector(trigger=True)
        result = self._tool(detector).execute(self._args())
        assert result.error
        assert "overtrading" in result.error.lower()
        assert not detector.recorded

    def test_clean_order_records(self) -> None:
        detector = _Detector(trigger=False)
        result = self._tool(detector).execute(self._args())
        assert result.output
        assert detector.recorded == [("BTC/USDT", "buy")]

    def test_detector_none_still_works(self) -> None:
        result = self._tool(None).execute(self._args())
        assert result.output


# ---------------------------------------------------------------------------
# 4. StrategyDeployTool gate
# ---------------------------------------------------------------------------


class TestStrategyDeployGate:
    @pytest.mark.asyncio
    async def test_missing_confirmation_rejected(self) -> None:
        from selene_space_agent.tools.strategy_tools import StrategyDeployTool

        called = []

        async def _fn(*a, **kw):
            called.append(a)
            return "ok"

        tool = StrategyDeployTool(_fn)
        result = await tool.async_execute({"strategy_id": "s-1"})
        assert result.error and "user_confirmed" in result.error
        assert not called

    @pytest.mark.asyncio
    async def test_confirmed_invokes_callback(self) -> None:
        from selene_space_agent.tools.strategy_tools import StrategyDeployTool

        called = []

        async def _fn(*a, **kw):
            called.append(a)
            return "ok"

        tool = StrategyDeployTool(_fn)
        result = await tool.async_execute(
            {"strategy_id": "s-1", "user_confirmed": True}
        )
        assert result.output == "ok"
        assert len(called) == 1


# ---------------------------------------------------------------------------
# 5. hallucination stream filters
# ---------------------------------------------------------------------------


class TestHallucinationFilters:
    def test_secret_redaction(self) -> None:
        from selene_space_security.guardrails.hallucination import HallucinationDetector

        detector = HallucinationDetector()
        text = "key is sk-abcdefghijklmnop123456 ok"
        cleaned = detector.redact_secrets_in_output(text)
        assert "sk-abcdefghijklmnop123456" not in cleaned
        assert "[已脱敏]" in cleaned

    def test_promise_scan_triggers(self) -> None:
        from selene_space_security.guardrails.hallucination import HallucinationDetector

        detector = HallucinationDetector()
        result = detector.scan_for_investment_promises("这个策略保证盈利")
        assert result.triggered
        assert result.warnings

    def test_promise_scan_clean(self) -> None:
        from selene_space_security.guardrails.hallucination import HallucinationDetector

        detector = HallucinationDetector()
        result = detector.scan_for_investment_promises("回测年化 12%")
        assert not result.triggered

    def test_agent_stream_wrapper_importable(self) -> None:
        from app.api.v1.agent import _guarded_agent_stream  # noqa: F401

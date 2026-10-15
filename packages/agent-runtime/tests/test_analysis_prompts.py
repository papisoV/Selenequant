"""Behaviour locks for the server-side analysis prompt builder: intent
routing, position rendering, kline injection, and template hygiene."""

from __future__ import annotations

from decimal import Decimal

from selene_space_agent.analysis_prompts import (
    INTENT_BACKTEST_EXPLAIN,
    INTENT_CLOSE_EVALUATION,
    INTENT_KLINE_PRICE_ACTION,
    INTENT_MULTI_TIMEFRAME,
    INTENT_TIMEFRAME_TRADE,
    VALID_INTENTS,
    build_analysis_prompt,
)
from selene_space_types.trading import MarginMode, OrderSide, Position, PositionSide


def _futures_base(intent: str, *, symbol: str = "BTC/USDT", mark: int = 61000) -> dict:
    return {
        "intent": intent,
        "symbol": symbol,
        "exchange": "okx",
        "market_type": "futures",
        "mark_price": mark,
    }


class TestIntentRouting:
    def test_unknown_or_missing_intent_rejected(self) -> None:
        checks = (
            build_analysis_prompt({}) is None,
            build_analysis_prompt({"intent": "made-up"}) is None,
        )
        assert all(checks)

    def test_every_registered_intent_builds(self) -> None:
        for intent in VALID_INTENTS:
            built = build_analysis_prompt({"intent": intent, "mark_price": 250})
            assert built is not None, f"Intent {intent} returned None"


class TestMultiTimeframePrompt:
    def test_core_fields_rendered(self) -> None:
        text = build_analysis_prompt(_futures_base(INTENT_MULTI_TIMEFRAME, mark=61500))
        checks = (
            text is not None,
            "BTC/USDT" in text,
            "okx" in text,
            "61500" in text,
            "15分钟" in text,
            "4小时" in text,
        )
        assert all(checks)

    def test_positions_block_included(self) -> None:
        payload = {
            **_futures_base(INTENT_MULTI_TIMEFRAME),
            "contract_symbol": "BTC-USDT-SWAP",
            "positions": [
                {
                    "symbol": "BTC-USDT-SWAP",
                    "pos_side": "long",
                    "leverage": 8,
                    "margin": 900.0,
                    "quantity_base": 0.2,
                    "avg_entry_price": 60500.0,
                    "unrealized_pnl": 120.0,
                },
            ],
        }
        text = build_analysis_prompt(payload)
        quad = (
            "用户当前持仓状态" in text,
            "多单" in text,
            "BTC-USDT-SWAP" in text,
            "60500.00" in text,
        )
        assert text is not None and all(quad)


class TestTimeframeTradePrompt:
    def test_timeframe_and_entry_fields(self) -> None:
        text = build_analysis_prompt(
            _futures_base(INTENT_TIMEFRAME_TRADE, symbol="ETH/USDT", mark=3250)
            | {"timeframe": "30m"}
        )
        checks = (
            text is not None,
            "30m" in text,
            "ETH/USDT" in text,
            "3250" in text,
            "入场点位" in text,
        )
        assert all(checks)

    def test_position_context_adds_reference_line(self) -> None:
        payload = {**_futures_base(INTENT_TIMEFRAME_TRADE), "timeframe": "1h"}
        payload["positions"] = [{"pos_side": "long", "leverage": 4, "margin": 300}]
        text = build_analysis_prompt(payload)
        assert text is not None and "结合我的持仓情况" in text


class TestCloseEvaluationPrompt:
    def test_short_position_risk_review(self) -> None:
        payload = {
            **_futures_base(INTENT_CLOSE_EVALUATION),
            "contract_symbol": "BTC-USDT-SWAP",
            "positions": [{"pos_side": "short", "leverage": 15, "margin": 150}],
        }
        text = build_analysis_prompt(payload)
        triple = ("平仓" in text, "空单" in text, "风险评估" in text)
        assert text is not None and all(triple)

    def test_no_positions_falls_back_to_entry_advice(self) -> None:
        payload = {
            **_futures_base(INTENT_CLOSE_EVALUATION, symbol="SOL/USDT", mark=155),
            "positions": [],
        }
        text = build_analysis_prompt(payload)
        triple = ("没有找到活跃持仓" in text, "SOL" in text, "入场建议" in text)
        assert text is not None and all(triple)


class TestPositionThreeState:
    """持仓三态:未提供(None)/ 确认空仓([])/ 有持仓 —— 三种必须呈现不同。

    None 与 [] 都是 falsy,旧实现把"没传持仓"和"传了空仓"压成同一句
    "当前没有检测到持仓";若上游是"读取失败"而非"确实空仓",模型会被告知
    一个未经证实的结论(open-source-reference ``trading_graph.py:157-158``:
    "None, an empty book and a changed book are three different runs")。
    """

    def test_absent_positions_is_unavailable_not_empty(self) -> None:
        text = build_analysis_prompt(_futures_base(INTENT_MULTI_TIMEFRAME))
        assert text is not None
        assert "持仓数据不可用" in text
        assert "当前没有检测到持仓" not in text

    def test_explicit_empty_list_is_confirmed_empty(self) -> None:
        text = build_analysis_prompt(
            {**_futures_base(INTENT_MULTI_TIMEFRAME), "positions": []}
        )
        assert text is not None
        assert "当前没有检测到持仓" in text
        assert "持仓数据不可用" not in text

    def test_close_eval_absent_positions_is_unavailable_not_empty(self) -> None:
        text = build_analysis_prompt(_futures_base(INTENT_CLOSE_EVALUATION))
        assert text is not None
        assert "持仓数据不可用" in text
        assert "没有找到活跃持仓" not in text

    def test_close_eval_empty_list_is_confirmed_empty(self) -> None:
        text = build_analysis_prompt(
            {**_futures_base(INTENT_CLOSE_EVALUATION), "positions": []}
        )
        assert text is not None
        assert "没有找到活跃持仓" in text

    def test_timeframe_trade_absent_positions_is_unavailable(self) -> None:
        text = build_analysis_prompt(
            {**_futures_base(INTENT_TIMEFRAME_TRADE), "timeframe": "1h"}
        )
        assert text is not None
        assert "持仓数据不可用" in text


class TestBacktestExplainPrompt:
    def test_metrics_block_rendered(self) -> None:
        text = build_analysis_prompt(
            {
                "intent": INTENT_BACKTEST_EXPLAIN,
                "backtest_id": "bt-9001",
                "strategy_id": "strat-9001",
                "strategy_name": "EMA Ribbon Rider",
                "symbol": "BTC/USDT",
                "timeframe": "1h",
                "metrics": {
                    "total_return": 0.09,
                    "annual_return": 0.33,
                    "sharpe_ratio": 1.45,
                    "max_drawdown": -0.11,
                    "win_rate": 0.51,
                    "profit_factor": 1.35,
                    "total_trades": 37,
                    "calmar_ratio": 3.0,
                    "sortino_ratio": 1.9,
                    "expectancy": 9.5,
                    "recovery_factor": 1.4,
                },
            }
        )
        checks = (
            text is not None,
            "EMA Ribbon Rider" in text,
            "bt-9001" in text,
            "最大回撤" in text,
            "下一步应该优先优化什么" in text,
        )
        assert all(checks)

    def test_missing_fields_degrade_to_placeholders(self) -> None:
        text = build_analysis_prompt({"intent": INTENT_MULTI_TIMEFRAME})
        checks = (text is not None, "BTC/USDT" in text, "未知" in text)
        assert all(checks)


class TestKlinePriceActionPrompt:
    def test_kline_rows_embedded(self) -> None:
        rows = (
            "2026-08-23 14:00,152.0,153.5,151.2,153.0,900\n"
            "2026-08-23 15:00,153.0,154.1,152.7,153.8,1100"
        )
        payload = {
            **_futures_base(INTENT_KLINE_PRICE_ACTION, symbol="SOL/USDT", mark=153),
            "timeframe": "1h",
            "kline_data": rows,
        }
        text = build_analysis_prompt(payload)
        checks = (
            text is not None,
            "153" in text,
            "152.0,153.5,151.2,153.0,900" in text,
            "153.0,154.1,152.7,153.8,1100" in text,
            "direction" in text,
            "stop_loss" in text,
            "take_profit" in text,
        )
        assert all(checks)

    def test_missing_klines_use_placeholder(self) -> None:
        text = build_analysis_prompt(
            _futures_base(INTENT_KLINE_PRICE_ACTION, symbol="ETH/USDT", mark=3250)
            | {"timeframe": "15m"}
        )
        checks = (text is not None, "无 K 线数据" in text, "3250" in text)
        assert all(checks)


class TestTemplateHygiene:
    def test_no_frontend_template_syntax_leaks(self) -> None:
        for intent in VALID_INTENTS:
            text = build_analysis_prompt(
                {"intent": intent, "symbol": "BTC/USDT", "mark_price": 55_000}
            )
            checks = (
                text is not None,
                "${" not in text,
                "tickerSymbol" not in text,
                "currentSymbolInfo" not in text,
            )
            assert all(checks), f"frontend leak in intent {intent}"


class TestPositionRenderingParity:
    def test_object_and_dict_produce_identical_block(self) -> None:
        """A Position pydantic instance and its JSON dict must render alike."""
        pos_obj = Position(
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

        base = {
            **_futures_base(INTENT_MULTI_TIMEFRAME),
            "contract_symbol": "BTC-USDT-SWAP",
        }
        from_obj = build_analysis_prompt({**base, "positions": [pos_obj]})
        from_dict = build_analysis_prompt(
            {**base, "positions": [pos_obj.model_dump(mode="json")]}
        )

        checks = (
            from_obj is not None,
            from_dict is not None,
            from_obj == from_dict,
            "用户当前持仓状态" in from_obj,
            "多单" in from_obj,
            "65000.00" in from_obj,
        )
        assert all(checks)

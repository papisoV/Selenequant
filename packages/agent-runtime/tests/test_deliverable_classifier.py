"""Tests for C4 deliverable classifier — pure function, observe-only."""

from __future__ import annotations

from selene_space_agent.guards.deliverable_classifier import (
    DeliverableClass,
    classify_deliverable,
)


class TestCompleted:
    def test_clean_answer(self) -> None:
        assert (
            classify_deliverable("BTC 现报 43,200 USDT, 24h -1.75%。")
            == DeliverableClass.COMPLETED
        )

    def test_clean_english(self) -> None:
        assert (
            classify_deliverable("The SMA20 crossed above SMA50, signaling momentum.")
            == DeliverableClass.COMPLETED
        )

    def test_empty_is_completed(self) -> None:
        assert classify_deliverable("") == DeliverableClass.COMPLETED
        assert classify_deliverable("   \n  ") == DeliverableClass.COMPLETED


class TestUnresolvedToken:
    def test_chatml_shape_hit(self) -> None:
        assert (
            classify_deliverable("ok <|im_end|> done")
            == DeliverableClass.UNRESOLVED_TOKEN
        )

    def test_fullwidth_shape_hit(self) -> None:
        assert (
            classify_deliverable("结果 ｜assistant｜ 继续分析")
            == DeliverableClass.UNRESOLVED_TOKEN
        )

    def test_negative_normal_angle_brackets(self) -> None:
        # 普通 XML/HTML 不是 token 形状
        assert classify_deliverable("use <div> tags") == DeliverableClass.COMPLETED


class TestRawEnvelope:
    def test_tool_result_envelope_hit(self) -> None:
        text = '{"tool_result": {"symbol": "BTC/USDT", "price": 43200}}'
        assert classify_deliverable(text) == DeliverableClass.RAW_ENVELOPE

    def test_negative_json_without_envelope_key(self) -> None:
        assert (
            classify_deliverable('{"symbol": "BTC/USDT"}') == DeliverableClass.COMPLETED
        )


class TestFabricated:
    def test_admits_fabricated_hit(self) -> None:
        assert (
            classify_deliverable("这个数据是我编的, 别当真")
            == DeliverableClass.FABRICATED_DATA
        )

    def test_english_fabricated_hit(self) -> None:
        assert (
            classify_deliverable("I made up the data for demonstration.")
            == DeliverableClass.FABRICATED_DATA
        )

    def test_negative_cited_data(self) -> None:
        assert (
            classify_deliverable("据 klines.db 数据, BTC 收 43200。")
            == DeliverableClass.COMPLETED
        )


class TestPlanOnly:
    def test_plan_stub_hit(self) -> None:
        assert classify_deliverable("以下是我的方案：") == DeliverableClass.PLAN_ONLY

    def test_english_plan_stub_hit(self) -> None:
        assert (
            classify_deliverable("I would start by fetching data.")
            == DeliverableClass.PLAN_ONLY
        )

    def test_negative_plan_plus_deliverable(self) -> None:
        text = "I would start by fetching data. Result: SMA20 = 43100."
        assert classify_deliverable(text) == DeliverableClass.COMPLETED


class TestPurity:
    def test_no_mutation_no_io(self) -> None:
        text = "review <|im_start|> this"
        snapshot = str(text)
        classify_deliverable(text)
        assert text == snapshot  # 输入不被改写

"""Tests for claim_audit — ai-berkshire report_audit absorbed into paper-engine.

Covers: equity + crypto extraction fixtures, Unicode minus handling, verdict
tolerance boundaries (within → PASS / sign-flip 200% → FAIL), the $price vs
$BMT absolute-amount disambiguation (R2), and the optional-param wiring
(default off / opt-in / live forwarding).
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from selene_space_paper.claim_audit import _clean_num, ClaimAuditor
from selene_space_paper.decision_pipeline import (
    DecisionPipeline,
    PipelineAction,
    PipelineConfig,
)
from selene_space_paper.live_decision_pipeline import (
    LiveDecisionPipeline,
    LivePipelineConfig,
)
from selene_space_paper.orders import PaperOrderManager
from selene_space_risk.engine import RiskEngine
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide

EQUITY_MD = """# 腾讯研究报告（权益域样本）
收入：7,518亿元
毛利率：56%
PE：18.8x
市值：$5,670亿

| 指标 | 2024 | 2025 |
| --- | --- | --- |
| 收入（亿元） | 6,090 | 7,518 |
| 净利润（亿元） | 1,648 | 1,852 |
| 净利率 | 27.1% | 24.6% |
"""

CRYPTO_MD = """# BTC 研究报告（crypto 域样本）
BTC 价格：$64,200
Funding rate：0.02%
市值：$1.23T
ETH 持仓 3.5 ETH
Funding rate 5bp

| 指标 | 当前值 |
| --- | --- |
| BTC 成交价 | $64,500 |
| 24h 涨幅 | +2.1% |
"""


# ---------------------------------------------------------------------------
# extract() — 权益域
# ---------------------------------------------------------------------------


class TestEquityExtract:
    def test_extracts_expected_count_and_values(self) -> None:
        auditor = ClaimAuditor(domains=("equity",))
        points = auditor.extract(EQUITY_MD)
        # 4 KV claims + 6 table cells (3 rows × 2 cols)
        assert len(points) == 10
        # KV claims
        assert any(p.label == "收入" and p.reported_value == 7518.0 for p in points)
        assert any(p.label == "毛利率" and p.reported_value == 56.0 for p in points)
        assert any(p.label == "PE" and p.reported_value == 18.8 for p in points)
        assert any(p.label == "市值" and p.reported_value == 5670.0 for p in points)
        # table claims
        assert any(p.label == "收入（亿元） · 2025" and p.reported_value == 7518.0 for p in points)
        assert any(p.label == "收入（亿元） · 2024" and p.reported_value == 6090.0 for p in points)
        assert any(p.label == "净利润（亿元） · 2024" and p.reported_value == 1648.0 for p in points)
        assert any(p.label == "净利率 · 2024" and p.reported_value == 27.1 for p in points)
        assert any(p.label == "净利率 · 2025" and p.reported_value == 24.6 for p in points)

    def test_ids_are_sequential(self) -> None:
        auditor = ClaimAuditor(domains=("equity",))
        points = auditor.extract(EQUITY_MD)
        assert [p.id for p in points] == list(range(1, len(points) + 1))


# ---------------------------------------------------------------------------
# extract() — crypto 域
# ---------------------------------------------------------------------------


class TestCryptoExtract:
    def test_extracts_expected_count_and_values(self) -> None:
        auditor = ClaimAuditor()  # both domains default
        points = auditor.extract(CRYPTO_MD)
        # 3 KV + 2 pattern (crypto_qty / funding_rate) + 2 table cells
        assert len(points) == 7
        assert any(p.reported_value == 64200.0 for p in points)  # BTC 价格 KV
        assert any(p.reported_value == 0.02 and p.unit == "%" for p in points)
        assert any(p.reported_value == 1.23 and p.unit == "T" for p in points)  # 市值
        assert any(p.reported_value == 3.5 and p.unit == "ETH" for p in points)  # crypto_qty
        assert any(p.reported_value == 5.0 and p.unit == "bp" for p in points)  # funding_rate
        assert any(p.reported_value == 64500.0 for p in points)  # BTC 成交价 table
        assert any(p.reported_value == 2.1 and p.unit == "%" for p in points)  # 24h 涨幅 table


# ---------------------------------------------------------------------------
# 消歧（R2）：$价格 vs $...BMT 绝对金额
# ---------------------------------------------------------------------------


class TestPriceVsUsdAbsDisambiguation:
    def test_unadorned_price_not_captured_by_usd_abs(self) -> None:
        auditor = ClaimAuditor()
        text = "BTC 报价 $64,200，市值 $5.67B，总市值 $1.2T"
        points = auditor.extract(text)
        usd_abs = [p for p in points if p.unit in ("B", "M", "T", "亿")]
        prices = [p for p in points if p.unit == "$"]
        # $5.67B / $1.2T 是绝对金额（带 B/T 后缀），不是裸价格
        assert len(usd_abs) == 2
        assert any(p.reported_value == 5.67 and p.unit == "B" for p in usd_abs)
        assert any(p.reported_value == 1.2 and p.unit == "T" for p in usd_abs)
        # $64,200 无后缀 → 裸价格，不被 $...BMT pattern 吞掉
        assert len(prices) == 1
        assert any(p.reported_value == 64200.0 and p.unit == "$" for p in prices)
        assert not any(p.reported_value == 64200.0 for p in usd_abs)


# ---------------------------------------------------------------------------
# Unicode 减号族（U+2212 / U+2013 / U+FF0D）
# ---------------------------------------------------------------------------


class TestUnicodeMinus:
    def test_minus_family_parses_negative(self) -> None:
        auditor = ClaimAuditor(domains=("equity",))
        text = "涨跌幅：−1.72%\n净利润：–2.5亿元\n股息率：－0.3%"
        points = auditor.extract(text)
        neg = [p for p in points if p.reported_value < 0]
        assert len(neg) == 3
        assert any(abs(p.reported_value + 1.72) < 1e-9 for p in neg)  # U+2212
        assert any(abs(p.reported_value + 2.5) < 1e-9 for p in neg)  # en-dash U+2013
        assert any(abs(p.reported_value + 0.3) < 1e-9 for p in neg)  # fullwidth U+FF0D


# ---------------------------------------------------------------------------
# verdict() — 准出/打回
# ---------------------------------------------------------------------------


class TestVerdict:
    def test_pass_within_tolerance(self) -> None:
        auditor = ClaimAuditor()
        points = auditor.extract("收入：7,518亿元")
        report = auditor.verdict(points, {1: 7518.0})
        assert report.verdict == "PASS"
        assert report.total == 1
        assert report.pass_count == 1
        assert report.fail_count == 0
        assert report.results[0].passed is True

    def test_boundary_inside_and_outside(self) -> None:
        auditor = ClaimAuditor()  # 1% tolerance
        points = auditor.extract("收入：7,518亿元")
        within = auditor.verdict(points, {1: 7518.0 * 1.005})  # 0.5% deviation
        assert within.verdict == "PASS"
        outside = auditor.verdict(points, {1: 7518.0 * 1.02})  # 2% deviation
        assert outside.verdict == "FAIL"
        assert outside.fail_count == 1

    def test_sign_flip_rejects_with_200pct(self) -> None:
        auditor = ClaimAuditor()
        points = auditor.extract("涨跌幅：-1.72%")
        report = auditor.verdict(points, {1: 1.72})  # sign flip → 200% deviation
        assert report.verdict == "FAIL"
        assert report.fail_count == 1
        assert report.results[0].diff_pct == pytest.approx(200.0)

    def test_fetched_source_tuple(self) -> None:
        auditor = ClaimAuditor()
        points = auditor.extract("市值：$5.67B")
        report = auditor.verdict(points, {1: (5.67, "binance")})
        assert report.verdict == "PASS"
        assert report.results[0].source == "binance"

    def test_claims_without_fetched_are_skipped(self) -> None:
        auditor = ClaimAuditor()
        points = auditor.extract("收入：7,518亿元")
        report = auditor.verdict(points, {})  # no fetched values at all
        assert report.total == 0
        assert report.verdict == "PASS"


# ---------------------------------------------------------------------------
# 可配置 pattern registry（显式覆盖）
# ---------------------------------------------------------------------------


class TestCustomPatternRegistry:
    def test_explicit_pattern_override(self) -> None:
        auditor = ClaimAuditor(patterns=[("mymetric", r"(\d+)\s*XYZ", "XYZ")])
        points = auditor.extract("5 XYZ 和 7 XYZ")
        assert len(points) == 2
        assert all(p.label == "mymetric" for p in points)
        assert {p.reported_value for p in points} == {5.0, 7.0}
        assert all(p.unit == "XYZ" for p in points)


# ---------------------------------------------------------------------------
# 接线：DecisionPipeline / LiveDecisionPipeline 可选参数
# ---------------------------------------------------------------------------


def _make_signal(**overrides: Any) -> Signal:
    defaults: dict[str, Any] = {
        "strategy_id": "strat-001",
        "symbol": "BTC/USDT",
        "side": OrderSide.BUY,
        "strength": 0.85,
        "timestamp": int(time.time() * 1000),
        "reason": "SMA cross",
    }
    defaults.update(overrides)
    return Signal(**defaults)


def _make_decision_pipeline(**kwargs: Any) -> DecisionPipeline:
    engine = RiskEngine([])
    order_mgr = PaperOrderManager()
    config = PipelineConfig(
        dedupe_ttl_seconds=60.0,
        min_order_interval_seconds=0.0,  # no throttle for tests
        default_quantity=0.01,
        default_account_id="test-acc",
        enable_validation=False,  # no price provider in tests
    )
    return DecisionPipeline(
        risk_engine=engine,
        order_manager=order_mgr,
        config=config,
        **kwargs,
    )


def _make_live_pipeline(**kwargs: Any) -> LiveDecisionPipeline:
    engine = RiskEngine([])
    order_mgr = PaperOrderManager()
    config = PipelineConfig(
        dedupe_ttl_seconds=60.0,
        min_order_interval_seconds=0.0,
        default_quantity=0.01,
        default_account_id="test-acc",
        enable_validation=False,
    )
    live_config = LivePipelineConfig(enforce_limits=False)
    return LiveDecisionPipeline(
        risk_engine=engine,
        order_manager=order_mgr,
        config=config,
        live_config=live_config,
        **kwargs,
    )


class TestPipelineWiring:
    def test_default_off_zero_behavior_change(self) -> None:
        pipeline = _make_decision_pipeline()  # no claim_auditor
        result = pipeline.process_signal(_make_signal(reason="BTC 价格：$64,200，涨幅 +2.1%"))
        assert result.action == PipelineAction.EXECUTED
        assert pipeline._last_claim_audit is None  # never touched when default off

    def test_opt_in_extracts_claims_from_signal_reason(self) -> None:
        auditor = ClaimAuditor()
        pipeline = _make_decision_pipeline(claim_auditor=auditor)
        result = pipeline.process_signal(_make_signal(reason="BTC 价格：$64,200，涨幅 +2.1%"))
        assert result.action == PipelineAction.EXECUTED
        assert pipeline._last_claim_audit is not None
        # BTC 价格 KV claim + percent pattern claim for +2.1%
        assert pipeline._last_claim_audit["claim_count"] == 2

    def test_live_pipeline_forwards_claim_auditor(self) -> None:
        auditor = ClaimAuditor()
        pipeline = _make_live_pipeline(claim_auditor=auditor)
        result = pipeline.process_signal(_make_signal(reason="BTC 价格：$64,200"))
        assert pipeline._last_claim_audit is not None
        assert pipeline._last_claim_audit["claim_count"] == 1
        # limits disabled → signal still executes end-to-end
        assert result.action == PipelineAction.EXECUTED


# ---------------------------------------------------------------------------
# _clean_num 单元级回归（源 test_report_audit.py BUG-1 最小单元 TestCleanNumSign）
# Unicode 减号族（U+2212/U+2013/U+FF0D）已在 test_minus_family_parses_negative
# 覆盖，此处不重复。
# ---------------------------------------------------------------------------


class TestCleanNumSign:
    def test_ascii_minus(self) -> None:
        assert _clean_num("-1.72") == -1.72

    def test_plain_positive(self) -> None:
        assert _clean_num("1.72") == 1.72

    def test_ascii_plus_is_stripped(self) -> None:
        assert _clean_num("+3.5") == 3.5

    def test_fullwidth_plus_is_normalized(self) -> None:
        assert _clean_num("＋6.3") == 6.3

    def test_negative_with_thousands_separator(self) -> None:
        assert _clean_num("-1,234.5") == -1234.5

    def test_negative_with_fullwidth_comma(self) -> None:
        assert _clean_num("-1，234.5") == -1234.5

    def test_garbage_returns_none(self) -> None:
        assert _clean_num("n/a") is None


# ---------------------------------------------------------------------------
# 端到端：表格负数提取不丢负号/不翻号（源 test_report_audit.py BUG-1 复现表）
# ---------------------------------------------------------------------------


class TestNegativeTableExtraction:
    MD = (
        "| 公司 | 代码 | 股价 | 当日 | PB |\n"
        "|---|---|---|---|---|\n"
        "| 示例公司甲 | 000001 | 27.40 | -1.72% | 1.03 |\n"
        "| 示例公司乙 | 000002 | 17.56 | -1.90% | 2.18 |\n"
        "| 示例公司丙 | 000003 | 12.78 | +1.11% | 1.79 |\n"
        "| 毛利率 | — | — | -3.10% | — |\n"
    )

    def _values(self) -> set[float]:
        return {p.reported_value for p in ClaimAuditor(domains=("equity",)).extract(self.MD)}

    def test_extracts_negative_percentages(self) -> None:
        values = self._values()
        for expected in (-1.72, -1.9, -3.1):
            assert expected in values, f"{expected} 未被提取 —— 负号很可能又被正则吃掉了"

    def test_does_not_flip_sign(self) -> None:
        """回归核心：绝不能把 -1.72 提成 +1.72。"""
        values = self._values()
        assert 1.72 not in values, "负号丢失：-1.72 被提取成了 1.72"
        assert 1.9 not in values, "负号丢失：-1.90 被提取成了 1.90"
        assert 3.1 not in values, "负号丢失：-3.10 被提取成了 3.10"

    def test_plus_sign_normalised_to_positive(self) -> None:
        values = self._values()
        assert 1.11 in values

    def test_negative_count(self) -> None:
        values = self._values()
        assert len([v for v in values if v < 0]) == 3, (
            "应恰好提取到 3 个负值（-1.72 / -1.90 / -3.10）"
        )


# ---------------------------------------------------------------------------
# 1e15 绝对值守卫（源 test_report_audit.py TestAbsoluteValueGuard）
# ---------------------------------------------------------------------------


class TestAbsoluteValueGuard:
    def test_huge_negative_is_filtered(self) -> None:
        md = (
            "| 项目 | 数值 |\n"
            "|---|---|\n"
            "| 异常值 | -9999999999999999 |\n"
            "| 正常值 | -12.5 |\n"
        )
        values = {p.reported_value for p in ClaimAuditor(domains=("equity",)).extract(md)}
        assert -12.5 in values
        assert all(abs(v) < 1e15 for v in values), "超过 1e15 的负值未被过滤（abs() 守卫失效）"

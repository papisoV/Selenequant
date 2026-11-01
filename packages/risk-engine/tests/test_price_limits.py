"""Tests for :mod:`selene_space_risk.price_limits` — the one A-share band rule.

Locks the board × risk-warning matrix, the 2026-07-06 main-board reform
boundary, the ``trade_date=None`` = current-rulebook contract, the
``coerce_date`` refusal to guess, and the cent rounding the exchange uses.

The band table is asserted as a matrix rather than case by case because the
bug this module replaced was a *matrix* bug: 5% was applied to every ST name on
every board, when the 5% band was always a main-board-only legacy attribute.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from selene_space_risk.price_limits import (
    BEIJING_BOARD_LIMIT,
    GROWTH_BOARD_LIMIT,
    LEGACY_MAIN_BOARD_ST_LIMIT,
    MAIN_BOARD_LIMIT,
    MAIN_BOARD_ST_LIMIT_CHANGE_DATE,
    board_of_code,
    coerce_date,
    is_legacy_main_board_st,
    is_risk_warning_name,
    limit_down_price,
    limit_up_price,
    price_limit_ratio,
    round_to_cent,
)

LEGACY_DAY = "2026-07-03"
REFORM_DAY = "2026-07-06"

# (symbol, board) for each board's own band.
BOARD_CASES = [
    ("600519.SH", "main"),
    ("000001.SZ", "main"),
    ("300750.SZ", "cyb"),
    ("301236.SZ", "cyb"),
    ("688981.SH", "kcb"),
    ("689009.SH", "kcb"),
    ("830799.BJ", "bse"),
    ("430047.BJ", "bse"),
    ("920002.BJ", "bse"),
]


class TestBoardOfCode:
    @pytest.mark.parametrize(("symbol", "board"), BOARD_CASES)
    def test_classifies_by_numeric_prefix(self, symbol: str, board: str) -> None:
        assert board_of_code(symbol) == board

    def test_bare_code_classifies_like_suffixed(self) -> None:
        assert board_of_code("300750") == board_of_code("300750.SZ")
        assert board_of_code("830799") == board_of_code("830799.BJ")

    def test_unknown_prefix_falls_back_to_main(self) -> None:
        assert board_of_code("510300.SH") == "main"


class TestRiskWarningName:
    def test_star_st_is_risk_warning(self) -> None:
        assert is_risk_warning_name("*ST某某")

    def test_plain_st_is_risk_warning(self) -> None:
        assert is_risk_warning_name("ST某某")

    def test_case_insensitive(self) -> None:
        # A JSON round-trip can lowercase a name; the designation is the same.
        assert is_risk_warning_name("st某某")

    def test_delisting_name_is_not_risk_warning(self) -> None:
        # 退市整理期 names keep their board's ordinary band.
        assert not is_risk_warning_name("某某退")

    def test_ordinary_name(self) -> None:
        assert not is_risk_warning_name("贵州茅台")

    @pytest.mark.parametrize("blank", ["", None])
    def test_blank_name(self, blank: object) -> None:
        assert not is_risk_warning_name(blank)  # type: ignore[arg-type]


class TestBandMatrix:
    """The full board × risk-warning × regime matrix, one assertion each."""

    @pytest.mark.parametrize(("symbol", "board"), BOARD_CASES)
    def test_ordinary_name_band_is_board_band(self, symbol: str, board: str) -> None:
        expected = {
            "main": MAIN_BOARD_LIMIT,
            "cyb": GROWTH_BOARD_LIMIT,
            "kcb": GROWTH_BOARD_LIMIT,
            "bse": BEIJING_BOARD_LIMIT,
        }[board]
        assert price_limit_ratio(symbol) == expected

    @pytest.mark.parametrize(
        ("symbol", "expected"),
        [
            ("600519.SH", LEGACY_MAIN_BOARD_ST_LIMIT),
            ("000001.SZ", LEGACY_MAIN_BOARD_ST_LIMIT),
        ],
    )
    def test_main_board_risk_warning_is_legacy_pre_reform(
        self, symbol: str, expected: Decimal
    ) -> None:
        assert (
            price_limit_ratio(symbol, is_risk_warning=True, trade_date=LEGACY_DAY)
            == expected
        )

    @pytest.mark.parametrize(
        "symbol", ["300750.SZ", "301236.SZ", "688981.SH", "689009.SH"]
    )
    def test_growth_board_risk_warning_keeps_20_percent(self, symbol: str) -> None:
        # Never had a 5% band — not before the reform either.
        for day in (LEGACY_DAY, REFORM_DAY, None):
            assert (
                price_limit_ratio(symbol, is_risk_warning=True, trade_date=day)
                == GROWTH_BOARD_LIMIT
            )

    @pytest.mark.parametrize("symbol", ["830799.BJ", "430047.BJ", "920002.BJ"])
    def test_bse_risk_warning_keeps_30_percent(self, symbol: str) -> None:
        for day in (LEGACY_DAY, REFORM_DAY, None):
            assert (
                price_limit_ratio(symbol, is_risk_warning=True, trade_date=day)
                == BEIJING_BOARD_LIMIT
            )


class TestReformBoundary:
    def test_day_before_reform_is_legacy(self) -> None:
        assert (
            price_limit_ratio(
                "600519.SH", is_risk_warning=True, trade_date="2026-07-03"
            )
            == LEGACY_MAIN_BOARD_ST_LIMIT
        )

    def test_reform_day_is_the_new_band(self) -> None:
        assert (
            price_limit_ratio("600519.SH", is_risk_warning=True, trade_date=REFORM_DAY)
            == MAIN_BOARD_LIMIT
        )

    def test_constant_matches_the_asserted_dates(self) -> None:
        # Pins the boundary date itself rather than only its neighbourhood: a
        # moved constant would otherwise pass both tests above.
        assert MAIN_BOARD_ST_LIMIT_CHANGE_DATE == date(2026, 7, 6)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, False),
            (LEGACY_DAY, True),
            (REFORM_DAY, False),
            ("2026-07-05", True),
            ("2026-07-07", False),
        ],
    )
    def test_is_legacy_main_board_st(self, value: str | None, expected: bool) -> None:
        assert is_legacy_main_board_st("600519.SH", True, value) is expected

    def test_is_legacy_requires_risk_warning(self) -> None:
        assert not is_legacy_main_board_st("600519.SH", False, LEGACY_DAY)

    def test_is_legacy_requires_main_board(self) -> None:
        assert not is_legacy_main_board_st("688981.SH", True, LEGACY_DAY)
        assert not is_legacy_main_board_st("830799.BJ", True, LEGACY_DAY)


class TestDatelessMeansCurrentRulebook:
    """``trade_date=None`` must never resolve to the legacy band.

    A live caller with no session at hand is looking at today's tape. Falling
    back to 5% would under-price the band gate on a real order; this is the
    contract the module docstring states, asserted rather than assumed.
    """

    def test_main_board_risk_warning_dateless(self) -> None:
        assert price_limit_ratio("600519.SH", is_risk_warning=True) == (
            MAIN_BOARD_LIMIT
        )

    def test_limit_up_price_dateless(self) -> None:
        assert limit_up_price(
            Decimal("10.00"), "600519.SH", is_risk_warning=True
        ) == Decimal("11.00")

    def test_limit_down_price_dateless(self) -> None:
        assert limit_down_price(
            Decimal("10.00"), "600519.SH", is_risk_warning=True
        ) == Decimal("9.00")


class TestLimitPrices:
    def test_limit_up_rounds_half_up(self) -> None:
        # 10.005 * 1.1 = 11.0055 -> 11.01
        assert limit_up_price("10.005", "600519.SH") == Decimal("11.01")

    def test_limit_down_rounds_half_up(self) -> None:
        # 10.005 * 0.9 = 9.0045 -> 9.00 (half-up on 9.0045 is 9.00 at 2 dp)
        assert limit_down_price("10.005", "600519.SH") == Decimal("9.00")

    def test_growth_board_price(self) -> None:
        assert limit_up_price("50.00", "688981.SH") == Decimal("60.00")

    def test_bse_price(self) -> None:
        assert limit_up_price("10.00", "830799.BJ") == Decimal("13.00")

    def test_legacy_band_price(self) -> None:
        assert limit_up_price(
            "10.00", "600519.SH", is_risk_warning=True, trade_date=LEGACY_DAY
        ) == Decimal("10.50")
        assert limit_down_price(
            "10.00", "600519.SH", is_risk_warning=True, trade_date=LEGACY_DAY
        ) == Decimal("9.50")

    def test_float_and_str_inputs_agree(self) -> None:
        assert limit_up_price(10.0, "600519.SH") == limit_up_price("10.00", "600519.SH")

    def test_round_to_cent(self) -> None:
        assert round_to_cent(Decimal("11.0055")) == Decimal("11.01")
        assert round_to_cent(Decimal("11.0049")) == Decimal("11.00")


class TestCoerceDate:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (date(2026, 7, 6), date(2026, 7, 6)),
            (datetime(2026, 7, 6, 9, 30), date(2026, 7, 6)),
            ("2026-07-06", date(2026, 7, 6)),
            ("20260706", date(2026, 7, 6)),
            ("2026/07/06", date(2026, 7, 6)),
            ("  2026-07-06  ", date(2026, 7, 6)),
        ],
    )
    def test_parses_known_shapes(self, value: object, expected: date) -> None:
        assert coerce_date(value) == expected

    @pytest.mark.parametrize(
        "value",
        [None, "", "   ", "nan", "NaN", "NaT", "null", "None", "d0", "d1", 42],
    )
    def test_refuses_to_guess(self, value: object) -> None:
        # An unreadable date must read as "unknown" — the caller then gets the
        # current rulebook — never as a fabricated session.
        assert coerce_date(value) is None

    def test_nat_reads_as_unknown(self) -> None:
        import pandas as pd

        assert coerce_date(pd.NaT) is None
        assert coerce_date(pd.Timestamp("2026-07-06")) == date(2026, 7, 6)

    def test_impossible_date_is_unknown(self) -> None:
        assert coerce_date("2026-13-40") is None


class TestPriceLimitRuleContext:
    """``PriceLimitRule`` — the live pre-trade gate — reads the band rule."""

    # Nothing else in the suite instantiates this rule, so without this class
    # the order gate could regress to a hard-coded 5%-for-ST branch and stay
    # green. The three cases below pin the three ways the old rule was wrong.

    @staticmethod
    def _rule():
        from selene_space_risk.market_rules import PriceLimitRule

        return PriceLimitRule()

    @staticmethod
    def _intent(symbol: str, price: float):
        import time

        from selene_space_types.agent import TradeIntent
        from selene_space_types.trading import OrderSide, OrderType

        return TradeIntent(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=100.0,
            price=price,
            order_type=OrderType.LIMIT,
            reasoning="test",
            confidence=0.8,
            timestamp=int(time.time() * 1000),
        )

    def test_growth_board_is_twenty_percent_even_for_a_risk_warning_name(self) -> None:
        rule = self._rule()
        context = {
            "prev_close": {"300750.SZ": 10.0},
            "stock_names": {"300750.SZ": "ST宁德"},
        }
        assert rule.check(self._intent("300750.SZ", 11.5), context) is None
        assert rule.check(self._intent("300750.SZ", 12.5), context) is not None

    def test_main_board_risk_warning_band_follows_context_trade_date(self) -> None:
        rule = self._rule()
        names = {
            "stock_names": {"600001.SH": "ST测试"},
            "prev_close": {"600001.SH": 10.0},
        }
        # +5% is at the limit before the reform, comfortably inside it after.
        legacy = {**names, "trade_date": "2026-07-03"}
        assert rule.check(self._intent("600001.SH", 10.5), legacy) is None
        assert rule.check(self._intent("600001.SH", 10.6), legacy) is not None

        reformed = {**names, "trade_date": "2026-07-06"}
        assert rule.check(self._intent("600001.SH", 10.6), reformed) is None
        assert rule.check(self._intent("600001.SH", 11.1), reformed) is not None

    def test_beijing_board_is_recognised_at_thirty_percent(self) -> None:
        rule = self._rule()
        context = {"prev_close": {"830799.BJ": 10.0}, "stock_names": {}}
        assert rule.check(self._intent("830799.BJ", 12.9), context) is None
        assert rule.check(self._intent("830799.BJ", 13.1), context) is not None

"""Tests for ashare market rules: ST detection, board classification,
limit ratios/prices, suspension, and limit-touch checks."""

from decimal import Decimal

from selene_space_ashare.market_rules import (
    board_of,
    is_price_at_limit,
    is_suspended,
    is_st,
    limit_down_price,
    limit_ratio,
    limit_up_price,
)


class TestIsSt:
    def test_plain_name(self) -> None:
        assert not is_st("贵州茅台")

    def test_st_prefix(self) -> None:
        assert is_st("ST摘帽")

    def test_star_st(self) -> None:
        assert is_st("*ST某某")

    def test_lowercase_st_not_matched(self) -> None:
        assert not is_st("st某某")

    def test_delisting_marker(self) -> None:
        assert is_st("退市未来")
        assert is_st("某某退")

    def test_empty_name(self) -> None:
        assert not is_st("")


class TestBoardOf:
    def test_strips_suffix(self) -> None:
        assert board_of("600519.SH") == "main"
        assert board_of("300750.SZ") == "cyb"

    def test_main_sh(self) -> None:
        assert board_of("600519") == "main"

    def test_main_sz(self) -> None:
        assert board_of("000001") == "main"

    def test_kcb(self) -> None:
        assert board_of("688981") == "kcb"
        assert board_of("689009") == "kcb"

    def test_cyb(self) -> None:
        assert board_of("300750") == "cyb"
        assert board_of("301236") == "cyb"

    def test_bse(self) -> None:
        assert board_of("830799") == "bse"
        assert board_of("430047") == "bse"
        assert board_of("920002") == "bse"


class TestLimitRatio:
    def test_main(self) -> None:
        assert limit_ratio("600519") == Decimal("0.10")

    def test_kcb(self) -> None:
        assert limit_ratio("688981") == Decimal("0.20")

    def test_cyb(self) -> None:
        assert limit_ratio("300750") == Decimal("0.20")

    def test_bse(self) -> None:
        assert limit_ratio("830799") == Decimal("0.30")

    def test_st_overrides_board(self) -> None:
        assert limit_ratio("688981", "*ST某某") == Decimal("0.05")
        assert limit_ratio("600519", "ST某某") == Decimal("0.05")


class TestLimitPrices:
    def test_limit_up_main(self) -> None:
        assert limit_up_price(Decimal("10.00"), "600519") == Decimal("11.00")

    def test_limit_down_main(self) -> None:
        assert limit_down_price(Decimal("10.00"), "600519") == Decimal("9.00")

    def test_rounding_half_up_cent(self) -> None:
        # 10.005 * 1.1 = 11.0055 -> 11.01
        assert limit_up_price("10.005", "600519") == Decimal("11.01")
        # 10.005 * 0.9 = 9.0045 -> 9.00
        assert limit_down_price("10.005", "600519") == Decimal("9.00")

    def test_kcb_20_percent(self) -> None:
        assert limit_up_price(Decimal("50.00"), "688981") == Decimal("60.00")
        assert limit_down_price(Decimal("50.00"), "688981") == Decimal("40.00")

    def test_st_5_percent(self) -> None:
        assert limit_up_price(Decimal("10.00"), "600519", "ST某某") == Decimal("10.50")
        assert limit_down_price(Decimal("10.00"), "600519", "ST某某") == Decimal("9.50")

    def test_bse_30_percent(self) -> None:
        assert limit_up_price(Decimal("10.00"), "830799") == Decimal("13.00")

    def test_float_input_accepted(self) -> None:
        assert limit_up_price(10.0, "600519") == Decimal("11.00")


class TestIsSuspended:
    def test_none(self) -> None:
        assert is_suspended(None)

    def test_empty_string(self) -> None:
        assert is_suspended("")
        assert is_suspended("  ")

    def test_dash(self) -> None:
        assert is_suspended("-")

    def test_non_positive(self) -> None:
        assert is_suspended(0)
        assert is_suspended(-1.5)
        assert is_suspended("0.00")

    def test_valid_price(self) -> None:
        assert not is_suspended(Decimal("10.00"))
        assert not is_suspended("3.5")


class TestIsPriceAtLimit:
    def test_at_limit_up(self) -> None:
        assert is_price_at_limit(Decimal("11.00"), Decimal("10.00"), "600519")

    def test_at_limit_down(self) -> None:
        assert is_price_at_limit(Decimal("9.00"), Decimal("10.00"), "600519")

    def test_mid_price(self) -> None:
        assert not is_price_at_limit(Decimal("10.50"), Decimal("10.00"), "600519")

    def test_tolerance(self) -> None:
        assert is_price_at_limit(
            Decimal("11.00") + Decimal("1e-10"), Decimal("10.00"), "600519"
        )

    def test_with_st_name(self) -> None:
        assert is_price_at_limit(Decimal("10.50"), Decimal("10.00"), "600519", "ST某某")
        assert not is_price_at_limit(
            Decimal("11.00"), Decimal("10.00"), "600519", "ST某某"
        )

    def test_string_inputs(self) -> None:
        assert is_price_at_limit("11.00", "10.00", "600519")

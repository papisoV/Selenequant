"""Tests for the canonical A-share symbol ⇄ Sina prefix mapping.

The rule these tests pin is the one whose absence dropped all 349 Beijing
(``92xxxx``) codes from the whole-market daily backfill: ``_to_sina_symbol``
only knew ``SZ``/``SH`` and raised on ``.BJ``. Every board segment is covered
here so the shared function cannot regress to a two-board rule.

Each test names the mutation it is meant to catch — a test that cannot go red
under a plausible edit is not protecting anything.
"""

from __future__ import annotations

import pytest

from selene_space_core.sina_symbols import (
    code_to_symbol,
    sina_prefix,
    to_sina_symbol,
)


class TestSinaPrefix:
    """Board → URL prefix. Catches: any board falling through to a default."""

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("600519", "sh"),  # Shanghai main
            ("601811", "sh"),
            ("688981", "sh"),  # STAR (68xxxx) — still sh
            ("000001", "sz"),  # Shenzhen main
            ("002594", "sz"),
            ("300750", "sz"),  # ChiNext (30xxxx)
            ("301314", "sz"),
            ("920002", "bj"),  # BSE new range — the segment the old rule dropped
            ("920819", "bj"),
            ("430047", "bj"),  # BSE legacy 43xxxx
            ("830799", "bj"),  # BSE legacy 83xxxx
        ],
    )
    def test_board_maps_to_prefix(self, code: str, expected: str) -> None:
        assert sina_prefix(code) == expected

    @pytest.mark.parametrize(
        "bad",
        [
            "60051",  # 5 digits
            "6005199",  # 7 digits
            "60051A",  # non-digit
            "",  # empty
            "300750.SZ",  # suffixed form is not a bare code
        ],
    )
    def test_invalid_code_raises(self, bad: str) -> None:
        """Catches: silently defaulting a malformed entry to some board.

        Sina answers an empty payload for a mismatched prefix, so a silent
        default reads downstream as "no data" instead of "wrong code".
        """
        with pytest.raises(ValueError):
            sina_prefix(bad)


class TestCodeToSymbol:
    def test_maps_all_boards(self) -> None:
        assert code_to_symbol("600519") == "600519.SH"
        assert code_to_symbol("300750") == "300750.SZ"
        assert code_to_symbol("920002") == "920002.BJ"


class TestToSinaSymbol:
    """Suffixed symbol → Sina wire form. Catches: BJ missing from the map."""

    @pytest.mark.parametrize(
        ("symbol", "expected"),
        [
            ("000001.SZ", "sz000001"),
            ("600519.SH", "sh600519"),
            ("300750.SZ", "sz300750"),
            ("920002.BJ", "bj920002"),  # regression: old map raised here
            ("430047.BJ", "bj430047"),
            ("688981.SH", "sh688981"),
        ],
    )
    def test_suffixed_symbol_to_wire(self, symbol: str, expected: str) -> None:
        assert to_sina_symbol(symbol) == expected

    def test_suffix_is_case_insensitive(self) -> None:
        assert to_sina_symbol("000001.sz") == "sz000001"
        assert to_sina_symbol("920002.bj") == "bj920002"

    @pytest.mark.parametrize(
        "bad",
        [
            "300750",  # bare code — no suffix to disambiguate the board
            "300750.XX",  # unknown suffix
            "300750.",  # empty suffix
            "",  # empty
        ],
    )
    def test_rejects_bare_and_unknown(self, bad: str) -> None:
        with pytest.raises(ValueError):
            to_sina_symbol(bad)

    @pytest.mark.parametrize(
        "mismatched",
        [
            "920002.SZ",  # BSE code wearing a Shenzhen suffix
            "600519.SZ",  # Shanghai code wearing a Shenzhen suffix
            "300750.SH",  # Shenzhen code wearing a Shanghai suffix
        ],
    )
    def test_rejects_suffix_code_mismatch(self, mismatched: str) -> None:
        """Catches: trusting the caller's suffix over the code's actual board.

        A mismatched pair would poll a real venue for a symbol that does not
        exist there, which Sina answers as an empty payload — a silent drop.
        """
        with pytest.raises(ValueError):
            to_sina_symbol(mismatched)


class TestRoundTrip:
    def test_symbol_and_code_agree_on_board(self) -> None:
        """``code_to_symbol`` then ``to_sina_symbol`` round-trips every board."""
        for code in ("600519", "000001", "300750", "688981", "920002", "430047"):
            assert to_sina_symbol(code_to_symbol(code)) == f"{sina_prefix(code)}{code}"

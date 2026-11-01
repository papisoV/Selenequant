# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""Index quote parsing tests — no network.

The property worth pinning: an index is identified **only** by its
exchange-qualified Sina string. ``000001`` is both 上证指数 and 平安银行, so
the table is the authority and anything off-table is ignored rather than
guessed at.
"""

from __future__ import annotations

import pytest

from selene_space_ashare.index_quotes import (
    CORE_INDEX_NAMES,
    CORE_INDEX_SYMBOLS,
    IndexQuote,
    _sina_code,
    parse_index_response,
)


def _line(
    sina: str, name: str, prev: str, last: str, *, date="2026-10-20", tm="15:00:00"
):
    fields = [name, prev, prev, last, last, prev] + [""] * 24 + [date, tm]
    return f'var hq_str_{sina}="{",".join(fields)}";'


def test_table_covers_four_distinct_indices():
    assert CORE_INDEX_SYMBOLS == ("000001.SH", "399001.SZ", "399006.SZ", "000680.SH")
    assert set(CORE_INDEX_NAMES) == set(CORE_INDEX_SYMBOLS)
    # 000680 科创综指 — not 000688 科创50, which is a different index.
    assert "000680.SH" in CORE_INDEX_NAMES
    assert "000688.SH" not in CORE_INDEX_NAMES


def test_sina_code_is_exchange_qualified():
    assert _sina_code("000001.SH") == "sh000001"
    assert _sina_code("399001.SZ") == "sz399001"
    # The trap: 000001.SZ is a Shenzhen *stock*. Index mapping is keyed by the
    # full symbol, so the same bare code on the other exchange is off-table.
    with pytest.raises(ValueError):
        _sina_code("000001.SZ")


def test_parse_reads_last_and_computes_change():
    text = _line("sh000001", "上证指数", "3300.00", "3333.00") + "\n"
    out = parse_index_response(text)

    quote = out["000001.SH"]
    assert isinstance(quote, IndexQuote)
    assert quote.last == 3333.0
    assert quote.prev_close == 3300.0
    assert quote.change == pytest.approx(33.0)
    assert quote.change_pct == pytest.approx(1.0)  # percent number, not fraction
    assert quote.name == "上证指数"
    assert quote.date == "2026-10-20"
    assert quote.time == "15:00:00"


def test_parse_ignores_off_table_rows():
    text = "\n".join(
        [
            _line("sh000001", "上证指数", "3300", "3333"),
            _line("sh000688", "科创50", "900", "910"),  # not a core index
            _line("sh600519", "贵州茅台", "1500", "1510"),  # a stock
        ]
    )
    out = parse_index_response(text)

    assert set(out) == {"000001.SH"}
    # ...and the request would never have asked for them.
    assert "sh000688" not in {_sina_code(s) for s in CORE_INDEX_SYMBOLS}


def test_zero_price_row_is_omitted_not_flat():
    """A pre-open 0 must not surface as a 0.00% "flat" statement."""
    text = _line("sh000001", "上证指数", "3300", "0") + "\n"
    assert parse_index_response(text) == {}


def test_short_payload_is_ignored():
    """Fewer than 32 fields means the tail slots are not there to read."""
    text = 'var hq_str_sh000001="上证指数,1,3300,3333";\n'
    assert parse_index_response(text) == {}


def test_blank_name_falls_back_to_table():
    text = _line("sz399006", "", "2000", "2020") + "\n"
    out = parse_index_response(text)
    assert out["399006.SZ"].name == CORE_INDEX_NAMES["399006.SZ"]


def test_garbage_lines_are_skipped():
    assert parse_index_response("") == {}
    assert parse_index_response("not a quote at all\n\n") == {}

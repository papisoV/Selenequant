"""Tests for funding-rate → (ts_ms, rate) pair conversion (P3-F-04).

``FundingRateItem.to_funding_pair`` and module-level ``to_funding_pairs`` are
the adapter seam between ``FundingRateFetcher.get_history`` results and the
factor-research funding pipeline (``HistoricalFundingModel``). Verifies field
selection, empty input, ascending sort, and timestamp de-duplication.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

from selene_space_market.funding_rate_fetcher import (
    FundingRateItem,
    to_funding_pairs,
)


def _item(
    exchange: str = "binance",
    symbol: str = "BTC/USDT",
    funding_rate: float = 0.001,
    mark_price: float = 0.0,
    index_price: float = 0.0,
    next_funding_time: int = 0,
    timestamp: int = 0,
    **kwargs: Any,
) -> FundingRateItem:
    return FundingRateItem(
        exchange=exchange,
        symbol=symbol,
        funding_rate=funding_rate,
        mark_price=mark_price,
        index_price=index_price,
        next_funding_time=next_funding_time,
        timestamp=timestamp,
        **kwargs,
    )


def test_to_funding_pair_fields() -> None:
    it = _item(timestamp=1234, funding_rate=0.0005)
    assert it.to_funding_pair() == (1234, 0.0005)


def test_to_funding_pairs_hand_computed() -> None:
    items = [
        _item(timestamp=2000, funding_rate=0.002),
        _item(timestamp=1000, funding_rate=0.001),
    ]
    assert to_funding_pairs(items) == [(1000, 0.001), (2000, 0.002)]


def test_to_funding_pairs_empty() -> None:
    assert to_funding_pairs([]) == []


def test_to_funding_pairs_sorts_unsorted_input() -> None:
    items = [
        _item(timestamp=3000, funding_rate=0.003),
        _item(timestamp=1000, funding_rate=0.001),
        _item(timestamp=2000, funding_rate=0.002),
    ]
    pairs = to_funding_pairs(items)
    assert pairs == [(1000, 0.001), (2000, 0.002), (3000, 0.003)]
    assert [p[0] for p in pairs] == sorted(p[0] for p in pairs)


def test_to_funding_pairs_dedup_timestamp_keeps_first() -> None:
    items = [
        _item(timestamp=1000, funding_rate=0.001),
        _item(timestamp=1000, funding_rate=0.999),
        _item(timestamp=2000, funding_rate=0.002),
    ]
    assert to_funding_pairs(items) == [(1000, 0.001), (2000, 0.002)]


def test_source_module_does_not_import_factor_research() -> None:
    """market-data stays one-directional: no factor-research import anywhere."""
    src = pathlib.Path(__file__).parent.parent / "selene_space_market" / "funding_rate_fetcher.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                assert "factor_research" not in alias.name, f"banned import {alias.name}"
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "factor_research" not in node.module


def test_testfile_imports_no_torch_or_polars() -> None:
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in ("torch", "polars"), f"banned import {alias.name}"

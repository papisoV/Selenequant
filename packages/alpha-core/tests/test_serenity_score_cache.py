"""Tests for SerenityScoreCache theme-pool rank gate (B1).

Covers the set_pool_filter API: filter-off regression lock, soft strength
scaling, hard direction zeroing, best-position multi-theme rank, and
out-of-pool None.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Direct imports bypassing selene_space_alpha.__init__ (which triggers torch)
_alpha_core = str(Path(__file__).parent.parent / "selene_space_alpha")
if _alpha_core not in sys.path:
    sys.path.insert(0, _alpha_core)

from serenity.pool import PoolCandidate, SerenityPoolScan  # noqa: E402
from serenity.score_cache import SerenityScoreCache  # noqa: E402


def _candidate(symbol: str, scarce: float) -> PoolCandidate:
    return PoolCandidate(
        symbol=symbol, name=symbol, category="Lending", chains=("eth",),
        normalized_score=0.7, tier="bullish", scarce_layer_score=scarce,
        evidence_strength=0.5, evidence_label="强", rationale="x",
        top_penalty="fork_risk:0.5",
    )


def _scan(theme: str, symbols: tuple[str, ...]) -> SerenityPoolScan:
    scarce = [round(1.0 - i * 0.2, 2) for i in range(len(symbols))]
    return SerenityPoolScan(
        theme=theme, theme_name=theme.title(), scanned_at=1735689600000,
        universe_size=len(symbols), layer_summary=(),
        ranked=tuple(_candidate(s, scarce[i]) for i, s in enumerate(symbols)),
        top_picks=(symbols[0],), weak_evidence_count=0,
    )


def _lending_scan() -> SerenityPoolScan:
    """3-candidate pool: AAA rank 1, BBB rank 2, CCC rank 3."""
    return _scan("lending", ("AAA", "BBB", "CCC"))


def _cache(scores: dict[str, float]) -> SerenityScoreCache:
    cache = SerenityScoreCache(workflow=object())
    cache._scores = {
        sym: {"normalized_score": ns} for sym, ns in scores.items()
    }
    return cache


def test_pool_filter_off_matches_no_filter():
    """Regression lock: set_pool_filter(None) / mode="off" leave direction+strength identical."""
    scores = {"AAA": 0.9, "BBB": 0.3, "ZZZ": 0.7, "UNK": 0.5}
    baseline = _cache(scores)
    none_filter = _cache(scores)
    none_filter.set_pool_filter(None, mode="hard")
    off_filter = _cache(scores)
    off_filter.set_pool_filter({"lending": _lending_scan()}, mode="off", top_n=1)

    for sym in scores:
        assert none_filter.get_direction(sym) == baseline.get_direction(sym)
        assert none_filter.get_strength(sym) == baseline.get_strength(sym)
        assert off_filter.get_direction(sym) == baseline.get_direction(sym)
        assert off_filter.get_strength(sym) == baseline.get_strength(sym)


def test_soft_strength_scaled_by_pool_rank():
    cache = _cache({"AAA": 0.9, "BBB": 0.9, "ZZZ": 0.9})
    cache.set_pool_filter({"lending": _lending_scan()}, mode="soft")
    # AAA rank 1/3 -> 1.0 (full strength); BBB rank 2/3 -> 2/3; out-of-pool -> 0.5.
    assert cache.get_strength("AAA") == pytest.approx(0.8)
    assert cache.get_strength("BBB") == pytest.approx(0.8 * (2.0 / 3.0))
    assert cache.get_strength("ZZZ") == pytest.approx(0.8 * 0.5)


def test_soft_direction_unchanged():
    baseline = _cache({"AAA": 0.9, "BBB": 0.3, "ZZZ": 0.9})
    soft = _cache({"AAA": 0.9, "BBB": 0.3, "ZZZ": 0.9})
    soft.set_pool_filter({"lending": _lending_scan()}, mode="soft")
    for sym in ("AAA", "BBB", "ZZZ"):
        assert soft.get_direction(sym) == baseline.get_direction(sym)


def test_hard_direction_zero_outside_top_n():
    five = _scan("dex", ("A1", "B2", "C3", "D4", "E5"))
    cache = _cache({"A1": 0.9, "B2": 0.1, "D4": 0.9, "E5": 0.1, "ZZZ": 0.9})
    cache.set_pool_filter({"dex": five}, mode="hard", top_n=3)
    assert cache.get_direction("A1") == 1     # rank 1 <= 3, score-driven bullish
    assert cache.get_direction("B2") == -1    # rank 2 <= 3, score-driven bearish
    assert cache.get_direction("D4") == 0     # rank 4 > 3, veto lost despite bullish
    assert cache.get_direction("E5") == 0     # rank 5 > 3, veto lost despite bearish
    assert cache.get_direction("ZZZ") == 0    # not in any pool


def test_get_pool_rank_best_position_multi_theme():
    # AAA in lending at rank 1 (size 3) and in yield at rank 2 (size 4).
    yield_scan = _scan("yield", ("BBB", "AAA", "CCC", "DDD"))
    cache = _cache({})
    cache.set_pool_filter(
        {"lending": _lending_scan(), "yield": yield_scan}, mode="soft"
    )
    assert cache.get_pool_rank("AAA") == pytest.approx(1.0)          # best rank 1/3
    assert cache.get_pool_rank("BBB") == pytest.approx(1.0 - 0 / 4)  # rank 1/4
    assert cache.get_pool_rank("CCC") == pytest.approx(1.0 - 2 / 3)  # rank 3/3 lending
    assert cache.get_pool_rank("ZZZ") is None


def test_get_pool_rank_none_outside_pool():
    cache = _cache({})
    cache.set_pool_filter({"lending": _lending_scan()}, mode="soft")
    assert cache.get_pool_rank("AAA") is not None
    assert cache.get_pool_rank("UNLISTED") is None


def test_set_pool_filter_off_clears_rank():
    cache = _cache({"AAA": 0.9})
    cache.set_pool_filter({"lending": _lending_scan()}, mode="soft")
    assert cache.get_pool_rank("AAA") is not None
    cache.set_pool_filter(None, mode="soft")
    assert cache.get_pool_rank("AAA") is None
    assert cache._pool_mode == "off"


def test_set_pool_filter_default_top_n_is_seven():
    """B1 config-align: default top_n is 7 (matches pool_top_n=7 config), not 5."""
    eight = _scan("dex", ("A1", "B2", "C3", "D4", "E5", "F6", "G7", "H8"))
    cache = _cache({"G7": 0.9, "H8": 0.9, "ZZZ": 0.9})
    cache.set_pool_filter({"dex": eight}, mode="hard")
    # Default top_n = 7: rank 7 keeps its score-driven direction, rank 8 loses veto.
    assert cache.get_direction("G7") == 1
    assert cache.get_direction("H8") == 0
    assert cache.get_direction("ZZZ") == 0
    assert cache._pool_top_n == 7

    # Explicit top_n still overrides the default.
    cache.set_pool_filter({"dex": eight}, mode="hard", top_n=3)
    assert cache._pool_top_n == 3

    # mode="off" resets to the aligned default.
    cache.set_pool_filter(None, mode="soft")
    assert cache._pool_top_n == 7


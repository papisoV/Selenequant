"""C2: theme candidate-pool scanner.

Covers universe building (category/chain gating, curated padding, dead /
misrepresented / tiny-TVL drop, symbol dedup), scarce-layer-first ranking
(structural factors beat demand momentum), evidence gating (weak-evidence
candidates stay in the full ranking but never reach top_picks), layer summary,
and the unknown-theme / empty-universe error paths.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_alpha_core = str(Path(__file__).parent.parent / "selene_space_alpha")
if _alpha_core not in sys.path:
    sys.path.insert(0, _alpha_core)

from serenity.models import GitHubActivity, ProtocolMetrics, SerenitySnapshot  # noqa: E402
from serenity.pool import PoolCandidate, SerenityPoolScanner, THEMES, ThemeSpec  # noqa: E402
from serenity.pool_db import SerenityPoolDB  # noqa: E402
from serenity.scorer import SerenityScorer  # noqa: E402


def _snapshot(
    symbol,
    *,
    tvl=1e9,
    mcap_to_tvl=1.0,
    fdv_to_tvl=2.0,
    revenue_7d=1e6,
    tvl_change_7d=0.05,
    chains=("eth",),
    category="Lending",
    github=True,
    funding=True,
):
    pm = ProtocolMetrics(
        protocol_id=symbol.lower(),
        name=symbol,
        category=category,
        chains=chains,
        tvl_usd=tvl,
        tvl_change_1d=0.0,
        tvl_change_7d=tvl_change_7d,
        mcap_to_tvl=mcap_to_tvl,
        fdv_to_tvl=fdv_to_tvl,
        revenue_24h=revenue_7d / 7.0,
        revenue_7d=revenue_7d,
        timestamp=1,
    )
    ga = None
    if github:
        ga = GitHubActivity(
            protocol_id=symbol.lower(),
            repo_name=f"owner/{symbol.lower()}",
            stars=100000,
            commits_4w=200,
            active_devs_4w=50,
            open_issues=10,
            closed_issues_4w=20,
            timestamp=1,
        )
    return SerenitySnapshot(
        symbol=symbol,
        protocol_metrics=pm,
        github_activity=ga,
        funding_rate=0.01 if funding else None,
        oi_change=None,
        ls_ratio=None,
        tvl_chain_share=1.0,
        price_change_7d=None,
        volume_change_7d=None,
        timestamp=1,
    )


def _empty_snapshot(symbol):
    return SerenitySnapshot(
        symbol=symbol,
        protocol_metrics=None,
        github_activity=None,
        funding_rate=None,
        oi_change=None,
        ls_ratio=None,
        tvl_chain_share=0.0,
        price_change_7d=None,
        volume_change_7d=None,
        timestamp=1,
    )


def _entry(symbol, category="Lending", tvl=1e8, dead=False, misrep=False, chains=("eth",)):
    return {
        "id": f"{symbol.lower()}-id",
        "symbol": symbol,
        "category": category,
        "chains": list(chains),
        "tvl": tvl,
        "dead": dead,
        "misrepresentedTokens": misrep,
    }


class _FakeFetcher:
    """Stub of the fetcher surface the pool scanner uses."""

    def __init__(self, protocols, snapshots):
        self._protocols = protocols
        self._snapshots = snapshots

    async def get_protocols(self):
        return self._protocols

    async def get_pool_snapshot(self, entry, fetch_github=True):
        return self._snapshots[str(entry.get("symbol", "")).upper()]


def _scanner(protocols, snapshots):
    return SerenityPoolScanner(_FakeFetcher(protocols, snapshots), SerenityScorer())


# --- universe building ------------------------------------------------------


def test_build_universe_filters_dead_misrep_lowtvl_and_dedups():
    protocols = [
        _entry("AAA", tvl=1e8),
        _entry("BBBB", dead=True),  # dead dropped
        _entry("CCCC", misrep=True),  # misrepresented dropped
        _entry("DDDD", tvl=1e3),  # tiny TVL dropped
        _entry("EEE", category="DEX", tvl=1e8),  # wrong category dropped
        _entry("FFF", tvl=1e6),
        _entry("DUP", tvl=1e6),  # duplicate symbol, low TVL
        _entry("DUP", tvl=1e8),  # duplicate symbol, high TVL kept
    ]
    universe = asyncio.run(_scanner(protocols, {})._build_universe("lending"))
    assert {e["symbol"] for e in universe} == {"AAA", "FFF", "DUP"}
    dup = next(e for e in universe if e["symbol"] == "DUP")
    assert dup["tvl"] == 1e8


def test_build_universe_l2_gates_chain_category():
    protocols = [
        _entry("ARB", category="Chain", chains=["Arbitrum"], tvl=1e8),
        _entry("OP", category="Chain", chains=["Optimism"], tvl=1e8),
        _entry("ETH", category="Chain", chains=["Ethereum"], tvl=1e9),  # L1 excluded
        _entry("SOL", category="Chain", chains=["Solana"], tvl=1e8),  # L1 excluded
    ]
    universe = asyncio.run(_scanner(protocols, {})._build_universe("l2"))
    assert {e["symbol"] for e in universe} == {"ARB", "OP"}


def test_build_universe_pads_curated_symbols(monkeypatch):
    monkeypatch.setitem(
        THEMES,
        "test_theme",
        ThemeSpec(name="Test", categories=("Lending",), curated_symbols=("AAA", "PAD")),
    )
    protocols = [
        _entry("AAA", tvl=1e8),  # category match
        _entry("BBB", category="DEX", tvl=1e8),  # category miss
        _entry("PAD", category="Yield", tvl=1e8),  # curated pad, category miss
    ]
    universe = asyncio.run(_scanner(protocols, {})._build_universe("test_theme"))
    assert {e["symbol"] for e in universe} == {"AAA", "PAD"}


def test_unknown_theme_raises_value_error():
    with pytest.raises(ValueError, match="unknown theme"):
        asyncio.run(_scanner([], {})._build_universe("bogus"))


# --- ranking -----------------------------------------------------------------


def test_scan_ranks_scarce_layer_before_momentum():
    aa = _snapshot(
        "AAA",
        tvl=1e9,
        mcap_to_tvl=0.5,
        revenue_7d=1e6,
        tvl_change_7d=0.05,
        chains=("eth", "arb", "op", "base", "sol"),
    )
    bb = _snapshot(
        "BBB",
        tvl=1e7,
        mcap_to_tvl=10.0,
        revenue_7d=1e7,
        tvl_change_7d=0.25,
        chains=("eth",),
        github=False,
    )
    scan = asyncio.run(_scanner([_entry("AAA"), _entry("BBB")], {"AAA": aa, "BBB": bb}).scan("lending", top_n=2))

    score_a = SerenityScorer().score(aa)
    score_b = SerenityScorer().score(bb)
    # BBB has the demand-momentum edge but a weaker structural moat.
    assert score_a.prepayment_signal < score_b.prepayment_signal
    assert scan.ranked[0].symbol == "AAA"
    assert scan.ranked[0].scarce_layer_score > scan.ranked[1].scarce_layer_score
    assert scan.ranked[1].symbol == "BBB"
    assert scan.theme == "lending"
    assert scan.theme_name == "Lending"


def test_scan_gates_weak_evidence_from_top_picks():
    strong = _snapshot("STRONG")
    weak = _empty_snapshot("WEAK")
    scan = asyncio.run(
        _scanner([_entry("STRONG"), _entry("WEAK")], {"STRONG": strong, "WEAK": weak}).scan(
            "lending", top_n=5
        )
    )
    assert [c.symbol for c in scan.ranked] == ["STRONG", "WEAK"]
    assert "STRONG" in scan.top_picks
    assert "WEAK" not in scan.top_picks  # weak evidence, still listed
    assert scan.weak_evidence_count == 1

    strong_c = next(c for c in scan.ranked if c.symbol == "STRONG")
    weak_c = next(c for c in scan.ranked if c.symbol == "WEAK")
    assert strong_c.evidence_strength == pytest.approx(0.7)
    assert strong_c.evidence_label == "强"
    assert strong_c.disproof  # eligible -> bear case attached
    assert weak_c.evidence_strength == 0.0
    assert weak_c.evidence_label == "弱"
    assert weak_c.disproof == ()  # ineligible -> no bear case


def test_scan_empty_universe_returns_empty_scan():
    scan = asyncio.run(_scanner([], {}).scan("lending"))
    assert scan.universe_size == 0
    assert scan.ranked == ()
    assert scan.top_picks == ()
    assert scan.layer_summary == ()


# --- helpers ------------------------------------------------------------------


def test_layer_summary_groups_by_category():
    scanner = _scanner([], {})
    candidates = [
        PoolCandidate(symbol="AAA", name="A", category="Lending", chains=("eth",),
                      normalized_score=0.6, tier="bullish", scarce_layer_score=0.8,
                      evidence_strength=0.5, evidence_label="强", rationale="x",
                      top_penalty="fork_risk:0.55"),
        PoolCandidate(symbol="BBB", name="B", category="Lending", chains=("eth",),
                      normalized_score=0.5, tier="bullish", scarce_layer_score=0.7,
                      evidence_strength=0.5, evidence_label="强", rationale="x",
                      top_penalty="fork_risk:0.55"),
        PoolCandidate(symbol="CCC", name="C", category="DEX", chains=("eth",),
                      normalized_score=0.6, tier="bullish", scarce_layer_score=0.9,
                      evidence_strength=0.5, evidence_label="强", rationale="x",
                      top_penalty="fork_risk:0.55"),
    ]
    summary = scanner._layer_summary(candidates)
    assert summary[0].layer == "Lending"  # 2 members, listed first
    assert summary[0].count == 2
    assert summary[0].top_scarce_symbols[0] == "AAA"  # 0.8 > 0.7 within Lending
    assert summary[1].layer == "DEX"


def test_evidence_label_thresholds():
    scanner = _scanner([], {})
    assert scanner._evidence_label(0.9) == "强"
    assert scanner._evidence_label(0.3) == "中"
    assert scanner._evidence_label(0.05) == "弱"


# --- pool_db persistence (C2) --------------------------------------------------


def test_scan_persists_to_pool_db_when_provided(tmp_path):
    db = SerenityPoolDB(db_path=tmp_path / "pool.db")
    strong = _snapshot("STRONG")
    weak = _empty_snapshot("WEAK")
    scanner = SerenityPoolScanner(
        _FakeFetcher(
            [_entry("STRONG"), _entry("WEAK")],
            {"STRONG": strong, "WEAK": weak},
        ),
        SerenityScorer(),
        pool_db=db,
    )
    scan = asyncio.run(scanner.scan("lending", top_n=5))
    assert scan.universe_size == 2
    assert db.count() == 1

    restored = db.get_latest("lending")
    assert restored is not None
    assert restored.theme_name == "Lending"
    assert [c.symbol for c in restored.ranked] == ["STRONG", "WEAK"]
    assert restored.top_picks == ("STRONG",)
    db.close()


def test_scan_without_pool_db_is_compatible():
    # No pool_db passed: scan still works, nothing persisted (no crash).
    scan = asyncio.run(_scanner([], {}).scan("lending"))
    assert scan.universe_size == 0
    assert scan.ranked == ()

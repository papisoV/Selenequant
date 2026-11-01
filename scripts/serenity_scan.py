"""Serenity fundamental analysis scanner.

Usage:
    python scripts/serenity_scan.py
    python scripts/serenity_scan.py --symbol ETH
    python scripts/serenity_scan.py --deep
    python scripts/serenity_scan.py --export-factors
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))

# Bypass parent __init__.py which triggers torch import (may fail on some systems)
import types as _types

if "selene_space_alpha" not in sys.modules:
    _parent = _types.ModuleType("selene_space_alpha")
    _parent.__path__ = [
        str(PROJECT_ROOT / "packages" / "alpha-core" / "selene_space_alpha")
    ]
    _parent.__package__ = "selene_space_alpha"
    sys.modules["selene_space_alpha"] = _parent

from selene_space_alpha.serenity.score_db import SerenityScoreDB  # noqa: E402
from selene_space_alpha.serenity import (
    SerenityConfig,
    SerenityFetcher,
    SerenityResearchReport,
    SerenityScorer,
    SerenityWorkflow,
)

DEFAULT_SYMBOLS = [
    "BTC",
    "ETH",
    "SOL",
    "ARB",
    "OP",
    "AVAX",
    "LINK",
    "UNI",
    "AAVE",
]


async def scan_symbols(
    symbols: list[str],
    deep: bool,
    export_factors: bool,
    proxy_url: str | None = None,
) -> None:
    config = SerenityConfig()

    # Open local DBs for data enrichment
    from selene_space_market.derivatives_db import DerivativesDB
    from selene_space_market.kline_db import KlineDB
    from selene_space_alpha.serenity.score_db import SerenityScoreDB

    # 单一解析来源:此前硬编码 services/local-api/data/derivatives.db —
    # 那正是后端 CWD 相对路径造出的空壳库,读它等于永远读不到资金费率。
    from selene_space_core.derivatives_paths import default_derivatives_db_path
    from selene_space_core.kline_paths import default_klines_db_path

    deriv_db = DerivativesDB(db_path=default_derivatives_db_path())
    kline_db = KlineDB(db_path=default_klines_db_path())
    score_db = SerenityScoreDB()

    try:
        deriv_db.open()
        kline_db.open()
        score_db.open()

        fetcher = SerenityFetcher(
            config=config,
            proxy_url=proxy_url,
            derivatives_db=deriv_db,
            kline_db=kline_db,
        )
        scorer = SerenityScorer(config=config)
        workflow = SerenityWorkflow(fetcher, scorer, config)

        print(f"\n{'Symbol':>8}  {'Score':>6}  {'Tier':>16}  {'Dir':>4}  {'Narrative'}")
        print(f"{'------':>8}  {'-----':>6}  {'----':>16}  {'---':>4}  {'---------'}")

        for i, symbol in enumerate(symbols):
            try:
                # Rate limit: pause between symbols to avoid GitHub API throttling
                if i > 0:
                    await asyncio.sleep(2.0)

                if deep:
                    report = await workflow.scan_and_verify(symbol)
                else:
                    report = await workflow.quick_scan(symbol)

                score = (
                    report.tier2.normalized_score
                    if report.tier2
                    else report.tier1.normalized_score
                )
                tier = report.tier2.tier if report.tier2 else report.tier1.tier
                direction = {1: "LONG", -1: "SHRT", 0: "----"}[report.signal_direction]
                narrative = report.narrative[:70]

                print(
                    f"{symbol:>8}  {score:>6.3f}  {tier:>16}  {direction:>4}  {narrative}"
                )

                if deep and report.tier2:
                    _print_detail(report)

                if export_factors:
                    _export_factor_file(symbol, report)

                # Always persist score to SQLite
                _persist_score_to_db(score_db, report)

            except Exception as e:
                print(f"{symbol:>8}  ERROR: {e}")

    finally:
        await fetcher.close()
        deriv_db.close()
        kline_db.close()
        score_db.close()


def _print_detail(report: SerenityResearchReport) -> None:
    """Print detailed factor breakdown for deep scan."""
    score = report.tier2 or report.tier1
    print(f"\n  --- {report.symbol} Factor Breakdown ---")
    print("  Factors:")
    print(f"    consensus_scarcity:     {score.consensus_scarcity:.3f}")
    print(f"    liquidity_scarcity:     {score.liquidity_scarcity:.3f}")
    print(f"    trust_scarcity:         {score.trust_scarcity:.3f}")
    print(f"    replication_difficulty: {score.replication_difficulty:.3f}")
    print(f"    certification_progress: {score.certification_progress:.3f}")
    print(f"    prepayment_signal:      {score.prepayment_signal:.3f}")
    print(f"    decentralization:      {score.decentralization:.3f}")
    print(f"    evidence_strength:      {score.evidence_strength:.3f}")
    print("  Penalties:")
    print(f"    fork_risk:          {score.penalty_fork_risk:.3f}")
    print(f"    tvl_concentration:  {score.penalty_tvl_concentration:.3f}")
    print(f"    dev_exodus:         {score.penalty_dev_exodus:.3f}")
    print(f"    revenue_decline:    {score.penalty_revenue_decline:.3f}")
    print(f"    oi_divergence:      {score.penalty_oi_divergence:.3f}")
    print(f"    whale_exodus:       {score.penalty_whale_exodus:.3f}")
    print(f"    governance_risk:    {score.penalty_governance_risk:.3f}")
    print(f"    liquidity_trap:     {score.penalty_liquidity_trap:.3f}")
    print(
        f"  Composite: raw={score.raw_score:.4f} normalized={score.normalized_score:.4f}"
    )

    if report.scarce_constraints:
        print(f"  Scarce constraints: {', '.join(report.scarce_constraints)}")
    if report.blind_spots:
        print(f"  Blind spots: {', '.join(report.blind_spots)}")
    if report.disproof_conditions:
        print(f"  Disproof: {', '.join(report.disproof_conditions[:2])}")
    print()


def _export_factor_file(symbol: str, report: SerenityResearchReport) -> None:
    """Export Serenity scores as JSON for Optuna pipeline."""
    score = report.tier2 or report.tier1
    data = {
        "symbol": symbol,
        "timestamp": score.timestamp,
        "factors": {
            "SERENITY_SCORE": score.normalized_score,
            "SERENITY_CONSENSUS": score.consensus_scarcity,
            "SERENITY_LIQUIDITY": score.liquidity_scarcity,
            "SERENITY_PREPAY": score.prepayment_signal,
            "SERENITY_EVIDENCE": score.evidence_strength,
            "SERENITY_PENALTY": (
                score.penalty_fork_risk
                + score.penalty_tvl_concentration
                + score.penalty_dev_exodus
                + score.penalty_revenue_decline
                + score.penalty_oi_divergence
                + score.penalty_whale_exodus
                + score.penalty_governance_risk
                + score.penalty_liquidity_trap
            )
            / 8.0,
        },
        "tier": score.tier,
        "signal_direction": report.signal_direction,
        "signal_strength": report.signal_strength,
    }

    out_dir = PROJECT_ROOT / "data" / "serenity"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{symbol.lower()}_score.json"
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"  -> Exported factors to {out_path}")


def _persist_score_to_db(
    score_db: SerenityScoreDB, report: SerenityResearchReport
) -> None:
    """Persist Serenity score to local SQLite."""
    try:
        score_db.put_score(report.tier1)
        if report.tier2 is not None:
            score_db.put_score(report.tier2)
    except Exception:
        print("  Warning: score persist failed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Serenity fundamental scanner")
    parser.add_argument(
        "--symbol", type=str, default="all", help="Symbol to scan (default: all)"
    )
    parser.add_argument(
        "--deep", action="store_true", help="Run deep verification (Tier 1 + Tier 2)"
    )
    parser.add_argument(
        "--export-factors",
        action="store_true",
        help="Export scores as factors for Optuna pipeline",
    )
    parser.add_argument(
        "--proxy",
        type=str,
        default="",
        help="Proxy URL for API access (e.g. http://<host>:<port>)",
    )
    args = parser.parse_args()

    symbols = DEFAULT_SYMBOLS if args.symbol == "all" else [args.symbol.upper()]
    proxy_url = (
        args.proxy
        or os.environ.get("SELENE_PROXY_URL")
        or os.environ.get("https_proxy")
    )
    asyncio.run(scan_symbols(symbols, args.deep, args.export_factors, proxy_url))


if __name__ == "__main__":
    main()

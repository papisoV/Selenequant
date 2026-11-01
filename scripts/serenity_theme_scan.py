"""Serenity theme candidate-pool scanner (CLI mirror of the pool API).

Builds a >=20 candidate universe from DeFi Llama /protocols for a theme,
scores each with the 8-factor scorer, ranks scarce-layer-first, and prints the
layer summary + full ranking + top picks.

Usage:
    python scripts/serenity_theme_scan.py --themes
    python scripts/serenity_theme_scan.py --theme lending
    python scripts/serenity_theme_scan.py --theme l2 --top-n 5 --limit 25
    python scripts/serenity_theme_scan.py --theme ai --no-github
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))

# Bypass parent __init__.py which triggers torch import (may fail on some systems).
# is_dir() guards against an off-by-one silently loading the editable-install
# copy from another replica instead of this checkout.
import types as _types  # noqa: E402

_ALPHA_CORE = PROJECT_ROOT / "packages" / "alpha-core" / "selene_space_alpha"
assert _ALPHA_CORE.is_dir(), f"alpha-core not found at {_ALPHA_CORE}"
if "selene_space_alpha" not in sys.modules:
    _parent = _types.ModuleType("selene_space_alpha")
    _parent.__path__ = [str(_ALPHA_CORE)]
    _parent.__package__ = "selene_space_alpha"
    sys.modules["selene_space_alpha"] = _parent

from selene_space_alpha.serenity import (  # noqa: E402
    SerenityConfig,
    SerenityFetcher,
    SerenityScorer,
)
from selene_space_alpha.serenity.pool import (  # noqa: E402
    THEMES,
    SerenityPoolScan,
    SerenityPoolScanner,
)


async def scan_theme(
    theme: str,
    top_n: int,
    limit: int | None,
    github_enabled: bool,
    proxy_url: str | None = None,
) -> None:
    config = SerenityConfig()
    if limit is not None:
        config = dataclasses.replace(config, pool_max_universe=limit)
    config = dataclasses.replace(config, pool_github_enabled=github_enabled)

    from selene_space_market.derivatives_db import DerivativesDB
    from selene_space_market.kline_db import KlineDB
    from selene_space_alpha.serenity.score_db import SerenityScoreDB
    from selene_space_alpha.serenity.pool_db import SerenityPoolDB

    # 单一解析来源(同 serenity_scan):CWD 相对的 services/local-api/data 那份
    # 是后端旧路径造出的空壳库,不是真数据。
    from selene_space_core.derivatives_paths import default_derivatives_db_path
    from selene_space_core.kline_paths import default_klines_db_path

    deriv_db = DerivativesDB(db_path=default_derivatives_db_path())
    kline_db = KlineDB(db_path=default_klines_db_path())
    score_db = SerenityScoreDB()
    pool_db = SerenityPoolDB()

    try:
        deriv_db.open()
        kline_db.open()
        score_db.open()
        pool_db.open()

        fetcher = SerenityFetcher(
            config=config,
            proxy_url=proxy_url,
            derivatives_db=deriv_db,
            kline_db=kline_db,
        )
        scorer = SerenityScorer(config=config)
        scanner = SerenityPoolScanner(
            fetcher=fetcher,
            scorer=scorer,
            config=config,
            score_db=score_db,
            pool_db=pool_db,
        )

        scan = await scanner.scan(theme, top_n=top_n)
        _print_scan(scan)
    finally:
        await fetcher.close()
        deriv_db.close()
        kline_db.close()
        score_db.close()
        pool_db.close()


def _print_scan(scan: SerenityPoolScan) -> None:
    print(f"\n== Theme: {scan.theme_name} ({scan.theme}) ==")
    print(
        f"Universe: {scan.universe_size} scored | "
        f"weak evidence: {scan.weak_evidence_count} | "
        f"top picks: {', '.join(scan.top_picks) or '-'}"
    )
    if scan.layer_summary:
        layers = ", ".join(f"{ls.layer} ({ls.count})" for ls in scan.layer_summary)
        print(f"Layers: {layers}")

    if not scan.ranked:
        print("No candidates scored.")
        return

    print(
        f"\n{'Rank':>4}  {'Symbol':>8}  {'Score':>6}  {'Tier':>14}  "
        f"{'Scarce':>6}  {'Evid':>5}  {'Label':>2}  Category"
    )
    print(
        f"{'----':>4}  {'------':>8}  {'-----':>6}  {'----':>14}  "
        f"{'------':>6}  {'----':>5}  {'-----':>2}  --------"
    )
    for i, c in enumerate(scan.ranked, 1):
        print(
            f"{i:>4}  {c.symbol:>8}  {c.normalized_score:>6.3f}  {c.tier:>14}  "
            f"{c.scarce_layer_score:>6.3f}  {c.evidence_strength:>5.2f}  "
            f"{c.evidence_label:>2}  {c.category}"
        )

    print("\nRationale:")
    for c in scan.ranked:
        flag = "★" if c.symbol in scan.top_picks else " "
        disproof = " | 证伪: " + "；".join(c.disproof) if c.disproof else ""
        print(f"  {flag} {c.symbol:>8}  {c.rationale}  风险:{c.top_penalty}{disproof}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Serenity theme pool scanner")
    parser.add_argument(
        "--theme",
        type=str,
        default="",
        help="Theme to scan (lending/dex/yield/restaking/derivatives/l2/ai)",
    )
    parser.add_argument(
        "--top-n", type=int, default=7, help="Number of top picks (default: 7)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the candidate universe at N (default: config pool_max_universe=30)",
    )
    parser.add_argument(
        "--no-github",
        action="store_true",
        help="Skip GitHub activity fetch (evidence degrades, never crashes)",
    )
    parser.add_argument(
        "--proxy",
        type=str,
        default="",
        help="Proxy URL for API access (e.g. http://<host>:<port>)",
    )
    parser.add_argument(
        "--themes", action="store_true", help="List available themes and exit"
    )
    args = parser.parse_args()

    if args.themes:
        for key, spec in THEMES.items():
            print(f"{key:<12} {spec.name}")
        return

    if not args.theme:
        parser.error("--theme is required (or use --themes to list them)")
    if args.theme not in THEMES:
        parser.error(f"unknown theme: {args.theme} (see --themes)")

    proxy_url = (
        args.proxy
        or os.environ.get("SELENE_PROXY_URL")
        or os.environ.get("https_proxy")
    )
    asyncio.run(
        scan_theme(args.theme, args.top_n, args.limit, not args.no_github, proxy_url)
    )


if __name__ == "__main__":
    main()

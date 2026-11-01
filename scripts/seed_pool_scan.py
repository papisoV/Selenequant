"""Offline seed for Serenity pool scans — deterministic restart-fallback check.

Inserts a synthetic SerenityPoolScan into serenity_pool_scans via
SerenityPoolDB (same DB as SerenityScoreDB). Lets you verify the
GET /api/v1/serenity/pool/{theme} DB-fallback read path (C3) across a server
restart without depending on DeFi Llama network availability.

Re-running with the same theme overwrites (fixed scanned_at + INSERT OR REPLACE).

Usage:
    python scripts/seed_pool_scan.py --theme lending
    python scripts/seed_pool_scan.py --theme lending --list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))

# Bypass parent __init__.py which triggers torch import. is_dir() guards
# against silently loading an editable-install copy from another replica.
import types as _types  # noqa: E402

_ALPHA_CORE = PROJECT_ROOT / "packages" / "alpha-core" / "selene_space_alpha"
assert _ALPHA_CORE.is_dir(), f"alpha-core not found at {_ALPHA_CORE}"
if "selene_space_alpha" not in sys.modules:
    _parent = _types.ModuleType("selene_space_alpha")
    _parent.__path__ = [str(_ALPHA_CORE)]
    _parent.__package__ = "selene_space_alpha"
    sys.modules["selene_space_alpha"] = _parent

from selene_space_alpha.serenity.pool import (  # noqa: E402
    PoolCandidate,
    PoolLayerSummary,
    SerenityPoolScan,
)
from selene_space_alpha.serenity.pool_db import SerenityPoolDB  # noqa: E402

# Fixed so repeated seeds overwrite instead of appending (PK = theme + scanned_at).
_FIXED_SCANNED_AT = 1735689600000

_THEME_META = {
    "lending": ("Lending", ("AAVE", "COMP", "MKR")),
    "dex": ("DEX", ("UNI", "SUSHI", "CRV")),
    "yield": ("Yield", ("AAVE", "CONVEX", "YFI")),
}


def _candidate(symbol: str, scarce: float, category: str) -> PoolCandidate:
    return PoolCandidate(
        symbol=symbol,
        name=symbol,
        category=category,
        chains=("eth",),
        normalized_score=0.7,
        tier="bullish",
        scarce_layer_score=scarce,
        evidence_strength=0.5,
        evidence_label="强",
        rationale="synthetic seed candidate",
        top_penalty="fork_risk:0.5",
    )


def build_scan(theme: str) -> SerenityPoolScan:
    theme_name, symbols = _THEME_META[theme]
    scarce = [0.9, 0.6, 0.3]
    ranked = tuple(
        _candidate(sym, scarce[i], theme_name) for i, sym in enumerate(symbols)
    )
    return SerenityPoolScan(
        theme=theme,
        theme_name=theme_name,
        scanned_at=_FIXED_SCANNED_AT,
        universe_size=len(ranked),
        layer_summary=(
            PoolLayerSummary(layer=theme_name, count=len(ranked), top_scarce_symbols=(symbols[0],)),
        ),
        ranked=ranked,
        top_picks=(symbols[0],),
        weak_evidence_count=0,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a synthetic Serenity pool scan")
    parser.add_argument("--theme", type=str, default="lending",
                        choices=sorted(_THEME_META), help="Theme to seed")
    parser.add_argument("--list", action="store_true",
                        help="List persisted scan themes and exit")
    args = parser.parse_args()

    pool_db = SerenityPoolDB()
    try:
        pool_db.open()
        if args.list:
            scans = pool_db.get_all_latest()
            print(f"Persisted scans ({len(scans)} themes):")
            for scan in scans:
                print(f"  {scan.theme:<10} {scan.theme_name:<10} "
                      f"scanned_at={scan.scanned_at} ranked={len(scan.ranked)}")
            return

        scan = build_scan(args.theme)
        pool_db.put_scan(scan)
        total = pool_db.count()
        print(f"Seeded {scan.theme} ({scan.theme_name}): {len(scan.ranked)} candidates, "
              f"top={scan.top_picks[0]}; total rows={total}")
        print(f"DB: {pool_db._db_path}")
    finally:
        pool_db.close()


if __name__ == "__main__":
    main()

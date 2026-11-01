"""E2E: astock saved-strategy deploy through the real app lifespan.

Requires data/strategies/alpha_v7_mining/best_meme_csi300.json and A-share
daily bars in services/local-api/data/klines.db (import via
scripts/import_astock_to_klines.py). Deselected by network marker groups is
not applicable — everything here is local DB + mocked market wiring.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI

from app.api.v1.alpha import _find_saved_strategy, _pandas_vocab_for_version
from selene_space_core.kline_paths import default_klines_db_path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_best_meme_csi300_present_with_v7_vocab() -> None:
    data = _find_saved_strategy("best_meme_csi300")
    assert data is not None, "best_meme_csi300.json missing from data/strategies/"
    assert data.get("version") == "v7_mining_csi300"
    tokens = (data.get("best_formula") or {}).get("tokens", [])
    assert tokens, "no formula tokens"

    feature_names, op_names = _pandas_vocab_for_version(data.get("version"))
    # Mining vocab: 11 features, torch op order INCLUDING GATE (32-size space)
    assert len(feature_names) == 11
    assert "GATE" in op_names
    assert max(tokens) < len(feature_names) + len(op_names)


def test_astock_klines_db_has_csi300() -> None:
    import sqlite3

    db = default_klines_db_path()
    assert db.exists()
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM klines WHERE exchange='astock' AND symbol='000001.SZ' AND interval='1d'"
        )
        (n,) = cur.fetchone()
    finally:
        conn.close()
    assert n >= 500, f"000001.SZ has only {n} bars"


@pytest.mark.asyncio
async def test_deploy_astock_via_lifespan_end_to_end() -> None:
    """Deploy best_meme_csi300 on 000001.SZ through the full app lifespan.

    Exercises: AstockSource registration → runner deploy → config_snapshot
    exchange routing → warmup from klines.db (astock/spot) → slot bars > 0.
    """
    import app.main as m

    runner_error: list[str | None] = []
    deployed_ids: list[str] = []

    async with m.lifespan(cast("FastAPI", object())):
        from app.core.dependencies import get_strategy_runner

        runner = get_strategy_runner()
        assert runner is not None, "strategy runner not constructed by lifespan"

        strategy_data = _find_saved_strategy("best_meme_csi300")
        assert strategy_data is not None

        formula = strategy_data.get("best_formula", {})
        feature_names, op_names = _pandas_vocab_for_version(
            strategy_data.get("version")
        )
        strategy_config = {
            "id": "alpha:best_meme_csi300",
            "source": "alpha:mined",
            "formula_tokens": formula.get("tokens", []),
            "buy_threshold": formula.get("buy_th", 0.85),
            "sell_threshold": formula.get("sell_th", 0.45),
            "vocab": {
                "feature_names": list(feature_names),
                "operator_names": list(op_names),
            },
            "symbols": ["000001.SZ"],
            "interval": "1d",
            "exchange": "astock",
            "market_type": "spot",
        }

        error = await runner.deploy("dep-e2e-astock-smoke", strategy_config, "auto")
        runner_error.append(error)
        assert error is None, f"runner.deploy failed: {error}"
        deployed_ids.append("dep-e2e-astock-smoke")

        slot = runner._slots.get("dep-e2e-astock-smoke")
        assert slot is not None, "slot missing after deploy"
        assert (
            slot.runtime.bar_count > 0
        ), f"slot bar_count={slot.runtime.bar_count} — astock warmup did not feed bars"

    # Cleanup is implicit: lifespan stop tears down runner/sources.

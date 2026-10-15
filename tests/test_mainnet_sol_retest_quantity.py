"""Mock unit test for mainnet_sol_retest.py quantity calc logic.

Verifies the LOT_SIZE/NOTIONAL loop:
- stepSize=0.001, minNotional=$5, live_price=$76
- target $5 / $76 = 0.0658 → floor to 0.065
- 0.065 * 76 = $4.94 < $5 → +0.001 → 0.066
- 0.066 * 76 = $5.02 ≥ $5 → return 0.066

Also tests:
- Hard cap abort: qty*price > $6 → RuntimeError
- Price drift (price=$80 → qty=0.0625 → floor 0.062 → 0.062*80=$4.96<5 → +0.001 → 0.063*80=$5.04)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def test_quantity_calc_basic():
    """$5 / $76 = 0.0658 → floor 0.065 → +0.001 → 0.066 (notional $5.02 ≥ $5)."""
    from mainnet_sol_retest import _compute_quantity, HARD_CAP_USDT

    qty = _compute_quantity(
        target_notional=5.0,
        live_price=76.0,
        step_size=0.001,
        min_notional=5.0,
    )
    assert qty == 0.066, f"expected 0.066, got {qty}"
    assert qty * 76.0 >= 5.0
    assert qty * 76.0 <= HARD_CAP_USDT


def test_quantity_calc_price_drift_higher_price():
    """Price drift $80: $5/$80=0.0625 → floor 0.062 → 0.062*80=$4.96<5 → +0.001 → 0.063*80=$5.04."""
    from mainnet_sol_retest import _compute_quantity

    qty = _compute_quantity(5.0, 80.0, 0.001, 5.0)
    assert qty == 0.063, f"expected 0.063 at $80, got {qty}"
    assert qty * 80.0 >= 5.0


def test_quantity_calc_lower_price():
    """Price drift $74: $5/$74=0.0675 → floor 0.067 → 0.067*74=$4.96<5 → +0.001 → 0.068*74=$5.03."""
    from mainnet_sol_retest import _compute_quantity

    qty = _compute_quantity(5.0, 74.0, 0.001, 5.0)
    assert qty == 0.068, f"expected 0.068 at $74, got {qty}"
    assert qty * 74.0 >= 5.0


def test_quantity_calc_hard_cap_abort():
    """If qty*price > $6, abort with RuntimeError."""
    import pytest
    from mainnet_sol_retest import _compute_quantity

    # min_notional=$7 would force qty up to $7 notional > $6 cap
    with pytest.raises(RuntimeError, match="hard cap"):
        _compute_quantity(5.0, 76.0, 0.001, 7.0)


def test_floor_to_step():
    from mainnet_sol_retest import _floor_to_step

    assert _floor_to_step(0.0658, 0.001) == 0.065
    assert _floor_to_step(0.066, 0.001) == 0.066
    assert _floor_to_step(0.0789, 0.001) == 0.078
    assert _floor_to_step(1.234, 0.0) == 1.234  # step=0 returns unchanged


def test_target_notional_constants():
    """Constants per plan v2."""
    from mainnet_sol_retest import (
        HARD_CAP_USDT,
        LIMIT_SELL_MARKUP_PCT,
        LIMIT_SELL_TIMEOUT_S,
        SAFETY_FLOOR_USDT,
        SYMBOL,
        TARGET_NOTIONAL_USDT,
    )

    assert SYMBOL == "SOLUSDT"
    assert TARGET_NOTIONAL_USDT == 5.0
    assert HARD_CAP_USDT == 6.0
    assert SAFETY_FLOOR_USDT == 9.68
    assert LIMIT_SELL_TIMEOUT_S == 3600  # 1h
    assert LIMIT_SELL_MARKUP_PCT == 0.005  # +0.5%


def test_exit_codes_documented():
    """Plan v2 exit code semantics."""
    from mainnet_sol_retest import __doc__

    doc = __doc__ or ""
    for code in ["0  success", "1  generic", "2  balance", "3  Authentication",
                 "4  hard cap", "5  idempotency", "6  lockfile"]:
        assert code in doc, f"exit code doc missing: {code}"


def test_import_live_engine_path_documented():
    """Plan v2: script documents that prod path is via HTTP /api/v1/live/place_order,
    not direct LiveExecutionEngine construction (which would require extensive service wiring)."""
    from mainnet_sol_retest import live_mode

    doc = live_mode.__doc__ or ""
    assert "LiveExecutionEngine" in doc, "script must explain why it uses HTTP not direct class"
    assert "/api/v1/live/place_order" in doc
    assert "trading.py" in doc or "live_engine" in doc

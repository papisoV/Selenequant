"""B1 第零步 — hold decision semantics locked as testable assertions.

The ``"hold"`` action (from ``_ACTION_BY_VERDICT[\"SUSPICIOUS\"]``) previously
had no downstream consumer: the deploy endpoint (``alpha.py``) deploys by
formula_id with zero verdict gating. Per the QD-absorption consensus plan v3
(B1 step zero, option (a)), hold is anchored as ``report.decision == \"hold\"``
— a machine-checkable assertion on the T4 report itself — while shortlist
deploy-gating remains a user-decision item, deliberately NOT implemented here.

Acceptance covered:

- ``test_suspicious_maps_to_hold`` — the cost-stress SUSPICIOUS path yields
  ``decision == \"hold\"`` (the only currently reachable SUSPICIOUS trigger).
- ``test_decision_covers_full_verdict_domain`` — every verdict the protocol
  can emit maps to exactly one non-empty action, and the mapping is total.
- ``test_hold_never_appears_for_valid_or_invalid`` — accept/reject keep their
  semantics; hold is reserved for SUSPICIOUS.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from selene_space_alpha.vocab import FORMULA_VOCAB
from selene_space_factor_research.mining.validation_protocol import (
    FoldConfig,
    _ACTION_BY_VERDICT,
    validate_formulas,
)
from selene_space_factor_research.mining.weight_drift_backtest import (
    WeightDriftConfig,
)


def _tok(name: str) -> int:
    return list(FORMULA_VOCAB.token_names).index(name)


MOM = [_tok("RET"), _tok("TS_RANK")]


def _syn(
    n_dates: int = 200,
    n_symbols: int = 4,
    seed: int = 1,
    ar: float = 0.95,
    sigma: float = 0.002,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    """AR(1) panel — same construction as test_validation_protocol."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start="2024-01-01", periods=n_dates)
    noise = rng.normal(0.0, sigma, (n_dates, n_symbols))
    rets = np.zeros_like(noise)
    rets[0] = noise[0]
    for t in range(1, n_dates):
        rets[t] = ar * rets[t - 1] + noise[t]
    close = 100.0 * np.cumprod(1.0 + rets, axis=0)
    open_ = close / (1.0 + rets)
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    volume = 1000.0 * (1.0 + 0.5 * rng.random((n_dates, n_symbols)))

    data_by_symbol: dict[str, pd.DataFrame] = {}
    ret_by_symbol: dict[str, pd.Series] = {}
    for s in range(n_symbols):
        sym = f"S{s}"
        data_by_symbol[sym] = pd.DataFrame(
            {
                "open": open_[:, s],
                "high": high[:, s],
                "low": low[:, s],
                "close": close[:, s],
                "volume": volume[:, s],
            },
            index=dates,
        )
        closes = pd.Series(close[:, s], index=dates)
        ret_by_symbol[sym] = closes.shift(-1) / closes - 1.0
    return data_by_symbol, ret_by_symbol


def test_suspicious_maps_to_hold() -> None:
    # The 30x cost-stress path is the established SUSPICIOUS trigger
    # (test_cost_stress_trigger_suspicious). Here we assert the downstream
    # decision field: SUSPICIOUS must surface as report.decision == "hold".
    data, ret = _syn()
    report = validate_formulas(
        [MOM],
        {"syn": (data, ret)},
        n_trials=20,
        fold_config=FoldConfig(min_train=80, fold_size=24, n_folds=5),
        backtest_config=WeightDriftConfig(epoch=12),
        cost_stress_multipliers=(30,),
    )
    v = report.formulas[0]
    assert v.verdict == "SUSPICIOUS"
    assert v.decision == "hold"


def test_decision_covers_full_verdict_domain() -> None:
    # The mapping must be total over the verdict vocabulary the protocol
    # documents: no verdict falls through to an empty action, and the fallback
    # is hold (fail-closed) — an unknown verdict never becomes "accept".
    assert set(_ACTION_BY_VERDICT) == {
        "VALID",
        "SUSPICIOUS",
        "INVALID",
        "INSUFFICIENT_DATA",
    }
    assert _ACTION_BY_VERDICT["VALID"] == "accept"
    assert _ACTION_BY_VERDICT["SUSPICIOUS"] == "hold"
    assert _ACTION_BY_VERDICT["INVALID"] == "reject"
    assert _ACTION_BY_VERDICT["INSUFFICIENT_DATA"] == "defer"
    assert _ACTION_BY_VERDICT.get("ANYTHING_UNKNOWN", "hold") == "hold"


@pytest.mark.parametrize(
    ("verdict", "forbidden"),
    [
        ("VALID", "hold"),
        ("INVALID", "hold"),
    ],
)
def test_hold_never_appears_for_valid_or_invalid(verdict: str, forbidden: str) -> None:
    # hold is reserved for SUSPICIOUS: VALID/INVALID keep accept/reject.
    assert _ACTION_BY_VERDICT[verdict] != forbidden

"""US-101 golden regression: build_ashare_panel extraction kept the
POST /api/v1/ashare/backtest response byte-equivalent (numeric tolerance).

The fixture at tests/fixtures/ashare_backtest_golden.json was captured from
the PRE-refactor endpoint implementation (git HEAD before the extraction;
swap procedure documented in scratch_capture_golden.py) and must never be
regenerated against post-refactor code — that would make the test
tautological.

Scenario (in _ashare_panel_golden_scenario.py): 3 symbols, n_long=2,
weight_method=equal, factor_set=technical_fundamental with NON-EMPTY
financials — the path where a dropped ``fund_factor_names`` round-trip
silently removes all fundamental factors from the composite (review
Major-1). Counterfactual verified: stripping the fund factors from
factor_names changes cumulative_return, so these assertions bite.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.error_handler import install_error_handlers

pytest.importorskip("selene_space_ashare")

from app.api.v1.ashare import build_ashare_panel, router as ashare_router  # noqa: E402
import selene_space_ashare.fundamentals_pit as fp_mod  # noqa: E402
from selene_space_ashare import data_fetcher as df_mod  # noqa: E402

# scenario module (same directory, import-free of app/selene deps so the
# capture script can reuse it against pre-refactor code)
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _ashare_panel_golden_scenario as sc  # noqa: E402

_DATA = _HERE / "fixtures" / "ashare_backtest_golden.json"


@pytest.fixture()
def patched_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(df_mod, "get_daily_klines", sc.fake_klines)
    monkeypatch.setattr(
        df_mod, "get_index_daily", lambda *a, **k: sc.fake_index_daily()
    )
    monkeypatch.setattr(fp_mod, "load_financials", lambda *a, **k: sc.financials())

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(ashare_router, prefix="/api/v1")
    return TestClient(app)


def _assert_close(got, want, path: str = "") -> None:
    """Recursive near-equality: numbers allclose, everything else exact."""
    if isinstance(want, dict):
        assert isinstance(got, dict), f"{path}: type mismatch {type(got)}"
        assert set(got) == set(want), f"{path}: key set differs: {set(got) ^ set(want)}"
        for key in want:
            _assert_close(got[key], want[key], f"{path}.{key}")
    elif isinstance(want, bool) or want is None or isinstance(want, str):
        assert got == want, f"{path}: {got!r} != {want!r}"
    elif isinstance(want, (int, float)):
        # NaN != NaN, so route through equality when both are NaN
        if want != want:
            assert got != got, f"{path}: expected NaN, got {got!r}"
        else:
            assert got == pytest.approx(want, rel=1e-9, abs=1e-12), (
                f"{path}: {got!r} != {want!r}"
            )
    else:
        assert got == want, f"{path}: {got!r} != {want!r}"


def test_backtest_golden_response_unchanged_after_helper_extraction(
    patched_client: TestClient,
) -> None:
    """Golden regression for US-101: the refactored endpoint must reproduce
    the pre-refactor response exactly (numbers within float tolerance)."""

    assert _DATA.exists(), (
        f"golden fixture missing: {_DATA} (re-capture ONLY from the "
        "pre-refactor implementation — see scratch_capture_golden.py)"
    )
    golden = json.loads(_DATA.read_text(encoding="utf-8"))

    resp = patched_client.post(f"/api/v1/ashare/backtest?{sc.REQUEST_QS}")
    assert resp.status_code == 200, resp.text
    _assert_close(resp.json(), golden)


def test_helper_returns_fund_factor_names_for_technical_fundamental(
    patched_client: TestClient,
) -> None:
    """build_ashare_panel contract: fund_factor_names is non-empty when
    financials carry data — the round-trip that keeps the composite from
    silently losing fundamental factors (Major-1)."""

    panel, failed_codes, fund_factor_names = build_ashare_panel(
        sc.CODES,
        start_date="20240101",
        factor_set="technical_fundamental",
        index_daily=sc.fake_index_daily(),
    )
    assert failed_codes == []
    assert fund_factor_names, "usable fund factors must not be empty here"
    assert set(fund_factor_names) == sc.FUND_FACTORS
    assert "RESID_VOL_60D" in panel.columns
    assert "CROWDING_SCORE" in panel.columns
    assert isinstance(panel, pd.DataFrame)


def test_helper_technical_set_returns_empty_fund_names(
    patched_client: TestClient,
) -> None:
    """factor_set=technical must not attach fundamentals: empty
    fund_factor_names, no fund columns on the panel."""

    panel, _failed, fund_factor_names = build_ashare_panel(
        sc.CODES,
        start_date="20240101",
        factor_set="technical",
        index_daily=sc.fake_index_daily(),
    )
    assert fund_factor_names == []
    assert not sc.FUND_FACTORS & set(panel.columns)
    assert "RESID_VOL_60D" in panel.columns


def test_helper_no_factor_data_raises_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty klines for every symbol -> SeleneSpaceError NOT_FOUND (the
    endpoint's 404 contract, now raised from inside the helper)."""

    from selene_space_types.errors import SeleneSpaceError

    monkeypatch.setattr(df_mod, "get_daily_klines", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(
        df_mod, "get_index_daily", lambda *a, **k: sc.fake_index_daily()
    )

    with pytest.raises(SeleneSpaceError) as exc_info:
        build_ashare_panel(
            sc.CODES,
            start_date="20240101",
            factor_set="technical",
            index_daily=sc.fake_index_daily(),
        )
    assert exc_info.value.code.value == "NOT_FOUND"

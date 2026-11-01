"""US-005: the report → API → deploy-gate chain, run end to end.

The other suites test each link in isolation — the p0 suite mocks the report
file by hand, this one lets ``run_supply_line`` write the real
``validation_report.json`` (its default artifact path redirected into a tmp
tree) and then drives the API layer against that artifact:

    mock checkpoint candidates (top_formulas.json) → G1-G6 → validate_formulas
    → write_report → GET /validation-report → deploy_formula verdict gating.

Path redirection note: ``supply_line``'s panel comes from ``build_datasets``,
which is monkeypatched (the panel is an external input, as in test_supply_line),
and the report path is passed explicitly — no monkeypatching of supply_line's
module constants is needed. The API side redirects ``_repo_root`` at the same
tmp tree, so the API reads the file supply_line actually wrote.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from app.api.v1 import alpha
from selene_space_alpha.factors import btc_bg_factors
from selene_space_alpha.vocab import FORMULA_VOCAB
from selene_space_factor_research.mining import supply_line

_FAST_FOLDS = supply_line.FoldConfig(min_train=360, fold_size=20, n_folds=2)

RET = list(FORMULA_VOCAB.token_names).index("RET")
TS_RANK = list(FORMULA_VOCAB.token_names).index("TS_RANK")
GOOD = [RET, TS_RANK]


@pytest.fixture(autouse=True)
def _stub_btc_background(monkeypatch):
    axis = pd.date_range("2020-01-01", periods=2000, freq="D")
    values = np.sin(np.arange(len(axis)) / 30.0)
    monkeypatch.setattr(
        btc_bg_factors, "build_background", lambda **_kw: pd.Series(values, index=axis)
    )


def _panel(*, n_dates: int = 400, seed: int = 7, ar: float = 0.5) -> dict:
    """AR(1) OHLCV panel in ``build_datasets`` shape, past G6's one-year floor."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start="2022-01-03", periods=n_dates)
    noise = rng.normal(0.0, 0.02, (n_dates, 2))
    rets = np.zeros_like(noise)
    rets[0] = noise[0]
    for t in range(1, n_dates):
        rets[t] = ar * rets[t - 1] + noise[t]
    close = 100.0 * np.cumprod(1.0 + rets, axis=0)
    open_ = close / (1.0 + rets)

    data_by_symbol: dict[str, pd.DataFrame] = {}
    ret_by_symbol: dict[str, pd.Series] = {}
    for s in range(2):
        sym = f"S{s}"
        data_by_symbol[sym] = pd.DataFrame(
            {
                "open": open_[:, s],
                "high": np.maximum(open_[:, s], close[:, s]) * 1.004,
                "low": np.minimum(open_[:, s], close[:, s]) * 0.996,
                "close": close[:, s],
                "volume": 1000.0 * (1.0 + 0.5 * rng.random(n_dates)),
            },
            index=dates,
        )
        closes = pd.Series(close[:, s], index=dates)
        ret_by_symbol[sym] = closes.shift(-1) / closes - 1.0
    return {"4h": (data_by_symbol, ret_by_symbol)}


def _write_artifact(
    tmp_path: Path, tokens: list[int], *, name="top_formulas_4h.json", evaluations=64
) -> Path:
    payload = {
        "symbols": ["S0", "S1"],
        "interval": "4h",
        "bars_per_symbol": 400,
        "evaluations_used": evaluations,
        "vocab_digest": FORMULA_VOCAB.vocab_digest,
        "top_formulas": [{"rank": 1, "readable": "TS_RANK(RET)", "tokens": tokens}],
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return Path(path)


async def _run_chain(
    tmp_path, monkeypatch, tokens: list[int], *, ar: float = 0.5, evaluations: int = 64
) -> dict:
    """Candidates -> G1-G6 -> T4 -> report on disk -> GET report -> verdict.

    Returns the GET response's report payload; asserts every link held.
    """
    artifact = _write_artifact(tmp_path, tokens, evaluations=evaluations)
    panel = _panel(ar=ar)
    monkeypatch.setattr(supply_line, "build_datasets", lambda *_a, **_k: panel)

    result = supply_line.run_supply_line(
        [artifact],
        report_path=tmp_path / "实验" / "alpha-mining" / "validation_report.json",
        fold_config=_FAST_FOLDS,
    )
    assert len(result.report.formulas) == 1

    # The API layer must read the same tree supply_line wrote into.
    monkeypatch.setattr(alpha, "_repo_root", lambda: tmp_path)
    resp = await alpha.get_validation_report()
    assert resp.error is None
    report = resp.data["report"]
    assert report["generated_at"]  # written by write_report, not hand-mocked
    assert report["config"]["n_trials_by_source"] == {"top_formulas_4h": evaluations}
    assert len(report["formulas"]) == 1
    return dict(report)


@pytest.mark.asyncio
async def test_accept_verdict_flows_to_deploy(tmp_path, monkeypatch) -> None:
    """A formula that passes T4 lands in the report with decision==accept and
    deploys without any gate interference. The near-persistent AR panel
    (ar=0.9) is a momentum haystack where TS_RANK(RET) ranks cleanly, and the
    small search count (16) keeps the DSR deflation benchmark low enough for
    the measured DSR to clear the 0.95 gate.

    min_train=360 keeps the OOS folds inside the region where _robust_norm's
    trailing 2880-bar median/MAD window (ROBUST_NORM_FIX, merge 44acca1) is
    past its min_periods warmup — the earlier folds (min_train=60) sat in the
    warmup where the rolling transform is not rank-preserving and the
    subinterval agreement gate split 1/0. Docstring's probed DSR numbers
    (0.98/0.94) predate that fix and refer to the old full-sample norm."""
    report = await _run_chain(tmp_path, monkeypatch, GOOD, ar=0.9, evaluations=16)
    formula = report["formulas"][0]
    assert formula["decision"] == "accept"
    assert formula["tokens"] == GOOD

    # Deploy gate reads the same file: accept → pass.
    results = tmp_path / "results"
    results.mkdir()
    (results / "best_meme_strategy_alpha_gate.json").write_text(
        json.dumps(GOOD), encoding="utf-8"
    )
    monkeypatch.setattr(alpha, "_results_dir", results)
    monkeypatch.setattr(
        alpha, "_deploy_archive_dir", tmp_path / "strategies" / "alpha_v_deploy"
    )
    monkeypatch.delenv("SELENE_ALPHA_DEPLOY_ALLOW_HOLD", raising=False)
    with patch(
        "selene_space_alpha.strategy_bridge.AlphaFormulaStrategy",
        lambda formula_tokens: type(
            "S", (), {"formula_readable": "f", "to_dict": lambda self: {}}
        )(),
    ):
        resp = await alpha.deploy_formula("alpha_gate")
    assert resp.data["status"] == "deployed"
    assert (results / "deployed" / "alpha_gate.json").exists()


@pytest.mark.asyncio
async def test_hold_verdict_flows_to_403(tmp_path, monkeypatch) -> None:
    """A formula whose T4 decision is not accept is refused by the same report
    file the API just served — the gate judges evidence, not a copy of it."""
    report = await _run_chain(tmp_path, monkeypatch, GOOD)
    formula = report["formulas"][0]
    assert formula["decision"] != "accept"  # this panel holds it back

    results = tmp_path / "results"
    results.mkdir()
    (results / "best_meme_strategy_alpha_gate.json").write_text(
        json.dumps(GOOD), encoding="utf-8"
    )
    monkeypatch.setattr(alpha, "_results_dir", results)
    monkeypatch.setattr(
        alpha, "_deploy_archive_dir", tmp_path / "strategies" / "alpha_v_deploy"
    )
    monkeypatch.delenv("SELENE_ALPHA_DEPLOY_ALLOW_HOLD", raising=False)
    with patch(
        "selene_space_alpha.strategy_bridge.AlphaFormulaStrategy",
        lambda formula_tokens: type(
            "S", (), {"formula_readable": "f", "to_dict": lambda self: {}}
        )(),
    ):
        with pytest.raises(HTTPException) as exc:
            await alpha.deploy_formula("alpha_gate")
    assert exc.value.status_code == 403
    assert "T4 validation decision" in exc.value.detail

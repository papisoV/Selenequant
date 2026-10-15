"""Config-driven preprocessing pipeline: provenance, reproducibility, A/B-ready configs.

Tests :class:`~selene_space_factor_research.preprocessing.pipeline.PreprocessingPipeline`
— declarative YAML/JSON/dict config driving :func:`apply` with per-step provenance
(method / params / in-out shapes / duration / prior hash) and a reproducible
``trace_hash``.
"""
from __future__ import annotations

import ast
import json
import pathlib

import numpy as np
import pandas as pd
import pytest

from selene_space_factor_research.preprocessing.config import PreprocessConfig
from selene_space_factor_research.preprocessing.errors import (
    PreprocessMethodError,
    PreprocessParamError,
)
from selene_space_factor_research.preprocessing.pipeline import (
    PreprocessingPipeline,
    StepProvenance,
)

_MODULE = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "factor-research" / "selene_space_factor_research" / "preprocessing" / "pipeline.py"
)


def _panel(n_dates: int = 6, n_symbols: int = 10, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_dates)
    n = n_dates * n_symbols
    return pd.DataFrame(
        {
            "trade_date": np.repeat(dates, n_symbols),
            "symbol": np.tile([f"S{i}" for i in range(n_symbols)], n_dates),
            "factor": rng.normal(0.0, 1.0, n),
            "size": rng.lognormal(0.0, 1.0, n),
        }
    )


WIN_STEP = {"method": "winsorize_mad", "params": {"trade_date_col": "trade_date", "factor_col": "factor", "n": 3.0}}
ZSCORE_STEP = {"method": "standardize_zscore", "params": {"trade_date": "trade_date", "factor_col": "factor"}}
LASSO_STEP = {
    "method": "neutralize_lasso",
    "params": {
        "trade_date_col": "trade_date",
        "factor_col": "factor",
        "neutralizer_cols": ["size"],
    },
}


def test_from_dict_list() -> None:
    pipe = PreprocessingPipeline.from_config([WIN_STEP, ZSCORE_STEP])
    assert [s.method for s in pipe.steps] == ["winsorize_mad", "standardize_zscore"]
    assert pipe.steps[0].params["n"] == 3.0


def test_from_single_step_dict() -> None:
    pipe = PreprocessingPipeline.from_config(WIN_STEP)
    assert len(pipe.steps) == 1
    assert pipe.steps[0].method == "winsorize_mad"


def test_from_pipeline_mapping() -> None:
    pipe = PreprocessingPipeline.from_config({"pipeline": [WIN_STEP, ZSCORE_STEP]})
    assert len(pipe.steps) == 2


def test_from_json_file(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "pipeline.json"
    p.write_text(json.dumps([WIN_STEP, ZSCORE_STEP]), encoding="utf-8")
    pipe = PreprocessingPipeline.from_config(str(p))
    assert len(pipe.steps) == 2


def test_from_yaml_file(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "pipeline.yaml"
    p.write_text(
        "pipeline:\n"
        "  - method: winsorize_mad\n"
        "    params:\n"
        "      trade_date_col: trade_date\n"
        "      factor_col: factor\n"
        "      n: 3.0\n"
        "  - method: standardize_zscore\n"
        "    params: {trade_date: trade_date, factor_col: factor}\n",
        encoding="utf-8",
    )
    pipe = PreprocessingPipeline.from_config(str(p))
    assert len(pipe.steps) == 2
    assert pipe.steps[1].method == "standardize_zscore"


def test_config_file_not_found(tmp_path: pathlib.Path) -> None:
    with pytest.raises(FileNotFoundError):
        PreprocessingPipeline.from_config(str(tmp_path / "missing.yaml"))


def test_bad_config_type() -> None:
    with pytest.raises(TypeError):
        PreprocessingPipeline.from_config(12345)


def test_applies_steps_in_order() -> None:
    df = _panel()
    res = PreprocessingPipeline.from_config([WIN_STEP, ZSCORE_STEP]).run(df)
    assert res.df.shape == df.shape
    assert res.df["factor"].notna().all()
    # zscore after winsorize: per-date factor mean ~0
    means = res.df.groupby("trade_date")["factor"].mean()
    assert np.allclose(means.to_numpy(), 0.0, atol=1e-9)


def test_provenance_records_each_step() -> None:
    df = _panel()
    res = PreprocessingPipeline.from_config([WIN_STEP, ZSCORE_STEP]).run(df)
    assert len(res.provenance) == 2
    assert all(isinstance(s, StepProvenance) for s in res.provenance)
    assert [s.method for s in res.provenance] == ["winsorize_mad", "standardize_zscore"]
    for s in res.provenance:
        assert s.in_shape == df.shape
        assert s.out_shape == df.shape
        assert s.duration_ms >= 0.0
        assert len(s.prior_hash) == 64  # sha256 hex


def test_trace_hash_reproducible() -> None:
    df = _panel(seed=1)
    cfg = [WIN_STEP, ZSCORE_STEP]
    h1 = PreprocessingPipeline.from_config(cfg).run(df).trace_hash
    h2 = PreprocessingPipeline.from_config(cfg).run(df).trace_hash
    assert h1 == h2
    assert len(h1) == 64


def test_trace_hash_differs_for_diff_config() -> None:
    df = _panel(seed=2)
    h_a = PreprocessingPipeline.from_config([WIN_STEP]).run(df).trace_hash
    h_b = PreprocessingPipeline.from_config([WIN_STEP, ZSCORE_STEP]).run(df).trace_hash
    assert h_a != h_b


def test_trace_hash_depends_on_input() -> None:
    h1 = PreprocessingPipeline.from_config([WIN_STEP]).run(_panel(seed=3)).trace_hash
    h2 = PreprocessingPipeline.from_config([WIN_STEP]).run(_panel(seed=4)).trace_hash
    assert h1 != h2


def test_bare_method_key_resolution() -> None:
    res = PreprocessingPipeline.from_config([{"method": "mad", "params": WIN_STEP["params"]}]).run(_panel())
    assert res.provenance[0].method == "winsorize_mad"


def test_ambiguous_bare_key_raises() -> None:
    with pytest.raises(PreprocessMethodError, match="ambiguous"):
        PreprocessingPipeline.from_config([{"method": "zscore", "params": {}}]).run(_panel())


def test_unknown_method_raises() -> None:
    with pytest.raises(PreprocessMethodError, match="unknown"):
        PreprocessingPipeline.from_config([{"method": "bogus_xyz", "params": {}}]).run(_panel())


def test_step_dict_missing_method_raises() -> None:
    with pytest.raises(PreprocessMethodError, match="missing 'method'"):
        PreprocessingPipeline.from_config([{"params": {"factor_col": "factor"}}]).run(_panel())


def test_invalid_params_raises() -> None:
    with pytest.raises(PreprocessParamError):
        # standardize_zscore requires factor_col
        PreprocessingPipeline.from_config([{"method": "standardize_zscore", "params": {"trade_date": "trade_date"}}]).run(_panel())


def test_unknown_param_raises() -> None:
    with pytest.raises(PreprocessParamError, match="unknown params"):
        PreprocessingPipeline.from_config(
            [{"method": "winsorize_mad", "params": {"trade_date_col": "trade_date", "factor_col": "factor", "bogus": 1}}]
        ).run(_panel())


def test_empty_steps() -> None:
    df = _panel()
    res = PreprocessingPipeline.from_config([]).run(df)
    assert res.provenance == []
    assert res.trace_hash == PreprocessingPipeline([]).run(df).trace_hash
    assert res.df.equals(df)


def test_three_step_chain_with_neutralize() -> None:
    df = _panel()
    res = PreprocessingPipeline.from_config([WIN_STEP, LASSO_STEP, ZSCORE_STEP]).run(df)
    assert len(res.provenance) == 3
    assert res.df["factor"].notna().all()
    assert set(res.df.columns) >= {"trade_date", "symbol", "factor", "size"}


def test_preprocess_config_objects_accepted() -> None:
    pipe = PreprocessingPipeline([PreprocessConfig("winsorize_mad", WIN_STEP["params"])])
    res = pipe.run(_panel())
    assert res.provenance[0].method == "winsorize_mad"


def test_port_module_imports_no_torch_or_polars() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in ("torch", "polars"), f"banned import {alias.name}"

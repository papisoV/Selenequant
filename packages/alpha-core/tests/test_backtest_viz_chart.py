"""Tests for backtest_viz chart / report generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from selene_space_alpha.backtest_viz.chart import (
    plot_formula_tree,
    plot_training_curve,
)
from selene_space_alpha.backtest_viz.engine import VizEngine
from selene_space_alpha.backtest_viz.report import generate_report
from selene_space_alpha.vocab import FORMULA_VOCAB


@pytest.fixture()
def history() -> dict:
    return {
        "step": [0, 1, 2, 3, 4],
        "best_reward": [-1.5, -1.2, -1.0, -0.8, -0.6],
        "mean_reward": [-1.6, -1.4, -1.2, -1.0, -0.9],
    }


@pytest.fixture()
def formula_tokens() -> list[int]:
    # A simple valid postfix: ADD(RET, LOG_VOL) -> 1 operator applied to 2 features.
    ret_idx = FORMULA_VOCAB.feature_names.index("RET")
    logvol_idx = FORMULA_VOCAB.feature_names.index("LOG_VOL")
    add_idx = FORMULA_VOCAB.feature_count + FORMULA_VOCAB.operator_names.index("ADD")
    return [ret_idx, logvol_idx, add_idx]


def test_plot_training_curve_creates_png(tmp_path: Path, history: dict) -> None:
    output = tmp_path / "curve.png"
    plot_training_curve(history, output)
    assert output.exists()
    assert output.stat().st_size > 0


def test_plot_formula_tree_creates_png(tmp_path: Path, formula_tokens: list[int]) -> None:
    output = tmp_path / "tree.png"
    plot_formula_tree(formula_tokens, FORMULA_VOCAB, output)
    assert output.exists()
    assert output.stat().st_size > 0


def test_generate_report_creates_html(
    tmp_path: Path, history: dict, formula_tokens: list[int]
) -> None:
    report_path = generate_report(history, formula_tokens, FORMULA_VOCAB, tmp_path)
    assert report_path.exists()
    assert report_path.suffix == ".html"
    content = report_path.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content
    assert "Training Curve" in content
    assert "Formula Tree" in content
    assert "data:image/png;base64" in content
    # History table rows: 5 steps -> 5 rows
    assert content.count("<tr><td>") == 5


def test_viz_engine_run_returns_html_path(
    tmp_path: Path, history: dict, formula_tokens: list[int]
) -> None:
    engine = VizEngine()
    out_dir = tmp_path / "viz_out"
    report_path = engine.run(history, formula_tokens, FORMULA_VOCAB, out_dir)
    assert report_path.exists()
    assert report_path.name == "report.html"
    assert (out_dir / "training_curve.png").exists()
    assert (out_dir / "formula_tree.png").exists()


def test_plot_training_curve_missing_key_raises(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        plot_training_curve({"step": [0]}, tmp_path / "x.png")


def test_plot_training_curve_mismatched_lengths_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        plot_training_curve(
            {"step": [0, 1], "best_reward": [0.0], "mean_reward": [0.0]},
            tmp_path / "x.png",
        )


def test_plot_formula_tree_invalid_postfix_raises(tmp_path: Path) -> None:
    """Two features with no operator leaves 2 operands on the stack — invalid postfix."""
    ret_idx = FORMULA_VOCAB.feature_names.index("RET")
    logvol_idx = FORMULA_VOCAB.feature_names.index("LOG_VOL")
    with pytest.raises(ValueError):
        plot_formula_tree([ret_idx, logvol_idx], FORMULA_VOCAB, tmp_path / "tree.png")

"""Backup-library location, resolution, and reported-baseline reading.

The baseline half of this file is deliberately adversarial: QD's reported
results live in hand-written prose, so the extractor's contract is *refusal*
as much as extraction. Each test pins one shape found in the real catalogue
(a ``baseline -> final`` chain, a walk-forward block, a benchmark parenthetical)
and asserts the figure is taken from the right place -- or that no figure is
reported when several compete.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from selene_space_strategy.qd.catalog import (
    QD_STRATEGY_DIR_ENV,
    extract_qd_metrics,
    list_qd_entries,
    list_qd_strategy_ids,
    qd_entry,
    qd_index_entry,
    qd_strategy_dir,
    resolve_qd_source,
)

_BACKUP = Path(r"<local-path-redacted>")
_SRC48_STEM = "src48_XLMUSDT_gk_vol_trail10_AlphaMaster"  # cleanroom: external QD export filename, not upstream code
_needs_backup = pytest.mark.skipif(
    not Path(_BACKUP / f"{_SRC48_STEM}.py").exists(),
    reason="QuantDinger strategy backup library not installed",
)


def _library_ids() -> list[str]:
    # cast: catalog.py is outside the mypy check set when only this test file
    # is staged, so the source module resolves to Any under --follow-imports=silent.
    return cast("list[str]", list_qd_strategy_ids())


# -- library location -------------------------------------------------------


def test_strategy_dir_defaults_to_the_quantdinger_backup(monkeypatch) -> None:
    monkeypatch.delenv(QD_STRATEGY_DIR_ENV, raising=False)
    assert qd_strategy_dir() == _BACKUP


def test_strategy_dir_honours_the_env_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(QD_STRATEGY_DIR_ENV, str(tmp_path))
    assert qd_strategy_dir() == tmp_path


# -- id resolution ----------------------------------------------------------


@_needs_backup
def test_ids_are_short_handles_in_qd_form() -> None:
    ids = _library_ids()
    if not ids:
        pytest.skip("no exported sources")
    assert all(part.startswith("qd:src") for part in ids)
    assert all(part.count("_") == 0 for part in ids), "id must stop at srcNN"
    assert len(set(ids)) == len(ids), "handles must be unique"


@_needs_backup
def test_resolve_accepts_short_id_full_stem_and_bare_stem() -> None:
    path = resolve_qd_source("qd:src48")
    assert path.stem == _SRC48_STEM
    assert resolve_qd_source(_SRC48_STEM) == path
    assert resolve_qd_source(f"qd:{_SRC48_STEM}") == path


@pytest.mark.parametrize("strategy_id", ["qd:src999999", "qd:src", "qd:"])
def test_resolve_rejects_unknown_and_empty_ids(strategy_id: str) -> None:
    with pytest.raises(ValueError):
        resolve_qd_source(strategy_id)


def test_resolve_refuses_an_ambiguous_prefix(monkeypatch, tmp_path) -> None:
    """Two exports sharing a handle must be refused, not silently picked."""
    for suffix in ("aaa", "bbb"):
        (tmp_path / f"src77_{suffix}.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv(QD_STRATEGY_DIR_ENV, str(tmp_path))
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_qd_source("qd:src77")


def test_ambiguous_sibling_does_not_shadow_the_exact_match(
    monkeypatch, tmp_path
) -> None:
    (tmp_path / "src77_aaa.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src77_bbb.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv(QD_STRATEGY_DIR_ENV, str(tmp_path))
    assert resolve_qd_source("qd:src77_aaa").name == "src77_aaa.py"


def test_missing_library_lists_nothing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(QD_STRATEGY_DIR_ENV, str(tmp_path))
    assert _library_ids() == []
    assert qd_index_entry(tmp_path / "src1_x.py") == {}


# -- prose -> metrics -------------------------------------------------------


def test_extract_takes_the_final_segment_of_an_arrow_chain() -> None:
    """``baseline -> final`` states the result last; the baseline is not it."""
    metrics = extract_qd_metrics(
        "Baseline(no stop) +74.4%/dd-30.5%/sharpe1.33 -> trail0.10 "
        "+94.6%/dd-25.2%/sharpe1.61, Q2 turned positive"
    ).metrics
    assert metrics is not None
    assert metrics["total_return"] == pytest.approx(0.946)
    assert metrics["max_drawdown"] == pytest.approx(-0.252)
    assert metrics["sharpe"] == pytest.approx(1.61)


def test_extract_prefers_the_walk_forward_block_over_its_own_arrow() -> None:
    """``train->test`` is a structural arrow; the OOS figure is the result."""
    metrics = extract_qd_metrics(
        "Walk-forward V2 two-gate pass: train70% pick->test30% 未见数据 "
        "收益+5.9%(12笔,回撤-19.3%)；全窗口复跑+89.1%(sharpe0.93,回撤-28.3%,38笔)。"
    ).metrics
    assert metrics is not None
    assert metrics["total_return"] == pytest.approx(0.059)
    assert metrics["max_drawdown"] == pytest.approx(-0.193)
    assert metrics["trades"] == pytest.approx(12)
    assert "sharpe" not in metrics, "the full-window figure must not leak in"


def test_extract_reads_annualised_return_as_its_own_metric() -> None:
    metrics = extract_qd_metrics(
        "Walk-forward pass: train70%->test30% 未见数据收益+13.63%"
        "(240天,年化~21.5%),47笔,回撤-15.08%。"
    ).metrics
    assert metrics is not None
    assert metrics["total_return"] == pytest.approx(0.1363)
    assert metrics["annual_return"] == pytest.approx(0.215)
    assert metrics["trades"] == pytest.approx(47)
    assert metrics["max_drawdown"] == pytest.approx(-0.1508)


def test_extract_ignores_labelled_neighbour_figures() -> None:
    """Drawdowns, benchmarks and zero-fee runs are not the headline return."""
    metrics = extract_qd_metrics(
        "Local engine fee +400pct (buy-hold -83pct) dd -31pct 5990 trades; "
        "zero-fee +528pct."
    ).metrics
    assert metrics is not None
    assert metrics["total_return"] == pytest.approx(4.0)
    assert metrics["max_drawdown"] == pytest.approx(-0.31)
    assert metrics["trades"] == pytest.approx(5990)


def test_extract_refuses_when_walk_forward_states_only_the_full_window() -> None:
    """A WF claim without an unseen-data figure is not a reportable result."""
    result = extract_qd_metrics(
        "[WF✓]ETH-4h-RSI +trailing(polish: full window+11.8%/dd-28.5% "
        "vs baseline+8.9%/dd-33.7%, every segment better)"
    )
    assert result.metrics is None
    assert result.candidates >= 1
    assert "out-of-sample" in result.reason


@pytest.mark.parametrize(
    "description",
    [
        "BTC +6.8% dd-19.3% 17 trades, ETH +31.2% dd-20.6% 11 trades",
        "2023 +6.5%/2024 +26.2%/2025 +22.5% (5 names equal weight)",
        "-63.75%/PF0.66, short leg -61.96%, long leg -4.78% (bench+490%)",
    ],
)
def test_extract_refuses_several_competing_figures(description: str) -> None:
    result = extract_qd_metrics(description)
    assert result.metrics is None, "an ambiguous description must not yield a number"
    assert result.candidates >= 2
    assert "ambiguous" in result.reason


def test_extract_reports_nothing_for_a_description_without_figures() -> None:
    result = extract_qd_metrics("elite-pool mined factor, 4-fold all-positive.")
    assert result.metrics is None
    assert result.candidates == 0
    assert result.reason == "no return figure reported"


def test_extract_handles_an_empty_description() -> None:
    result = extract_qd_metrics("")
    assert (result.metrics, result.candidates) == (None, 0)


def test_payload_is_json_serialisable() -> None:
    payload = extract_qd_metrics("fee +94.6%/dd-25.2%").as_payload()
    assert json.loads(json.dumps(payload))["metrics"]["total_return"] == pytest.approx(
        0.946
    )


# -- entries against the real library ---------------------------------------


@_needs_backup
def test_real_src48_baseline_matches_the_catalogue() -> None:
    entry: dict[str, Any] = qd_entry("qd:src48")
    metrics: dict[str, Any] = entry["qd_baseline"]["metrics"]
    assert metrics["total_return"] == pytest.approx(0.946)
    assert metrics["max_drawdown"] == pytest.approx(-0.252)
    assert metrics["sharpe"] == pytest.approx(1.61)
    assert entry["runnable"] is True
    assert entry["symbol"] == "XLM/USDT"
    assert entry["interval"] == "1h"
    assert entry["warmup"] == 320


@_needs_backup
def test_real_src13_baseline_is_the_out_of_sample_pick() -> None:
    entry: dict[str, Any] = qd_entry("qd:src13")
    metrics: dict[str, Any] = entry["qd_baseline"]["metrics"]
    assert metrics["total_return"] == pytest.approx(0.059)
    assert metrics["trades"] == pytest.approx(12)
    assert metrics["max_drawdown"] == pytest.approx(-0.193)


@_needs_backup
def test_real_library_entries_are_well_formed() -> None:
    entries: list[dict[str, Any]] = list_qd_entries()
    assert len(entries) == 30
    assert len({entry["id"] for entry in entries}) == 30
    for entry in entries:
        assert str(entry["id"]).startswith("qd:src")
        assert str(entry["file"]).endswith(".py")
        assert set(entry) >= {
            "id",
            "name",
            "symbol",
            "interval",
            "warmup",
            "runnable",
            "reason",
            "qd_baseline",
            "flags",
        }
        assert isinstance(entry["runnable"], bool)
        if not entry["runnable"]:
            assert entry["reason"], "an unavailable strategy must say why"


@_needs_backup
def test_real_library_flags_each_kind_of_caveat() -> None:
    by_id: dict[str, dict[str, Any]] = {
        str(entry["id"]): entry for entry in list_qd_entries()
    }
    assert "falsified" in by_id["qd:src17"]["flags"]
    assert "portfolio" in by_id["qd:src41"]["flags"]
    assert "multi_instrument" in by_id["qd:src41"]["flags"]
    assert "in_sample_only" in by_id["qd:src31"]["flags"]


@_needs_backup
def test_real_library_refuses_what_this_engine_cannot_drive() -> None:
    by_id: dict[str, dict[str, Any]] = {
        str(entry["id"]): entry for entry in list_qd_entries()
    }
    portfolio = by_id["qd:src41"]
    assert portfolio["runnable"] is False
    assert "handle_data" in str(portfolio["reason"])
    equity = by_id["qd:src15"]
    assert equity["runnable"] is False
    assert "TSLA" in str(equity["symbol"])

    runnable = [entry for entry in by_id.values() if entry["runnable"]]
    assert runnable, "src48 and its siblings must stay runnable"
    assert all("/" in str(entry["symbol"]) for entry in runnable)


@_needs_backup
def test_real_library_baselines_are_plausible() -> None:
    """Every reported figure is a finite fraction and no loss below -100%."""
    entries: list[dict[str, Any]] = list_qd_entries()
    reported = [
        entry["qd_baseline"]["metrics"]
        for entry in entries
        if entry["qd_baseline"]["metrics"]
    ]
    assert reported, "the catalogue reports figures for several strategies"
    for metrics in reported:
        assert isinstance(metrics, dict)
        for name, value in metrics.items():
            assert value == value, f"{name} is NaN"
            assert value not in (float("inf"), float("-inf"))
        assert metrics["total_return"] > -1.0
        assert metrics["max_drawdown"] <= 0.0

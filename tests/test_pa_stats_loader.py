"""Three-state tests for the PA stats loader (A2, design review).

F1 load-bearing wall: the calibration sentence must appear verbatim in
the global-rules line for every state — committed, missing, corrupt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pa_research.pa_stats_loader import (  # noqa: E402
    CALIBRATION_SENTENCE,
    load_pa_stats,
    stats_rule_or_fallback,
)


def _write_committed(results: Path) -> Path:
    payload = {
        "version": "2026-09-10v1",
        "data": {
            "symbol": "BTC/USDT",
            "market_type": "futures",
            "interval": "1h",
            "bars": 8999,
        },
        "experiments": {
            "swing_breakout": {
                "survival": {
                    "window_3": {
                        "successes": 1051,
                        "total": 1703,
                        "rate": 0.6171,
                        "ci95": [0.5938, 0.6399],
                        "degraded": False,
                    }
                }
            },
            "measured_move": {
                "hits": {
                    "successes": 1502,
                    "total": 1703,
                    "rate": 0.882,
                    "ci95": [0.8658, 0.8964],
                    "degraded": False,
                }
            },
            "spike_reversal": {
                "reversal": {
                    "successes": 379,
                    "total": 711,
                    "rate": 0.5331,
                    "ci95": [0.4963, 0.5694],
                    "degraded": False,
                }
            },
            "direction_edge": {"edge_after_cost": -0.0181, "n": 8998},
            "_degraded": False,
        },
    }
    results.mkdir(parents=True, exist_ok=True)
    (results / "pa_stats_BTCUSDT_futures_1h.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return results


# ------------------------------------------------------------- committed


def test_committed_line_contains_calibration_and_numbers(tmp_path: Path) -> None:
    results = _write_committed(tmp_path / "committed")
    line, meta = load_pa_stats(results)
    assert meta["source"] == "committed"
    assert meta["version"] == "2026-09-10v1"
    assert CALIBRATION_SENTENCE in line
    assert "61.7%" in line
    assert "88.2%" in line
    assert "53.3%" in line
    assert "无条件概率" in line
    assert "禁止将基率当作当前条件概率" in line


def test_committed_in_global_rules_shape(tmp_path: Path) -> None:
    results = _write_committed(tmp_path / "shape")
    line, meta = stats_rule_or_fallback(results)
    assert meta["source"] == "committed"
    assert isinstance(line, str)
    assert line.startswith(CALIBRATION_SENTENCE[:10])


# --------------------------------------------------------------- missing


def test_missing_json_returns_fallback_without_numbers(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    line, meta = stats_rule_or_fallback(empty)
    assert meta["source"] == "missing"
    assert CALIBRATION_SENTENCE in line
    assert "不可用" in line
    # No fabricated numbers in fallback state.
    assert "61.7" not in line
    assert "88.2" not in line


def test_missing_json_load_returns_none(tmp_path: Path) -> None:
    empty = tmp_path / "empty2"
    empty.mkdir()
    line, meta = load_pa_stats(empty)
    assert line is None
    assert meta["source"] == "missing"


# --------------------------------------------------------------- corrupt


def test_corrupt_json_falls_back_with_source_marker(tmp_path: Path) -> None:
    results = tmp_path / "corrupt"
    results.mkdir()
    (results / "pa_stats_BTCUSDT_futures_1h.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    line, meta = stats_rule_or_fallback(results)
    assert meta["source"] == "corrupt"
    assert CALIBRATION_SENTENCE in line
    assert "不可用" in line


def test_corrupt_missing_experiments_key_falls_back(tmp_path: Path) -> None:
    results = tmp_path / "nokey"
    results.mkdir()
    (results / "pa_stats_BTCUSDT_futures_1h.json").write_text(
        json.dumps({"version": "x", "data": {}}), encoding="utf-8"
    )
    line, meta = stats_rule_or_fallback(results)
    assert meta["source"] == "corrupt"
    assert CALIBRATION_SENTENCE in line


# -------------------------------------------------- degraded flag notice


def test_degraded_experiment_marks_reference_only(tmp_path: Path) -> None:
    results = tmp_path / "degraded"
    _write_committed(results)
    json_path = results / "pa_stats_BTCUSDT_futures_1h.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["experiments"]["_degraded"] = True
    json_path.write_text(json.dumps(data), encoding="utf-8")
    line, _ = load_pa_stats(results)
    assert "仅作参考" in line

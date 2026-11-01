"""Date-window selection for the klines loader and the T3/T4 handoff.

Covers the `--start`/`--end` capability added on top of the original
"newest N bars" loader:

1. ``load_klines(start=, end=)`` selects by time and bypasses the row cap.
2. A date-only ``end`` covers that whole UTC day (inclusive).
3. An out-of-range window raises instead of yielding an empty panel.
4. ``start > end`` is rejected up front.
5. The runner echoes the window into ``top_formulas.json``.
6. ``report.window_of`` round-trips it, and ``build_datasets`` reloads the
   *same* slice rather than the newest N bars.
7. The runner's G6 constant stays equal to the supply line's — a silent drift
   between the two would make the "this panel will be deferred" warning lie.
"""

from __future__ import annotations

import json
import sqlite3

import pandas as pd
import pytest

from selene_space_factor_research.mining import report, runner, supply_line

# 2024-01-01T00:00:00Z, one bar per hour.
_T0 = 1_704_067_200_000
_HOUR = 3_600_000


def _write_hourly_db(
    path, *, symbols=("BTC/USDT", "ETH/USDT"), n_bars=48, market="futures"
) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE klines ("
        " symbol TEXT, exchange TEXT, market_type TEXT, interval TEXT,"
        " timestamp INTEGER, open REAL, high REAL, low REAL, close REAL,"
        " volume REAL)"
    )
    rows = [
        (
            symbol,
            "binance",
            market,
            "1h",
            _T0 + i * _HOUR,
            100.0 + i,
            101.0 + i,
            99.0 + i,
            100.5 + i,
            1000.0,
        )
        for symbol in symbols
        for i in range(n_bars)
    ]
    conn.executemany("INSERT INTO klines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def _ts(day: str) -> pd.Timestamp:
    """Naive UTC — ``load_klines`` builds its index with ``to_datetime(unit="ms")``
    and does not localise, so the panel index is tz-naive UTC."""
    return pd.Timestamp(day)


# ---------------------------------------------------------------------------
# Loader: window semantics.
# ---------------------------------------------------------------------------
def test_window_selects_by_time_and_ignores_max_bars(tmp_path) -> None:
    """The row cap must not truncate an explicitly requested window."""
    db = tmp_path / "klines.db"
    _write_hourly_db(db, n_bars=48)  # 2024-01-01 .. 2024-01-03

    # 24 bars exist inside day 1; max_bars=5 would cut it to 5 on the legacy path.
    data = runner.load_klines(
        db, ["BTC/USDT"], "1h", max_bars=5, start="2024-01-01", end="2024-01-01"
    )
    df = data["BTC/USDT"]
    assert len(df) == 24, "window selection must bypass max_bars"
    assert df.index.min() == _ts("2024-01-01 00:00")
    assert df.index.max() == _ts("2024-01-01 23:00")


def test_date_only_end_covers_the_whole_day(tmp_path) -> None:
    """`--end 2024-01-01` includes bars after midnight, not just 00:00."""
    db = tmp_path / "klines.db"
    _write_hourly_db(db, n_bars=48)

    data = runner.load_klines(
        db, ["BTC/USDT"], "1h", start="2024-01-01", end="2024-01-01"
    )
    last = data["BTC/USDT"].index.max()
    assert last >= _ts("2024-01-01 23:00"), (
        "a date-only end truncated the sample early; got " + str(last)
    )


def test_explicit_datetime_end_is_respected_exactly(tmp_path) -> None:
    db = tmp_path / "klines.db"
    _write_hourly_db(db, n_bars=48)

    data = runner.load_klines(
        db, ["BTC/USDT"], "1h", start="2024-01-01T00:00:00Z", end="2024-01-01T05:00:00Z"
    )
    assert len(data["BTC/USDT"]) == 6  # 00:00 .. 05:00 inclusive


def test_start_only_takes_everything_after_it(tmp_path) -> None:
    db = tmp_path / "klines.db"
    _write_hourly_db(db, n_bars=48)

    data = runner.load_klines(db, ["BTC/USDT"], "1h", start="2024-01-02")
    assert len(data["BTC/USDT"]) == 24


def test_empty_window_raises_instead_of_returning_empty_panel(tmp_path) -> None:
    db = tmp_path / "klines.db"
    _write_hourly_db(db, n_bars=48)

    with pytest.raises(ValueError, match="no klines"):
        runner.load_klines(db, ["BTC/USDT"], "1h", start="2030-01-01", end="2030-02-01")


def test_start_after_end_is_rejected(tmp_path) -> None:
    db = tmp_path / "klines.db"
    _write_hourly_db(db, n_bars=48)

    with pytest.raises(ValueError, match="start .* is after end"):
        runner.load_klines(db, ["BTC/USDT"], "1h", start="2024-03-01", end="2024-01-01")


def test_no_window_keeps_the_legacy_newest_n_behaviour(tmp_path) -> None:
    db = tmp_path / "klines.db"
    _write_hourly_db(db, n_bars=48)

    data = runner.load_klines(db, ["BTC/USDT"], "1h", max_bars=10)
    assert len(data["BTC/USDT"]) == 10
    assert data["BTC/USDT"].index.max() == _ts("2024-01-02 23:00")


def test_unparseable_bound_raises(tmp_path) -> None:
    db = tmp_path / "klines.db"
    _write_hourly_db(db, n_bars=48)

    with pytest.raises(ValueError, match="unparseable date bound"):
        runner.load_klines(db, ["BTC/USDT"], "1h", start="not-a-date")


# ---------------------------------------------------------------------------
# Runner CLI → artifact provenance.
# ---------------------------------------------------------------------------
def test_runner_records_window_in_artifact(tmp_path, monkeypatch) -> None:
    db = tmp_path / "klines.db"
    _write_hourly_db(db, n_bars=48)
    monkeypatch.setattr(
        runner,
        "run_search",
        lambda data, ret, config, progress=None, **kwargs: type(
            "Result",
            (),
            {
                "top_formulas": [],
                "seed": config.seed,
                "evaluations_used": 0,
                "generations": 0,
                "stopped_reason": "monkeypatched",
            },
        )(),
    )

    code = runner.main(
        [
            "--symbols",
            "BTC/USDT,ETH/USDT",
            "--interval",
            "1h",
            "--db",
            str(db),
            "--budget",
            "1",
            "--start",
            "2024-01-01",
            "--end",
            "2024-01-01",
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0

    payload = json.loads((tmp_path / "top_formulas.json").read_text(encoding="utf-8"))
    window = payload["window"]
    assert window["selects_window"] is True
    assert window["requested_start"] == "2024-01-01"
    assert window["start"].startswith("2024-01-01")
    assert window["end"].startswith("2024-01-01")
    assert payload["bars_per_symbol"] == 24


def test_runner_without_window_marks_selects_window_false(
    tmp_path, monkeypatch
) -> None:
    db = tmp_path / "klines.db"
    _write_hourly_db(db, n_bars=48)
    monkeypatch.setattr(
        runner,
        "run_search",
        lambda data, ret, config, progress=None, **kwargs: type(
            "Result",
            (),
            {
                "top_formulas": [],
                "seed": config.seed,
                "evaluations_used": 0,
                "generations": 0,
                "stopped_reason": "monkeypatched",
            },
        )(),
    )

    code = runner.main(
        [
            "--symbols",
            "BTC/USDT,ETH/USDT",
            "--interval",
            "1h",
            "--db",
            str(db),
            "--budget",
            "1",
            "--bars",
            "6",
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0
    window = json.loads((tmp_path / "top_formulas.json").read_text(encoding="utf-8"))[
        "window"
    ]
    assert window["selects_window"] is False
    # Provenance only: the realised span is still recorded for display.
    assert window["span_days"] > 0


# ---------------------------------------------------------------------------
# T4 handoff: validation must reload the *same* sample.
# ---------------------------------------------------------------------------
def test_window_of_round_trips_a_windowed_artifact() -> None:
    payload = {
        "window": {
            "selects_window": True,
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-01-31T23:00:00Z",
        }
    }
    assert report.window_of(payload) == (
        "2024-01-01T00:00:00Z",
        "2024-01-31T23:00:00Z",
    )


def test_window_of_ignores_bars_based_artifacts() -> None:
    """A bars-based run records its span for display, but T4 must not pin it."""
    payload = {
        "window": {
            "selects_window": False,
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-01-02T23:00:00Z",
        }
    }
    assert report.window_of(payload) == (None, None)


def test_window_of_tolerates_legacy_artifacts_without_window() -> None:
    assert report.window_of({"top_formulas": []}) == (None, None)


def test_build_datasets_honours_the_window(tmp_path) -> None:
    db = tmp_path / "klines.db"
    _write_hourly_db(db, n_bars=48)

    datasets = report.build_datasets(
        db,
        ["BTC/USDT"],
        "1h",
        5,  # deliberately smaller than the window: the window must win
        start="2024-01-01",
        end="2024-01-01",
    )
    data_by_symbol, _ = datasets["1h"]
    assert len(data_by_symbol["BTC/USDT"]) == 24


def test_supply_line_gate_datasets_honours_the_window(tmp_path) -> None:
    db = tmp_path / "klines.db"
    _write_hourly_db(db, n_bars=48)

    datasets, fire = supply_line.gate_datasets(
        db,
        ["BTC/USDT"],
        "1h",
        5,
        start="2024-01-02",
        end="2024-01-02",
    )
    assert fire is None
    assert datasets is not None
    data_by_symbol, _ = datasets["1h"]
    assert len(data_by_symbol["BTC/USDT"]) == 24


# ---------------------------------------------------------------------------
# The G6 warning constant must not drift from the gate it describes.
# ---------------------------------------------------------------------------
def test_runner_g6_constant_matches_supply_line() -> None:
    assert runner._G6_MIN_SPAN_DAYS == supply_line._MIN_SPAN_DAYS


def test_span_days_reports_the_shortest_symbol() -> None:
    idx_long = pd.date_range("2024-01-01", periods=100, freq="D")
    idx_short = pd.date_range("2024-01-01", periods=10, freq="D")
    data = {
        "LONG": pd.DataFrame({"close": range(100)}, index=idx_long),
        "SHORT": pd.DataFrame({"close": range(10)}, index=idx_short),
    }
    assert runner._span_days(data) == pytest.approx(9.0, abs=1e-6)


def test_span_days_utc_clock_is_stable() -> None:
    """Sanity: the helper does not depend on the machine's local timezone."""
    idx = pd.date_range("2024-01-01", "2024-01-31", freq="D")
    data = {"S": pd.DataFrame({"close": range(len(idx))}, index=idx)}
    expected = (idx.max() - idx.min()).total_seconds() / 86_400.0
    assert runner._span_days(data) == pytest.approx(expected)

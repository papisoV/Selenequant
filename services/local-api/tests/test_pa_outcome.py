"""T3a outcome review — judge_outcome state machine (pure function).

Architect D4 verdicts, one test per terminal state plus the honest-refusal
and still-open clauses. Bars are synthetic epoch-ms dicts matching
``load_bars`` shape ({"ts", "open", "high", "low", "close", "volume"}).
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from app.core.pa_outcome import judge_outcome

_H = 3_600_000  # 1h in ms
_T0 = 1_700_000_000_000.0


def _bar(i: int, high: float, low: float) -> dict[str, Any]:
    return {
        "ts": _T0 + i * _H,
        "open": (high + low) / 2,
        "high": high,
        "low": low,
        "close": (high + low) / 2,
        "volume": 100.0,
    }


def _record(
    *,
    direction: str = "long",
    entry: float = 100.0,
    sl: float = 95.0,
    tp: float = 110.0,
    decided_after_bars: int = 10,
    outcome: dict[str, Any] | None = None,
    flat_stage2: bool = False,
) -> dict[str, Any]:
    """Decision taken after bar index ``decided_after_bars`` (i.e. the LLM
    saw bars 0..N, decided at that last bar's close)."""
    decided_iso = datetime.datetime.fromtimestamp(
        (_T0 + decided_after_bars * _H) / 1000.0, tz=datetime.timezone.utc
    ).isoformat()
    order = {
        "direction": direction,
        "entry": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "type": "limit",
    }
    if flat_stage2:
        stage2: dict[str, Any] = {"decision": "limit", "order": order}
    else:
        stage2 = {
            "decision": "limit",
            "diagnosis_or_decision": {"decision": "limit", "order": order},
        }
    return {
        "meta": {"input_id": "in-1", "session_id": "s", "timestamp": decided_iso},
        "input_data": {"symbol": "BTC/USDT", "interval": "1h"},
        "stage1": {"entry_gate": "proceed"},
        "stage2": stage2,
        "outcome": outcome,
    }


def _bars(n: int, highs: list[float] | None = None, lows: list[float] | None = None):
    """120 bars; highs/lows default to an oscillation that never touches
    the entry at 100 (all in 111..118)."""
    if highs is None:
        highs = [118.0 - (i % 6) for i in range(n)]
    if lows is None:
        lows = [h - 6.0 for h in highs]
    return [_bar(i, highs[i], lows[i]) for i in range(n)]


# --- terminal states -------------------------------------------------------


def test_win_backfilled_with_positive_pnl_r() -> None:
    """Entry triggered then TP hit → win, pnl_r = +RR (2.0 here)."""
    # Bar 30 is the first after the decision and touches entry (low 99
    # ≤ 100); its high 111 ≥ 110 → TP in the same bar → win, exit bar 30.
    highs = [118.0] * 30 + [111.0] * 90
    lows = [109.0] * 30 + [99.0] * 90
    bars = _bars(120, highs, lows)
    payload = judge_outcome(_record(decided_after_bars=29), bars)
    assert payload is not None
    assert payload["status"] == "win"
    assert payload["pnl_r"] == pytest.approx(2.0)
    assert payload["entry_triggered"] is True
    assert payload["source"] == "heartbeat_review"
    assert payload["exit_ts"] == bars[30]["ts"]


def test_loss_backfilled_with_negative_pnl_r() -> None:
    """Entry triggered then SL hit → loss, pnl_r = -1.

    Bar 30 touches entry (low 99) and its low 94 ≤ 95 → SL. Long checks
    SL first (conservative same-bar ordering), so this is loss not win
    even though high 111 ≥ 110 on the same bar."""
    highs = [118.0] * 30 + [111.0] * 90
    lows = [109.0] * 30 + [99.0] * 90
    lows[30] = 94.0
    bars = _bars(120, highs, lows)
    payload = judge_outcome(_record(decided_after_bars=29), bars)
    assert payload is not None
    assert payload["status"] == "loss"
    assert payload["pnl_r"] == -1.0
    assert payload["exit_ts"] == bars[30]["ts"]


def test_expired_when_entry_never_triggered_after_window() -> None:
    """Entry never touched, window fully elapsed → expired, no pnl_r."""
    bars = _bars(120)  # lows all ≥ 109 — never touches entry 100
    payload = judge_outcome(_record(decided_after_bars=10), bars)
    assert payload is not None
    assert payload["status"] == "expired"
    assert payload["pnl_r"] is None
    assert payload["entry_triggered"] is False


def test_timeout_open_is_terminal_when_window_elapsed() -> None:
    """Entry triggered, neither SL nor TP, window over → timeout_open."""
    highs = [118.0] * 30 + [105.0] * 90
    lows = [109.0] * 30 + [99.0] * 90  # entry touch at bar 30
    bars = _bars(120, highs, lows)  # after: high 105 < tp 110, low 99 > sl 95
    payload = judge_outcome(_record(decided_after_bars=29), bars)
    assert payload is not None
    assert payload["status"] == "timeout_open"
    assert payload["pnl_r"] is None
    assert payload["entry_triggered"] is True


def test_still_open_returns_none_when_window_not_elapsed() -> None:
    """Position live but window not over → None (nothing written; next round
    re-examines with a longer bar history)."""
    bars = _bars(40)  # only ~10 bars after decision — window not elapsed
    payload = judge_outcome(_record(decided_after_bars=29), bars)
    assert payload is None


# --- honest-refusal clause (D4 gap rule) -----------------------------------


def test_decision_before_window_start_refuses() -> None:
    """Decision timestamp < first bar ts → honest refusal, None."""
    bars = _bars(120)
    rec = _record(decided_after_bars=10)
    # Move the decision 1000h before the window — bars after it are absent.
    early = datetime.datetime.fromtimestamp(
        (_T0 - 1000 * _H) / 1000.0, tz=datetime.timezone.utc
    ).isoformat()
    rec["meta"]["timestamp"] = early
    assert judge_outcome(rec, bars) is None


# --- no-trade / malformed guards ------------------------------------------


def test_no_trade_order_never_judged() -> None:
    rec = _record(decided_after_bars=10)
    rec["stage2"]["diagnosis_or_decision"]["decision"] = "no_trade"
    rec["stage2"]["decision"] = "no_trade"
    assert judge_outcome(rec, _bars(120)) is None


def test_flat_stage2_shape_also_read() -> None:
    """Legacy/flat stage2 (order directly under stage2) is understood."""
    highs = [118.0] * 30 + [111.0] * 90
    lows = [109.0] * 30 + [99.0] * 90
    highs[35] = 112.0
    bars = _bars(120, highs, lows)
    payload = judge_outcome(_record(decided_after_bars=29, flat_stage2=True), bars)
    assert payload is not None
    assert payload["status"] == "win"


def test_missing_order_returns_none() -> None:
    rec = _record(decided_after_bars=10)
    rec["stage2"]["diagnosis_or_decision"]["order"] = None
    assert judge_outcome(rec, _bars(120)) is None


def test_non_numeric_prices_return_none() -> None:
    rec = _record(decided_after_bars=10)
    rec["stage2"]["diagnosis_or_decision"]["order"]["entry"] = "high"
    assert judge_outcome(rec, _bars(120)) is None


def test_unknown_direction_returns_none() -> None:
    rec = _record(decided_after_bars=10)
    rec["stage2"]["diagnosis_or_decision"]["order"]["direction"] = "maybe"
    assert judge_outcome(rec, _bars(120)) is None


def test_empty_bars_return_none() -> None:
    assert judge_outcome(_record(decided_after_bars=10), []) is None


def test_unparseable_timestamp_returns_none() -> None:
    rec = _record(decided_after_bars=10)
    rec["meta"]["timestamp"] = "not-a-date"
    assert judge_outcome(rec, _bars(120)) is None


def test_short_case_short_order_reads_reversed_levels() -> None:
    """Short: SL above entry, TP below — win when low ≤ tp.

    Post-decision bars never exceed high 103 (short SL 105.5 untouched);
    bar 30 touches entry (high ≥ 100 via range 99..103); bar 34 dips to
    90 ≤ tp 90.5 → win."""
    highs = [118.0] * 30 + [103.0] * 90
    lows = [109.0] * 30 + [99.0] * 90
    lows[34] = 90.0
    bars = _bars(120, highs, lows)
    payload = judge_outcome(
        _record(
            direction="short", entry=100.0, sl=105.5, tp=90.5, decided_after_bars=29
        ),
        bars,
    )
    assert payload is not None
    assert payload["status"] == "win"
    assert payload["pnl_r"] == pytest.approx((100.0 - 90.5) / (105.5 - 100.0))
    assert payload["exit_ts"] == bars[34]["ts"]


# --- B1 forward-read integration (load_bars after_ms=...) --------------------
#
# B1 (trading_pipeline.load_bars, after_ms param) returns the N bars STARTING
# at the anchor. These two tests pin the integration from both sides: the
# forward read feeds judge_outcome a scorable window, and a window ENDING at
# the decision (the historical read) cannot — the reason the forward branch
# exists (v3 plan §3 B1; R3/MF3).


def test_forward_read_feeds_judge_outcome() -> None:
    """Decision at the anchor; forward closes run flat then tap TP -> win.

    Entry 100 / SL 95 / TP 110, long. Forward bars hold 100 for 20 bars then
    high 111 at forward idx 21 (also >= entry, so it triggers and resolves in
    the same bar). Via load_bars(after_ms=anchor) the window STARTS at the
    decision bar; judge_outcome settles it win with pnl_r = RR.
    """
    from selene_space_decision_workflow.trading_pipeline import load_bars
    import sqlite3
    from pathlib import Path

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "klines.db"
        con = sqlite3.connect(db_path)
        con.execute(
            "CREATE TABLE klines (exchange TEXT, market_type TEXT, symbol TEXT,"
            " interval TEXT, timestamp INTEGER, open REAL, high REAL, low REAL,"
            " close REAL, volume REAL)"
        )
        forward: list[tuple[float, float]] = [(100.0, 100.5)] * 21 + [
            (105.0, 111.0)
        ] * 30
        for i, (c, h) in enumerate(forward):
            ts = _T0 + i * _H
            con.execute(
                "INSERT INTO klines VALUES ('binance','futures','TEST/USDT','1h',?,?,?,?,?,10.0)",
                (ts, c, h, c - 0.5, c),
            )
        con.commit()
        con.close()

        bars = load_bars(db_path, "TEST/USDT", "1h", 60, min_bars=1, after_ms=int(_T0))
        assert len(bars) == 51 and bars[0]["ts"] == _T0

        payload = judge_outcome(
            _record(
                direction="long", entry=100.0, sl=95.0, tp=110.0, decided_after_bars=0
            ),
            bars,
        )
        assert payload is not None
        assert payload["status"] == "win"
        assert payload["pnl_r"] == pytest.approx(2.0)


def test_window_ending_at_decision_cannot_settle() -> None:
    """The historical read (window ENDING at the decision) holds zero
    strictly-later bars -> judge_outcome honest-refuses with None. This is
    the exact failure B1's forward branch exists to remove."""
    highs = [101.0] * 60
    lows = [99.0] * 60
    bars = _bars(60, highs, lows)  # bars 0..59; decision at bar 59's close
    payload = judge_outcome(
        _record(
            direction="long", entry=100.0, sl=95.0, tp=110.0, decided_after_bars=59
        ),
        bars,
    )
    assert payload is None

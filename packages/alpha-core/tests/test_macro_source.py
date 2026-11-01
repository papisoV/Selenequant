"""Tests for MacroSource window logic (RED/ORANGE/YELLOW/COOLDOWN).

Verifies the EventGuard alert windows against a seeded macro_events.db:
    RED      ±2h  of release
    ORANGE   ±24h of release
    YELLOW   ±48h of release
    COOLDOWN +48h to +72h after release (only if no tighter window matched)
Outside all windows -> no alert. Tightest window wins (break on first match).

Import note: `macro`/`event_guard` are imported directly (selene_space_alpha dir
on sys.path) to bypass selene_space_alpha/__init__.py, which pulls in vocab/vm
(torch chain). event_guard itself has no torch dependency.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_alpha_core = str(Path(__file__).parent.parent / "selene_space_alpha")
if _alpha_core not in sys.path:
    sys.path.insert(0, _alpha_core)

from event_guard.macro_source import MacroSource  # noqa: E402
from event_guard.models import AlertLevel, EventSource  # noqa: E402
from macro.db import MacroEventDB  # noqa: E402
from macro.fetcher import _date_to_utc_ms  # noqa: E402

_HOUR_MS = 3_600_000
_CPI_DATE = "2026-01-14"  # hardcoded top-up date for the 2026 window
_RELEASE_TIME = "08:30"


def _seed_cpi_event(db_path: Path, event_ts: int) -> None:
    """Create a macro_events.db with a single CPI event at event_ts."""
    db = MacroEventDB(db_path)
    db.open()
    try:
        db.put_events(
            [
                (
                    "CPI", _CPI_DATE, _RELEASE_TIME,
                    0.3, 0.3, 0.4, 0.0, "CPIAUCSL", event_ts,
                )
            ]
        )
    finally:
        db.close()


@pytest.fixture()
def src(tmp_path) -> MacroSource:
    event_ts = _date_to_utc_ms(_CPI_DATE, _RELEASE_TIME)
    _seed_cpi_event(tmp_path / "macro.db", event_ts)
    return MacroSource(tmp_path / "macro.db")


def _alert_at(src: MacroSource, offset_hours: float) -> list:
    event_ts = _date_to_utc_ms(_CPI_DATE, _RELEASE_TIME)
    return src.check("BTC", int(event_ts + offset_hours * _HOUR_MS))


def test_red_within_2h(src) -> None:
    """±2h window -> RED alert (tightest match)."""
    alerts = _alert_at(src, +1.0)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.source == EventSource.MACRO_CALENDAR
    assert a.level == AlertLevel.RED
    assert a.symbol == "*"
    assert a.event_type == "CPI"
    assert "CPI" in a.reason


def test_orange_within_24h(src) -> None:
    """+5h (past RED, within ±24h) -> ORANGE."""
    alerts = _alert_at(src, +5.0)
    assert len(alerts) == 1
    assert alerts[0].level == AlertLevel.ORANGE


def test_yellow_within_48h(src) -> None:
    """+30h (past ORANGE, within ±48h) -> YELLOW."""
    alerts = _alert_at(src, +30.0)
    assert len(alerts) == 1
    assert alerts[0].level == AlertLevel.YELLOW


def test_cooldown_48_to_72h(src) -> None:
    """+60h (past YELLOW, within cooldown) -> COOLDOWN."""
    alerts = _alert_at(src, +60.0)
    assert len(alerts) == 1
    assert alerts[0].level == AlertLevel.COOLDOWN


def test_no_alert_outside_all_windows(src) -> None:
    """+100h -> no alert at all."""
    assert _alert_at(src, +100.0) == []


def test_before_release_red_window(src) -> None:
    """-1h (before release, within ±2h) -> RED."""
    alerts = _alert_at(src, -1.0)
    assert len(alerts) == 1
    assert alerts[0].level == AlertLevel.RED


def test_tightest_window_wins(src) -> None:
    """In RED window -> only RED, not also ORANGE/YELLOW."""
    alerts = _alert_at(src, +1.0)
    assert [a.level for a in alerts] == [AlertLevel.RED]


def test_cpi_red_multiplier(src) -> None:
    """CPI RED multiplier is 0.5 (per-type override, not the 0.0 default)."""
    alerts = _alert_at(src, +1.0)
    assert alerts[0].multiplier == 0.5


def test_missing_db_no_crash(tmp_path) -> None:
    """Missing DB file -> empty alerts, not a crash."""
    src = MacroSource(tmp_path / "does-not-exist.db")
    assert src.check("BTC", int(_date_to_utc_ms(_CPI_DATE, _RELEASE_TIME))) == []

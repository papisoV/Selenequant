"""CreditSource + EventGuard enable_credit wiring tests (US-006).

Layers:
- bit-identical gate: enable_credit=False, 20-case constant table spanning
  vix-alarm / tg-alarm / FOMC-alarm / no-alarm branches — expected values are
  derived from pre-credit branch logic (see _BITIDENTICAL_CASES derivation
  comments) so any future refactor that shifts semantics goes red.
- percentile thresholds on a synthetic 5y credit_daily (bands controllable).
- missing-data contract: empty DB / missing table / missing file → [] + 1.0
  + non-empty degraded_reason (plan decision D: absent source != alert).
- e2e write→read path-identity assertion (plan acceptance 6, Pre-mortem ④).
- warn-once: 100 checks emit exactly 1 warning.
- ingest smoke: skipped unless scripts/macro_credit_ingest.py exists.
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from selene_space_alpha.event_guard.credit_source import (
    CREDIT_ORANGE_MULT,
    CREDIT_YELLOW_MULT,
    CreditSource,
    resolve_credit_db_path,
)
from selene_space_alpha.event_guard.guard import EventGuard
from selene_space_alpha.event_guard.models import EventSource

REPO_ROOT = Path(__file__).resolve().parents[3]
INGEST_SCRIPT = REPO_ROOT / "scripts" / "macro_credit_ingest.py"

DAY_MS = 86_400_000
# 2026-01-01 00:00 UTC — well after every synthetic DB row so rows are fresh.
T0 = 1_767_225_600_000

# TG shock rows for tg-alarm branch (tg_events table, processed=0).
TG_TS = T0 - 3600_000  # 1h before evaluation time


# ---------------------------------------------------------------------------
# Synthetic DB builders
# ---------------------------------------------------------------------------


def _make_credit_db(path: Path, rows: list[tuple[int, float]]) -> None:
    """Create macro_events.db with credit_daily/yield_daily (ingest schema)."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS credit_daily (ts INTEGER, value REAL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS yield_daily (ts INTEGER, dgs2 REAL, dgs10 REAL, t10y2y REAL)"
    )
    conn.executemany("INSERT INTO credit_daily VALUES (?,?)", rows)
    conn.commit()
    conn.close()


def _synthetic_credit_rows(
    n: int = 1827, base_ts: int | None = None
) -> list[tuple[int, float]]:
    """n daily rows ending at T0 (last row ts = T0). 1826d span < 5y window.

    Value at row i (0-based): 500 + i (values 500..2326). A probe value V
    substituted for the last row lands in a window containing all 1827 rows
    (the probe itself included), so pct(V) = (V - 500 + 2) / 1827.
    Probes: YELLOW V=2144 → pct≈0.9009; ORANGE V=2272 → pct≈0.9710;
    p50 V=1413 → pct≈0.5008; MAX V=2326 → pct=1.0 (still ORANGE, no RED).
    """
    end = T0 if base_ts is None else base_ts
    return [(end - (n - 1 - i) * DAY_MS, 500.0 + i) for i in range(n)]


def _guard(
    tmp_path: Path,
    credit_db: Path | None,
    *,
    enable_credit: bool,
    enable_vix: bool = False,
    enable_tg: bool = False,
) -> EventGuard:
    return EventGuard(
        macro_db_path=str(tmp_path / "macro_events.db"),
        enable_vix=enable_vix,
        enable_tg=enable_tg,
        enable_credit=enable_credit,
        credit_db_path=str(credit_db) if credit_db else None,
    )


# ---------------------------------------------------------------------------
# Bit-identical gate: 20-case constant table, enable_credit=False
# ---------------------------------------------------------------------------

# Derivation (pre-credit behavior; no source emits event_type="VIX" in this
# repo — VixSource.check() returns [] and VIX overlap only fires when
# vix_alerts_active is non-empty, so those cases hold at 1.0 unless a macro
# event supplies the multiplier):
#
# no-alarm (1-5):   no rows anywhere → 1.0.
# FOMC (6-10):      macro_events rows at EV_MS; multiplier from
#                   EVENT_TYPE_MULTIPLIERS["FOMC"] by window: ±2h RED 0.7,
#                   ±24h ORANGE 0.85, ±48h YELLOW 0.95, +48..72h COOLDOWN 0.95.
# tg (11-15):       tg_events SECURITY ORANGE row (mult 0.7, ±2h window via
#                   half_life 2.0); combined = min(1.0, 0.7) = 0.7.
# vix (16-20):      vix_daily rows only (VixSource.check() == []); VIX overlap
#                   branch dead → 1.0 regardless of regime. Rows exist so a
#                   regression that starts emitting VIX alerts changes values.
BITIDENTICAL_EXPECTED = {
    # no-alarm
    ("noalarm", 1): 1.0,
    ("noalarm", 2): 1.0,
    ("noalarm", 3): 1.0,
    ("noalarm", 4): 1.0,
    ("noalarm", 5): 1.0,
    # FOMC: EV at T0 - 200h; cases probe t offsets from EV
    ("fomc", 6): 0.7,  # EV +1h  → RED ±2h
    ("fomc", 7): 0.7,  # EV -1h  → RED ±2h
    ("fomc", 8): 0.85,  # EV +12h → ORANGE ±24h
    ("fomc", 9): 0.95,  # EV +40h → YELLOW ±48h
    ("fomc", 10): 0.95,  # EV +60h → COOLDOWN +48..72h (FOMC 0.95)
    # tg-alarm
    ("tg", 11): 0.7,  # SECURITY ORANGE 0.7, +1h < 2h half-life
    ("tg", 12): 1.0,  # +3h > half-life → expired
    ("tg", 13): 0.7,
    ("tg", 14): 0.7,
    ("tg", 15): 0.7,
    # vix-only rows (crisis VIX): no VIX alert source → 1.0
    ("vix", 16): 1.0,
    ("vix", 17): 1.0,
    ("vix", 18): 1.0,
    ("vix", 19): 1.0,
    ("vix", 20): 1.0,
}

EV_MS = T0 - 200 * 3600_000  # FOMC event 200h before evaluation baseline


def _bitidentical_guard_inputs(
    tmp_path: Path,
) -> list[tuple[tuple[str, int], str, EventGuard, int]]:
    """(case_key, symbol, guard, now_ts) for all 20 cases."""
    macro_db = tmp_path / "macro_events.db"
    conn = sqlite3.connect(str(macro_db))
    conn.execute(
        "CREATE TABLE macro_events (event_type TEXT, date TEXT, timestamp_utc INTEGER)"
    )
    conn.execute(
        "INSERT INTO macro_events VALUES (?,?,?)", ("FOMC", "2026-01-01", EV_MS)
    )
    conn.execute(
        "CREATE TABLE tg_events (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER,"
        " message TEXT, event_type TEXT, alert_level TEXT, affected TEXT, half_life_h REAL,"
        " processed INTEGER DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO tg_events (timestamp, message, event_type, alert_level, affected, half_life_h)"
        " VALUES (?,?,?,?,?,?)",
        (TG_TS, "exchange hacked", "SECURITY", "orange", "*", 2.0),
    )
    conn.commit()
    conn.close()

    vix_db = tmp_path / "vix.db"
    conn = sqlite3.connect(str(vix_db))
    conn.execute("CREATE TABLE vix_daily (timestamp INTEGER, close REAL)")
    conn.executemany(
        "INSERT INTO vix_daily VALUES (?,?)",
        [(T0 - (6 - i) * DAY_MS, 32.0 + i) for i in range(7)],  # crisis regime
    )
    conn.commit()
    conn.close()

    guards: dict[str, tuple[EventGuard, Path | None]] = {}
    for branch in ("noalarm", "fomc", "tg", "vix"):
        db = (
            macro_db
            if branch in ("fomc", "tg")
            else (vix_db if branch == "vix" else tmp_path / "empty.db")
        )
        guards[branch] = (
            EventGuard(
                macro_db_path=str(db),
                enable_vix=(branch == "vix"),
                enable_tg=(branch == "tg"),
                enable_credit=False,
            ),
            None,
        )

    cases: list[tuple[tuple[str, int], str, EventGuard, int]] = []
    for i in range(1, 6):
        cases.append((("noalarm", i), "BTC/USDT", guards["noalarm"][0], T0))
    for i, offset_h in zip(range(6, 11), (1, -1, 12, 40, 60)):
        cases.append(
            (
                ("fomc", i),
                "BTC/USDT",
                guards["fomc"][0],
                EV_MS + int(offset_h * 3600_000),
            )
        )
    for i, offset_h in zip(range(11, 16), (1, 3, 1, 1, 1)):
        cases.append(
            (("tg", i), "BTC/USDT", guards["tg"][0], TG_TS + int(offset_h * 3600_000))
        )
    for i in range(16, 21):
        cases.append((("vix", i), "BTC/USDT", guards["vix"][0], T0))
    return cases


def test_bit_identical_20_cases_enable_credit_false(tmp_path):
    for key, symbol, guard, now_ts in _bitidentical_guard_inputs(tmp_path):
        got = guard.get_position_multiplier(symbol, now_ts)
        assert (
            got == BITIDENTICAL_EXPECTED[key]
        ), f"bit-identical drift at {key}: got {got}, expected {BITIDENTICAL_EXPECTED[key]}"


def test_credit_enum_registered():
    assert EventSource.CREDIT.value == "credit"


# ---------------------------------------------------------------------------
# Percentile thresholds
# ---------------------------------------------------------------------------


def _percentile_source(tmp_path: Path, probe_value: float) -> CreditSource:
    db = tmp_path / "pct.db"
    rows = _synthetic_credit_rows()[:-1] + [(T0, probe_value)]
    _make_credit_db(db, rows)
    return CreditSource(db)


def test_p90_band_yellow_alert(tmp_path):
    src = _percentile_source(tmp_path, 2144.0)  # pct ≈ 0.9009
    alerts = src.check("BTC/USDT", T0 + 3600_000)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.source == EventSource.CREDIT
    assert a.event_type == "CREDIT"
    assert a.symbol == "*"
    assert a.multiplier == CREDIT_YELLOW_MULT == 0.85
    assert a.is_active(T0 + 3600_000)


def test_p50_no_alert(tmp_path):
    src = _percentile_source(tmp_path, 1413.0)  # pct ≈ 0.5008
    assert src.check("BTC/USDT", T0 + 3600_000) == []


def test_p97_band_orange_alert(tmp_path):
    src = _percentile_source(tmp_path, 2272.0)  # pct ≈ 0.9710
    alerts = src.check("BTC/USDT", T0 + 3600_000)
    assert len(alerts) == 1
    assert alerts[0].level.value == "orange"
    assert alerts[0].multiplier == CREDIT_ORANGE_MULT == 0.7


def test_no_red_level_possible(tmp_path):
    src = _percentile_source(tmp_path, 2326.0)  # pct = 1.0 → ORANGE, never RED
    alerts = src.check("BTC/USDT", T0 + 3600_000)
    assert alerts and alerts[0].level.value == "orange"


def test_partial_window_flagged_in_status(tmp_path):
    db = tmp_path / "short.db"
    # 300 rows ≥ _MIN_WINDOW_ROWS(250) → scores within available window and
    # flags partial_window; probe value 500 is the series minimum → no alert.
    _make_credit_db(db, _synthetic_credit_rows(n=300)[:-1] + [(T0, 500.0)])
    src = CreditSource(db)
    status = src.get_status(T0 + 3600_000)
    assert status["available"] is True
    assert status["partial_window"] is True
    assert src.check("BTC/USDT", T0 + 3600_000) == []


# ---------------------------------------------------------------------------
# Missing-data contract (decision D: absent source != alert)
# ---------------------------------------------------------------------------


def test_missing_file_contract(tmp_path):
    src = CreditSource(tmp_path / "nope.db")
    assert src.check("BTC/USDT", T0) == []
    status = src.get_status(T0)
    assert status["available"] is False
    assert status["degraded_reason"]
    assert "not found" in status["degraded_reason"]


def test_empty_db_no_table_contract(tmp_path):
    db = tmp_path / "empty.db"
    db.touch()
    src = CreditSource(db)
    assert src.check("BTC/USDT", T0) == []
    status = src.get_status(T0)
    assert status["available"] is False
    assert status["degraded_reason"]


def test_missing_table_contract(tmp_path):
    db = tmp_path / "notable.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE other (x INTEGER)")
    conn.commit()
    conn.close()
    src = CreditSource(db)
    assert src.check("BTC/USDT", T0) == []
    status = src.get_status(T0)
    assert status["available"] is False
    assert "credit_daily" in status["degraded_reason"]


def test_stale_rows_contract(tmp_path):
    db = tmp_path / "stale.db"
    _make_credit_db(db, _synthetic_credit_rows()[:-8])  # last row 8d old
    src = CreditSource(db)
    assert src.check("BTC/USDT", T0) == []
    status = src.get_status(T0)
    assert status["available"] is False
    assert "stale" in status["degraded_reason"]


def test_insufficient_history_no_alert(tmp_path):
    db = tmp_path / "thin.db"
    _make_credit_db(db, _synthetic_credit_rows(n=100))
    src = CreditSource(db)
    assert src.check("BTC/USDT", T0) == []
    status = src.get_status(T0)
    assert "insufficient" in status["degraded_reason"]


def test_guard_disabled_and_missing_db_multiplier_one(tmp_path):
    guard = _guard(tmp_path, None, enable_credit=False)
    assert guard.get_position_multiplier("BTC/USDT", T0) == 1.0
    assert guard.get_credit_status()["degraded_reason"] == "credit source disabled"


def test_guard_enabled_missing_db_no_exception(tmp_path):
    guard = _guard(tmp_path, tmp_path / "ghost.db", enable_credit=True)
    assert guard.get_position_multiplier("BTC/USDT", T0) == 1.0
    status = guard.get_credit_status(T0)
    assert status["available"] is False
    assert status["degraded_reason"]


# ---------------------------------------------------------------------------
# Guard min() composition (acceptance 5)
# ---------------------------------------------------------------------------


def test_credit_yellow_tightens_but_never_loosens_fomc_red(tmp_path):
    macro_db = tmp_path / "macro.db"
    conn = sqlite3.connect(str(macro_db))
    conn.execute(
        "CREATE TABLE macro_events (event_type TEXT, date TEXT, timestamp_utc INTEGER)"
    )
    conn.execute("INSERT INTO macro_events VALUES (?,?,?)", ("FOMC", "2026-01-01", T0))
    conn.commit()
    conn.close()
    credit_db = tmp_path / "credit.db"
    _make_credit_db(credit_db, _synthetic_credit_rows()[:-1] + [(T0, 2144.0)])  # YELLOW

    guard = EventGuard(
        macro_db_path=str(macro_db),
        enable_vix=False,
        enable_tg=False,
        enable_credit=True,
        credit_db_path=str(credit_db),
    )
    # FOMC RED ±2h → 0.7; credit YELLOW 0.85 must NOT loosen it.
    assert guard.get_position_multiplier("BTC/USDT", T0 + 3600_000) == 0.7


def test_credit_yellow_applies_without_other_alerts(tmp_path):
    credit_db = tmp_path / "credit.db"
    _make_credit_db(credit_db, _synthetic_credit_rows()[:-1] + [(T0, 2144.0)])
    guard = EventGuard(
        macro_db_path=str(tmp_path / "absent.db"),
        enable_vix=False,
        enable_tg=False,
        enable_credit=True,
        credit_db_path=str(credit_db),
    )
    assert guard.get_position_multiplier("BTC/USDT", T0 + 3600_000) == 0.85


# ---------------------------------------------------------------------------
# Path resolution (Pre-mortem ④)
# ---------------------------------------------------------------------------


def test_resolve_explicit_arg_wins(tmp_path):
    assert resolve_credit_db_path(tmp_path / "x.db") == tmp_path / "x.db"


def test_resolve_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("SELENE_MACRO_DB", str(tmp_path / "env.db"))
    assert resolve_credit_db_path() == tmp_path / "env.db"


def test_resolve_default_is_canonical_absolute():
    p = resolve_credit_db_path()
    assert p.is_absolute()
    assert p == REPO_ROOT / "services" / "local-api" / "data" / "macro_events.db"
    assert "data/macro_events.db" in str(p).replace("\\", "/")


# ---------------------------------------------------------------------------
# e2e write→read path identity (acceptance 6)
# ---------------------------------------------------------------------------


def test_e2e_ingest_write_read_same_db(tmp_path):
    writer_db = (tmp_path / "data" / "macro_events.db").resolve()
    writer_db.parent.mkdir(parents=True)
    rows = _synthetic_credit_rows()[:-1] + [(T0, 2144.0)]
    _make_credit_db(writer_db, rows)  # stands in for macro_credit_ingest.py output

    reader_db = resolve_credit_db_path(str(writer_db))
    assert (
        reader_db.resolve() == writer_db
    ), "writer/reader paths diverge after resolve()"
    src = CreditSource(reader_db)
    alerts = src.check("BTC/USDT", T0 + 3600_000)
    assert alerts, "CreditSource must see rows the writer wrote — path identity broken"
    assert alerts[0].multiplier == 0.85


# ---------------------------------------------------------------------------
# warn-once
# ---------------------------------------------------------------------------


def test_warn_once_across_100_checks(tmp_path, caplog):
    src = CreditSource(tmp_path / "missing.db")
    with caplog.at_level(
        logging.WARNING, logger="selene_space_alpha.event_guard.credit_source"
    ):
        for _ in range(100):
            assert src.check("BTC/USDT", T0) == []
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1


# ---------------------------------------------------------------------------
# Ingest smoke (parallel US-005 deliverable; skip if not landed yet)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not INGEST_SCRIPT.exists(), reason="scripts/macro_credit_ingest.py not present yet"
)
def test_ingest_script_no_key_smoke():
    result = subprocess.run(
        [sys.executable, str(INGEST_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=120,
        env={
            "SELENE_MACRO_DB": "",
            "PATH": __import__("os").environ["PATH"],
            "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
        },
    )
    # No FRED_API_KEY in env → guidance + exit code 2 per plan (US-005).
    assert result.returncode == 2
    assert "FRED_API_KEY" in (result.stdout + result.stderr)

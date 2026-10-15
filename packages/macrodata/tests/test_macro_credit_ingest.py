"""Unit tests for scripts/macro_credit_ingest.py (US-005).

Covers: upsert idempotency, missing-key exit 2, table schema, DB path
resolution chain (SELENE_MACRO_DB override + canonical default).
No network — fetch path is exercised via monkeypatched provider.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "macro_credit_ingest.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("macro_credit_ingest", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def ingest():
    return _load_script()


class FakeProvider:
    """Minimal stand-in for FredProvider.fetch_series."""

    def __init__(self, series: dict[str, pd.DataFrame]):
        self._series = series
        self.stopped = False

    async def fetch_series(self, series_id, *, start=None, end=None, limit=None):
        from selene_space_macrodata.source import MacroSeries, SeriesKind

        return MacroSeries(
            provider="fred",
            series_id=series_id,
            kind=SeriesKind.FINANCIAL_STRESS,
            description="fake",
            observations=self._series[series_id],
            fetched_at=datetime.now(UTC),
        )

    async def start(self):
        pass

    async def stop(self):
        self.stopped = True


def _make_df(dates: list[str], values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime(dates), "value": values})


def test_resolve_db_path_env_override(ingest, monkeypatch, tmp_path):
    target = tmp_path / "override" / "macro_events.db"
    monkeypatch.setenv("SELENE_MACRO_DB", str(target))
    assert ingest.resolve_db_path(None) == target


def test_resolve_db_path_default_canonical(ingest, monkeypatch):
    monkeypatch.delenv("SELENE_MACRO_DB", raising=False)
    expected = PROJECT_ROOT / "services" / "local-api" / "data" / "macro_events.db"
    assert ingest.resolve_db_path(None).resolve() == expected.resolve()


def test_resolve_db_path_explicit_wins(ingest, monkeypatch, tmp_path):
    monkeypatch.setenv("SELENE_MACRO_DB", str(tmp_path / "env.db"))
    assert (
        ingest.resolve_db_path(str(tmp_path / "explicit.db"))
        == tmp_path / "explicit.db"
    )


def test_ensure_tables_schema(ingest, tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    try:
        ingest.ensure_tables(conn)
        ingest.ensure_tables(conn)  # idempotent DDL
        cols_credit = conn.execute("PRAGMA table_info(credit_daily)").fetchall()
        cols_yield = conn.execute("PRAGMA table_info(yield_daily)").fetchall()
        assert [(c[1], c[2]) for c in cols_credit] == [
            ("ts", "TEXT"),
            ("value", "REAL"),
        ]
        assert [(c[1], c[2]) for c in cols_yield] == [
            ("ts", "TEXT"),
            ("dgs2", "REAL"),
            ("dgs10", "REAL"),
            ("t10y2y", "REAL"),
        ]
        pk = [c[1] for c in cols_credit if c[5]]
        assert pk == ["ts"]
    finally:
        conn.close()


def test_upsert_idempotent(ingest, tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    try:
        ingest.ensure_tables(conn)
        rows = [("2026-01-02", 3.21), ("2026-01-03", 3.35), ("2026-01-06", 3.40)]
        assert ingest.upsert_credit(conn, rows) == 3
        assert ingest.upsert_credit(conn, rows) == 3  # rerun
        assert ingest.upsert_credit(conn, [("2026-01-02", 9.99)]) == 1  # update one
        n, v = conn.execute("SELECT COUNT(*), SUM(value) FROM credit_daily").fetchone()
        assert n == 3
        assert v == pytest.approx(3.35 + 3.40 + 9.99)

        yield_rows = {
            "2026-01-02": {"dgs2": 4.1, "dgs10": 4.5, "t10y2y": 0.4},
            "2026-01-03": {"dgs2": 4.2, "dgs10": 4.4, "t10y2y": 0.2},
        }
        assert ingest.upsert_yield(conn, yield_rows) == 2
        assert ingest.upsert_yield(conn, yield_rows) == 2  # rerun
        n = conn.execute("SELECT COUNT(*) FROM yield_daily").fetchone()[0]
        assert n == 2
        # Partial upsert replaces full row (dgs2 NULL where absent)
        assert ingest.upsert_yield(conn, {"2026-01-03": {"dgs2": 4.9}}) == 1
        row = conn.execute(
            "SELECT dgs2, dgs10, t10y2y FROM yield_daily WHERE ts='2026-01-03'"
        ).fetchone()
        assert row == (4.9, None, None)
    finally:
        conn.close()


def test_main_missing_key_exit_2(ingest, monkeypatch, capsys):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    rc = ingest.main([])
    assert rc == 2
    out = capsys.readouterr()
    assert "fred.stlouisfed.org" in out.err
    assert "FRED_API_KEY" in out.err


def test_run_ingest_writes_rows(ingest, monkeypatch, tmp_path):
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    db = tmp_path / "ing.db"

    dates = ["2026-01-02", "2026-01-03"]
    fake = FakeProvider(
        {
            "BAMLH0A0HYM2": _make_df(dates, [3.2, 3.3]),
            "DGS2": _make_df(dates, [4.1, 4.2]),
            "DGS10": _make_df(dates, [4.5, 4.4]),
            "T10Y2Y": _make_df(dates, [0.4, 0.2]),
        }
    )

    import selene_space_macrodata.providers.fred as fred_mod

    monkeypatch.setattr(fred_mod, "FredProvider", lambda cfg: fake)

    results = ingest.asyncio.run(ingest._run_ingest(db, 5))
    assert results["credit_daily"] == 2
    assert results["yield_daily"] == 2
    assert results["failures"] == []

    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM credit_daily").fetchone()[0] == 2
        row = conn.execute(
            "SELECT dgs2, dgs10, t10y2y FROM yield_daily WHERE ts='2026-01-03'"
        ).fetchone()
        assert row == (4.2, 4.4, 0.2)
    finally:
        conn.close()
    assert fake.stopped


def test_run_ingest_partial_failure_continues(ingest, monkeypatch, tmp_path):
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    db = tmp_path / "ing2.db"

    dates = ["2026-01-02"]

    class BrokenProvider(FakeProvider):
        async def fetch_series(self, series_id, *, start=None, end=None, limit=None):
            if series_id == "T10Y2Y":
                raise RuntimeError("boom")
            return await super().fetch_series(
                series_id, start=start, end=end, limit=limit
            )

    import selene_space_macrodata.providers.fred as fred_mod

    monkeypatch.setattr(
        fred_mod,
        "FredProvider",
        lambda cfg: BrokenProvider(
            {
                "BAMLH0A0HYM2": _make_df(dates, [3.2]),
                "DGS2": _make_df(dates, [4.1]),
                "DGS10": _make_df(dates, [4.5]),
                "T10Y2Y": pd.DataFrame(columns=["date", "value"]),
            }
        ),
    )

    results = ingest.asyncio.run(ingest._run_ingest(db, 5))
    assert results["failures"] == ["T10Y2Y"]
    assert results["credit_daily"] == 1
    row = (
        sqlite3.connect(str(db))
        .execute("SELECT dgs2, dgs10, t10y2y FROM yield_daily WHERE ts='2026-01-02'")
        .fetchone()
    )
    assert row == (4.1, 4.5, None)


def test_fred_catalog_has_bamlh(ingest):
    from selene_space_macrodata.providers.fred import FRED_CATALOG
    from selene_space_macrodata.source import SeriesKind

    kind, desc = FRED_CATALOG["BAMLH0A0HYM2"]
    assert kind == SeriesKind.FINANCIAL_STRESS
    assert "High Yield" in desc

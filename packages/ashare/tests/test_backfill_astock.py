"""Tests for the whole-market A-share daily backfill (B0).

Zero network: ``fetch_daily_rows`` and ``get_a_share_list`` are monkeypatched
with rows shaped like the real Sina response. Both the DB and the state file
live under ``tmp_path`` so a test never touches the user's real klines.db.

The properties worth guarding, in the order they bite in production:

* **Northbound is not dropped.** The old ``_to_sina_symbol`` map was
  ``{SZ, SH}`` and raised on every ``.BJ`` code — 349 symbols lost to an
  exception. If the universe-to-request path regresses there, this goes red.
* **Idempotence.** ``put_klines`` is an upsert, so a rerun must not double the
  row count. This is what makes ``--resume`` and crash-restart safe.
* **A single failure is not a silent pass.** One symbol erroring must leave the
  others written *and* exit non-zero — the failure mode of the binance script
  this mirrors.
* **Dropped rows are counted, not swallowed.** Sina returns ``0`` prices for
  halted/pre-open rows and ``KlineEvent`` rejects them; a quiet drop would read
  downstream as "no data that day".
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import backfill_klines_astock as bf  # noqa: E402
import verify_daily_all as vd  # noqa: E402
import xcheck_suspensions as xs  # noqa: E402
from selene_space_market.kline_db import KlineDB  # noqa: E402

_UNIVERSE = [("600519", "贵州茅台"), ("000001", "平安银行"), ("920002", "万达轴承")]


def _count(db_path, symbol: str) -> int:
    db = KlineDB(db_path=str(db_path))
    with db.connect():
        # cast: market-data ships no py.typed, so under the pre-commit hook's
        # --follow-imports=silent this cross-package call types as Any.
        return cast("int", db.count(bf.EXCHANGE, bf.MARKET_TYPE, symbol, bf.INTERVAL))


def _rows(days: list[str], *, price: float = 10.0) -> list[dict[str, str]]:
    return [
        {
            "day": d,
            "open": str(price),
            "high": str(price + 1),
            "low": str(price - 1),
            "close": str(price + 0.5),
            "volume": "1234",
        }
        for d in days
    ]


def _weekdays(start: str, count: int) -> list[str]:
    """``count`` consecutive weekday dates from ``start`` (no holiday logic)."""
    from datetime import date, timedelta

    y, m, d = (int(x) for x in start.split("-"))
    cur = date(y, m, d)
    out: list[str] = []
    while len(out) < count:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Point the script at a temp DB and a fake universe, no network."""
    import pandas as pd

    # Start the fixture on the window's first day, not before it: bars older
    # than the window are filtered out, so a fixture that starts earlier would
    # silently make "count == len(days)" wrong for the right reason.
    days = _weekdays(bf._window_start(3.0), 780)
    db_path = tmp_path / "klines.db"
    state = tmp_path / "state.json"
    out = tmp_path / "out"

    calls: list[str] = []

    def fake_universe(force: bool = False):
        return pd.DataFrame(_UNIVERSE, columns=["code", "name"])

    def fake_fetch(symbol, *, datalen=bf.MAX_DATALEN, session=None, timeout=15.0):
        calls.append(symbol)
        return _rows(days)

    monkeypatch.setattr(bf, "get_a_share_list", fake_universe)
    monkeypatch.setattr(bf, "fetch_daily_rows", fake_fetch)
    return {
        "db": db_path,
        "state": state,
        "out": out,
        "days": days,
        "calls": calls,
    }


def _run(wired, *extra: str) -> int:
    argv = [
        "backfill_klines_astock.py",
        "--db",
        str(wired["db"]),
        "--state",
        str(wired["state"]),
        "--out-dir",
        str(wired["out"]),
        "--sleep",
        "0",
        *extra,
    ]
    old = sys.argv
    sys.argv = argv
    try:
        return cast("int", bf.main())
    finally:
        sys.argv = old


class TestUniverse:
    def test_bj_codes_are_converted_not_dropped(self, wired):
        """92xxxx must become 920002.BJ — the old {SZ,SH} map raised here."""
        symbols = bf._resolve_universe(None)
        assert "920002.BJ" in symbols
        assert "600519.SH" in symbols
        assert "000001.SZ" in symbols
        assert not any(s.startswith("INVALID:") for s in symbols)

    def test_bad_code_is_surfaced_not_dropped(self, monkeypatch):
        import pandas as pd

        monkeypatch.setattr(
            bf,
            "get_a_share_list",
            lambda force=False: pd.DataFrame(
                [("600519", "ok"), ("12345", "short")], columns=["code", "name"]
            ),
        )
        symbols = bf._resolve_universe(None)
        # A malformed code must appear as a marked entry so the caller can
        # count it — silently vanishing is the failure this guards.
        assert any(s.startswith("INVALID:") for s in symbols)

    def test_explicit_symbols_skip_the_universe(self, monkeypatch):
        monkeypatch.setattr(
            bf,
            "get_a_share_list",
            lambda force=False: pytest.fail("universe must not be consulted"),
        )
        assert bf._resolve_universe("600519.sh, 920002.bj") == [
            "600519.SH",
            "920002.BJ",
        ]


class TestWindow:
    def test_window_start_is_calendar_years_back(self):
        from datetime import datetime, timedelta

        got = bf._window_start(3.0)
        expect = datetime.now(bf.CST).date() - timedelta(days=int(3 * 365.25))
        assert got == expect.isoformat()


class TestRun:
    def test_writes_every_symbol(self, wired):
        assert _run(wired, "--years", "3") == 0
        for sym in ("600519.SH", "000001.SZ", "920002.BJ"):
            assert _count(wired["db"], sym) == len(wired["days"])

    def test_rerun_is_idempotent(self, wired):
        assert _run(wired, "--years", "3", "--fresh") == 0
        first = _count(wired["db"], "600519.SH")
        # A second full pass over the same data must not add rows: the upsert
        # key is the whole partition tuple, so this is what makes rerun safe.
        assert _run(wired, "--years", "3", "--fresh") == 0
        assert _count(wired["db"], "600519.SH") == first

    def test_resume_skips_completed_symbols(self, wired):
        assert _run(wired, "--years", "3", "--fresh") == 0
        wired["calls"].clear()
        assert _run(wired, "--years", "3", "--resume") == 0
        assert wired["calls"] == [], "--resume refetched a completed symbol"

    def test_fresh_ignores_state(self, wired):
        assert _run(wired, "--years", "3", "--fresh") == 0
        wired["calls"].clear()
        assert _run(wired, "--years", "3", "--fresh") == 0
        assert len(wired["calls"]) == len(_UNIVERSE)

    def test_failure_exits_nonzero_but_writes_the_rest(self, wired, monkeypatch):
        good = bf.fetch_daily_rows

        def flaky(symbol, **kw):
            if symbol == "000001.SZ":
                raise ConnectionError("boom")
            return good(symbol, **kw)

        monkeypatch.setattr(bf, "fetch_daily_rows", flaky)
        assert _run(wired, "--years", "3", "--fresh") == 1
        # The healthy symbols still landed — one bad ticker is not a reason to
        # discard an hour of fetching.
        assert _count(wired["db"], "600519.SH") > 0
        assert _count(wired["db"], "000001.SZ") == 0

    def test_nonpositive_rows_are_dropped_and_counted(self, wired, monkeypatch):
        days = wired["days"]
        bad = _rows(days[:5])
        for row in bad:
            row["open"] = "0"  # halted/pre-open row, as Sina returns it

        def mixed(symbol, **kw):
            return bad + _rows(days[5:])

        monkeypatch.setattr(bf, "fetch_daily_rows", mixed)
        assert _run(wired, "--years", "3", "--fresh") == 0
        assert _count(wired["db"], "600519.SH") == len(days) - 5

    def test_window_filter_excludes_older_bars(self, wired, monkeypatch):
        # Two bars before the 3-year window plus the normal set: only the
        # in-window ones may be written. This is the test that would go red if
        # the window filter were dropped — it fails the same way the fixture
        # bug above did.
        old = _rows(["2020-01-02", "2020-01-03"])
        monkeypatch.setattr(
            bf, "fetch_daily_rows", lambda symbol, **kw: old + _rows(wired["days"])
        )
        assert _run(wired, "--years", "3", "--fresh") == 0
        assert _count(wired["db"], "600519.SH") == len(wired["days"])

    def test_dry_run_writes_nothing(self, wired):
        assert _run(wired, "--years", "3", "--dry-run") == 0
        assert not wired["db"].exists()
        # ...and must not clobber a real run's evidence artifacts.
        assert not (wired["out"] / "gap_report.json").exists()


class TestArtifacts:
    def test_gap_report_records_interior_hole(self, wired, monkeypatch):
        days = wired["days"]
        # Drop a month from the middle — a suspension-shaped hole.
        holed = [d for d in days if not ("2024-03-01" <= d <= "2024-04-15")]
        monkeypatch.setattr(bf, "fetch_daily_rows", lambda symbol, **kw: _rows(holed))
        assert _run(wired, "--years", "3", "--fresh") == 0
        report = json.loads((wired["out"] / "gap_report.json").read_text("utf-8"))
        assert "600519.SH" in report
        assert any(g[2] > bf.GAP_THRESHOLD_DAYS for g in report["600519.SH"])

    def test_allowlist_excludes_gapped_and_late_listed(self, wired, monkeypatch):
        days = wired["days"]

        def per_symbol(symbol, **kw):
            if symbol == "600519.SH":  # full history, no hole
                return _rows(days)
            if symbol == "000001.SZ":  # interior hole -> excluded
                return _rows(
                    [d for d in days if not ("2024-03-01" <= d <= "2024-04-15")]
                )
            return _rows(days[700:])  # late-listed -> excluded

        monkeypatch.setattr(bf, "fetch_daily_rows", per_symbol)
        assert _run(wired, "--years", "3", "--fresh") == 0
        doc = json.loads((wired["out"] / "allowlist.json").read_text("utf-8"))
        assert doc["symbols"] == ["600519.SH"]
        assert doc["window_start"] == bf._window_start(3.0)

    def test_resume_preserves_first_day_for_earlier_runs(self, wired):
        """A resumed run must not shrink the allowlist to the new symbols."""
        assert _run(wired, "--years", "3", "--fresh") == 0
        full = json.loads((wired["out"] / "allowlist.json").read_text("utf-8"))
        assert _run(wired, "--years", "3", "--resume") == 0
        resumed = json.loads((wired["out"] / "allowlist.json").read_text("utf-8"))
        assert resumed["symbols"] == full["symbols"] != []

    def test_resume_preserves_gap_report_from_earlier_runs(self, wired, monkeypatch):
        """The hole found before a crash must survive the resumed run.

        Regression: a real run died at symbol 5250 with "database is locked".
        ``--resume`` then skips every already-fetched symbol, so a gap report
        built only from the new symbols would silently drop 5250 symbols'
        worth of holes — the worst possible failure for an artifact whose
        whole job is to be the evidence.
        """
        days = wired["days"]
        holed = [d for d in days if not ("2024-03-01" <= d <= "2024-04-15")]

        def first_pass(symbol, **kw):
            # Only the first symbol has a hole in pass one...
            return _rows(holed if symbol == "600519.SH" else days)

        monkeypatch.setattr(bf, "fetch_daily_rows", first_pass)
        assert _run(wired, "--years", "3", "--fresh") == 0
        first = json.loads((wired["out"] / "gap_report.json").read_text("utf-8"))
        assert "600519.SH" in first

        # ...and pass two skips 600519.SH entirely because it is in `done`.
        monkeypatch.setattr(bf, "fetch_daily_rows", lambda symbol, **kw: _rows(days))
        assert _run(wired, "--years", "3", "--resume") == 0
        resumed = json.loads((wired["out"] / "gap_report.json").read_text("utf-8"))
        assert "600519.SH" in resumed, "resume dropped the earlier gap report"


class TestScanPartition:
    """The DB read-back, exercised directly.

    It is what both artifacts are built from on a real run, so it gets its own
    assertions rather than being trusted through the allowlist alone.
    """

    def _seed(self, db_path, per_symbol: dict[str, list[str]]) -> None:
        db = KlineDB(db_path=str(db_path))
        with db.connect():
            for sym, days in per_symbol.items():
                db.put_klines(list(bf.parse_rows(_rows(days), sym).events))

    def test_every_symbol_is_reported_including_the_last(self, tmp_path):
        """The symbol the scan ends on must not be swallowed.

        Regression: the per-symbol flush ran only when the next symbol's rows
        arrived, so the final symbol was dropped — losing its first-bar date
        (and any hole it owned) from both artifacts. Alphabetical order decides
        which symbol is "last", so the fixture's largest name is the one at
        risk.
        """
        db_path = tmp_path / "k.db"
        days = _weekdays("2023-10-17", 20)
        per_symbol = {"000001.SZ": days, "600519.SH": days, "920002.BJ": days}
        self._seed(db_path, per_symbol)

        _, first_day = bf._scan_partition(db_path, "2023-10-17")

        assert set(first_day) == set(per_symbol)
        assert first_day["920002.BJ"] == days[0]

    def test_hole_is_reported_from_the_db_not_the_input(self, tmp_path):
        db_path = tmp_path / "k.db"
        days = _weekdays("2023-10-17", 60)
        holed = [d for d in days if not ("2023-11-01" <= d <= "2023-12-01")]
        self._seed(db_path, {"600519.SH": holed, "000001.SZ": days})

        gap_report, _ = bf._scan_partition(db_path, "2023-10-17")

        assert list(gap_report) == ["600519.SH"]
        assert gap_report["600519.SH"][0][2] > bf.GAP_THRESHOLD_DAYS

    def test_bars_before_the_window_do_not_leak_into_first_day(self, tmp_path):
        db_path = tmp_path / "k.db"
        old = _rows(["2020-01-02", "2020-01-03"])
        days = _weekdays("2023-10-17", 10)
        db = KlineDB(db_path=str(db_path))
        with db.connect():
            db.put_klines(list(bf.parse_rows(old + _rows(days), "600519.SH").events))

        _, first_day = bf._scan_partition(db_path, "2023-10-17")

        # A 2020 bar must not make this look like deep history: the scan's
        # window filter is what keeps the allowlist honest.
        assert first_day["600519.SH"] == days[0]

    def test_missing_db_is_loud(self, tmp_path):
        with pytest.raises(SystemExit):
            bf._scan_partition(tmp_path / "nope.db", "2023-10-17")


class TestVerifyNoGaps:
    def test_flagged_symbols_are_returned(self):
        report = {
            "600519.SH": [],
            "000670.SZ": [("a", "b", 886)],
            "x": [("c", "d", 20)],
        }
        assert vd.verify_no_gaps(report, suspended=set()) == ["000670.SZ", "x"]

    def test_suspended_symbols_are_excused(self):
        report = {"000670.SZ": [("a", "b", 886)]}
        assert vd.verify_no_gaps(report, suspended={"000670.SZ"}) == []

    def test_empty_report_passes(self):
        assert vd.verify_no_gaps({}, suspended=set()) == []


class TestCorruptArtifacts:
    """A well-formed JSON document of the wrong *shape* must fail loudly.

    Both guards below were added in response to mypy: ``json.loads`` returns
    Any, so a JSON array parsed cleanly and only blew up later as an
    AttributeError from inside resume/verify logic — which reads as a bug in
    the script rather than as a corrupt file on disk.
    """

    def test_non_object_prior_run_is_rejected(self, tmp_path, monkeypatch):
        """--resume must not AttributeError on a corrupt prior file."""
        db_path = tmp_path / "k.db"
        # The DB is read before the prior file, so it has to exist and be a
        # real klines DB for the run to reach the --resume branch at all.
        db = KlineDB(db_path=str(db_path))
        with db.connect():
            db.put_klines(
                list(bf.parse_rows(_rows(["2024-01-02"]), "600519.SH").events)
            )
        prior = tmp_path / "suspensions.json"
        prior.write_text("[1, 2]", encoding="utf-8")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "xcheck_suspensions",
                "--db",
                str(db_path),
                "--data-dir",
                str(tmp_path),
                "--resume",
            ],
        )
        with pytest.raises(SystemExit, match="expected an object"):
            xs.main()

    def test_non_object_gap_report_is_rejected(self, tmp_path):
        p = tmp_path / "gap_report.json"
        p.write_text("[]", encoding="utf-8")
        with pytest.raises(SystemExit, match="expected an object"):
            vd._load_json(p)

    def test_wrong_typed_prior_inner_key_is_rejected(self, tmp_path, monkeypatch):
        """The outer object check is not enough — the inner keys are indexed.

        A half-written run (the exact situation --resume exists for) can be a
        well-formed object whose ``evidence`` is a list; the resume loop then
        blows up with a bare TypeError that reads as a bug in the script.
        """
        db_path = tmp_path / "k.db"
        db = KlineDB(db_path=str(db_path))
        with db.connect():
            db.put_klines(
                list(bf.parse_rows(_rows(["2024-01-02"]), "600519.SH").events)
            )
        prior = tmp_path / "suspensions.json"
        prior.write_text(json.dumps({"evidence": [1, 2]}), encoding="utf-8")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "xcheck_suspensions",
                "--db",
                str(db_path),
                "--data-dir",
                str(tmp_path),
                "--resume",
            ],
        )
        with pytest.raises(SystemExit, match="'evidence' must be a dict"):
            xs.main()

    def test_non_object_state_file_is_rejected(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(SystemExit, match="expected an"):
            bf._load_state(p)

    def test_a_valid_state_file_still_loads(self, tmp_path):
        # The guard must not reject the real shape, including the artifact-era
        # keys it exists to upgrade away from.
        p = tmp_path / "state.json"
        p.write_text(
            json.dumps(
                {"done": ["600519.SH"], "gap_report": {"x": []}, "first_day": {}}
            ),
            encoding="utf-8",
        )
        state = bf._load_state(p)
        assert state["done"] == ["600519.SH"]
        assert "gap_report" not in state, "stale key must be dropped on load"

    def test_missing_state_keys_are_still_filled_in(self, tmp_path):
        # The upgrade path: an old file without the newer keys must gain them,
        # not be rejected. Guards against over-tightening the list check.
        p = tmp_path / "state.json"
        p.write_text(json.dumps({"done": ["600519.SH"]}), encoding="utf-8")
        state = bf._load_state(p)
        assert state["done"] == ["600519.SH"]
        assert state["failed"] == []
        assert state["skipped_invalid"] == []

    def test_wrong_typed_state_key_is_rejected(self, tmp_path):
        """A correct-outer-shape file whose *values* are wrong must fail loudly.

        ``set(state["done"])`` and ``state["done"].append`` assume a list; an
        int here is a bare TypeError from inside the fetch loop, which reads as
        a bug in the script rather than as a hand-edited file.
        """
        p = tmp_path / "state.json"
        p.write_text(json.dumps({"done": 5}), encoding="utf-8")
        with pytest.raises(SystemExit, match="'done' must be a list"):
            bf._load_state(p)


class TestInnerKeyGuards:
    """The outer dict check is not enough: the *keys* are indexed too.

    ``allowlist.json`` is the artifact the plan freezes for human review, so a
    bad edit is a realistic event, and the wrong type used to surface as a bare
    ``TypeError`` from inside analysis code rather than as a bad file.
    """

    def test_non_list_allowlist_symbols_is_rejected(self):
        with pytest.raises(SystemExit, match="'symbols' must be a list, got int"):
            vd._require_list({"symbols": 50}, "symbols", Path("allowlist.json"))

    def test_non_string_window_start_is_rejected(self):
        with pytest.raises(SystemExit, match="'window_start' must be a string"):
            vd._require_str({"window_start": 20231017}, "window_start", Path("a.json"))

    def test_absent_keys_pass_the_guards(self):
        # Absent must not be an error: the caller's own "no window_start"
        # failure has to stay reachable, and `symbols` legitimately defaults.
        assert vd._require_list({}, "symbols", Path("a.json")) == []
        assert vd._require_str({}, "window_start", Path("a.json")) is None

    def test_valid_types_pass(self):
        assert vd._require_list({"symbols": ["600519.SH"]}, "symbols", Path("a")) == [
            "600519.SH"
        ]
        doc = {"window_start": "2023-10-17"}
        assert vd._require_str(doc, "window_start", Path("a")) == "2023-10-17"


class TestLoadSuspensions:
    def test_missing_file_is_an_empty_set(self, tmp_path):
        # Absent excuses must not crash — but they must also mean "no excuses",
        # so every hole then fails criterion (3).
        assert vd.load_suspensions(tmp_path / "nope.json") == set()

    def test_symbols_are_read(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text('{"reason": "halt", "symbols": ["000670.SZ"]}', encoding="utf-8")
        assert vd.load_suspensions(p) == {"000670.SZ"}

    def test_malformed_shape_is_loud(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text('{"symbols": "000670.SZ"}', encoding="utf-8")
        with pytest.raises(SystemExit):
            vd.load_suspensions(p)


class TestVerifyEndToEnd:
    """Drives the acceptance script against a real (temp) DB and artifacts."""

    def _artifacts(self, tmp_path, gap_symbol: str | None):
        (tmp_path / "gap_report.json").write_text(
            json.dumps(
                {gap_symbol: [["2026-04-30", "2026-07-07", 68]]} if gap_symbol else {}
            ),
            encoding="utf-8",
        )
        (tmp_path / "allowlist.json").write_text(
            json.dumps({"window_start": "2023-10-17", "symbols": ["600519.SH"]}),
            encoding="utf-8",
        )

    def _verify(self, tmp_path, db_path, *extra: str) -> int:
        argv = [
            "verify_daily_all.py",
            "--db",
            str(db_path),
            "--data-dir",
            str(tmp_path),
            *extra,
        ]
        old = sys.argv
        sys.argv = argv
        try:
            return cast("int", vd.main())
        finally:
            sys.argv = old

    def _seed_db(self, db_path, *, symbols=("600519.SH",), bars=760):
        db = KlineDB(db_path=str(db_path))
        with db.connect():
            db.put_klines(
                [
                    e
                    for sym in symbols
                    for e in bf.parse_rows(
                        _rows(_weekdays(bf._window_start(3.0), bars)), sym
                    ).events
                ]
            )

    def test_passes_on_a_consistent_db(self, tmp_path):
        db_path = tmp_path / "k.db"
        self._seed_db(db_path, symbols=("600519.SH",))
        self._artifacts(tmp_path, gap_symbol=None)
        assert self._verify(tmp_path, db_path, "--expect", "1") == 0

    def test_missing_artifacts_fail_loudly(self, tmp_path):
        """An empty artifact pair must not read as a clean pass.

        Criteria (2) and (3) are both quantified over the artifacts: no
        allowlist means no symbol can be too shallow, and no gap report means
        no hole can be unexplained. With a populated DB and no artifacts, every
        check passes vacuously — which is exactly the "confident 0 symbols"
        failure this guard exists for.
        """
        db_path = tmp_path / "k.db"
        self._seed_db(db_path, symbols=("600519.SH",))
        self._artifacts(tmp_path, gap_symbol=None)
        # Present but empty: the backfill wrote its files, the scan found
        # nothing. Criteria (2) and (3) are both quantified over these two
        # documents, so an empty pair satisfies them vacuously.
        (tmp_path / "gap_report.json").write_text("{}", encoding="utf-8")
        (tmp_path / "allowlist.json").write_text(
            json.dumps({"window_start": "2023-10-17", "symbols": []}),
            encoding="utf-8",
        )
        assert self._verify(tmp_path, db_path, "--expect", "1") == 1

    def test_fails_on_short_coverage(self, tmp_path):
        db_path = tmp_path / "k.db"
        self._seed_db(db_path, symbols=("600519.SH",))
        self._artifacts(tmp_path, gap_symbol=None)
        assert self._verify(tmp_path, db_path, "--expect", "5574") == 1

    def test_bad_allowlist_symbols_type_fails_through_main(self, tmp_path, capsys):
        """The guard must be on main()'s path, not just callable standalone.

        A unit test on ``_require_list`` alone proves the function works; it
        does not prove ``main`` uses it. This drives the real entry point, which
        is the only thing that catches a guard that was written but never wired.
        """
        db_path = tmp_path / "k.db"
        self._seed_db(db_path, symbols=("600519.SH",))
        self._artifacts(tmp_path, gap_symbol=None)
        (tmp_path / "allowlist.json").write_text(
            json.dumps({"window_start": "2023-10-17", "symbols": 50}),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit, match="'symbols' must be a list"):
            self._verify(tmp_path, db_path, "--expect", "1")

    def test_bad_window_start_type_fails_through_main(self, tmp_path):
        db_path = tmp_path / "k.db"
        self._seed_db(db_path, symbols=("600519.SH",))
        self._artifacts(tmp_path, gap_symbol=None)
        (tmp_path / "allowlist.json").write_text(
            json.dumps({"window_start": 20231017, "symbols": ["600519.SH"]}),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit, match="'window_start' must be a string"):
            self._verify(tmp_path, db_path, "--expect", "1")

    def test_null_gap_value_fails_through_main(self, tmp_path):
        """A ``null`` hole value must not read as "this symbol has no hole".

        Regression, found by probing rather than reasoning: both readers of
        ``gap_report`` test the value for truthiness, so ``null`` made a hidden
        hole disappear — criterion (3) printed PASS. Fail-open on the one
        artifact whose purpose is to expose holes, so it must exit non-zero.
        """
        db_path = tmp_path / "k.db"
        self._seed_db(db_path, symbols=("600519.SH",))
        self._artifacts(tmp_path, gap_symbol=None)
        (tmp_path / "gap_report.json").write_text(
            json.dumps({"000670.SZ": None}), encoding="utf-8"
        )
        with pytest.raises(SystemExit, match="'000670.SZ' must map to a list"):
            self._verify(tmp_path, db_path, "--expect", "1")

    def test_non_list_gap_value_fails_through_main(self, tmp_path):
        db_path = tmp_path / "k.db"
        self._seed_db(db_path, symbols=("600519.SH",))
        self._artifacts(tmp_path, gap_symbol=None)
        (tmp_path / "gap_report.json").write_text(
            json.dumps({"000670.SZ": 5}), encoding="utf-8"
        )
        with pytest.raises(SystemExit, match="'000670.SZ' must map to a list"):
            self._verify(tmp_path, db_path, "--expect", "1")

    def test_well_formed_gap_values_still_pass_the_guard(self, tmp_path):
        """The guard must not reject the real artifact shape (list of holes)."""
        db_path = tmp_path / "k.db"
        self._seed_db(db_path, symbols=("600519.SH",))
        self._artifacts(tmp_path, gap_symbol=None)
        (tmp_path / "gap_report.json").write_text(
            json.dumps({"000670.SZ": [["2026-04-30", "2026-07-07", 68]]}),
            encoding="utf-8",
        )
        assert self._verify(tmp_path, db_path, "--expect", "1") == 1  # unexplained

    def test_bad_date_string_in_allowlist_fails_through_main(self, tmp_path):
        """A str that is not a date passes the type guard; the format still matters.

        ``_require_str`` proves the type; ``"2023-13-45"`` is a string. Without a
        format check it reaches ``_day_ms`` → ``strptime`` and raises a bare
        ValueError from the calendar math.
        """
        db_path = tmp_path / "k.db"
        self._seed_db(db_path, symbols=("600519.SH",))
        self._artifacts(tmp_path, gap_symbol=None)
        (tmp_path / "allowlist.json").write_text(
            json.dumps({"window_start": "2023-13-45", "symbols": ["600519.SH"]}),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit, match="not a YYYY-MM-DD date"):
            self._verify(tmp_path, db_path, "--expect", "1")

    def test_bad_window_start_flag_fails_loudly(self, tmp_path):
        db_path = tmp_path / "k.db"
        self._seed_db(db_path, symbols=("600519.SH",))
        self._artifacts(tmp_path, gap_symbol=None)
        with pytest.raises(SystemExit, match="not a YYYY-MM-DD date"):
            self._verify(
                tmp_path, db_path, "--expect", "1", "--window-start", "notadate"
            )

    def test_unexplained_gap_fails(self, tmp_path):
        db_path = tmp_path / "k.db"
        self._seed_db(db_path, symbols=("600519.SH",))
        self._artifacts(tmp_path, gap_symbol="000670.SZ")
        assert self._verify(tmp_path, db_path, "--expect", "1") == 1

    def test_honest_halts_pass_the_ratio_criterion(self, tmp_path):
        """A name that halts often must not read as a truncated one.

        Regression: criterion (2) was ">= 700 bars", which the 727-session
        window turns into ">= 96% coverage". 603389.SH trades 694/727 (95.5%)
        across ~33 sessions of short halts — an honest name the absolute test
        rejected, identically to a real truncation. Here the symbol is seeded
        with 95% of the calendar's sessions and must pass.
        """
        db_path = tmp_path / "k.db"
        sessions = vd.count_sessions("2023-10-17", "2026-10-16")
        assert sessions, "calendar must be available for this test to mean anything"
        # Fixture days are weekdays, so a ratio of sessions and a count of
        # weekdays agree closely enough to place the symbol at ~95% coverage.
        self._seed_db(db_path, bars=int(sessions * 0.95))
        self._artifacts(tmp_path, gap_symbol=None)
        assert self._verify(tmp_path, db_path, "--expect", "1") == 0
        # ...and the same rows must still fail a stricter bar.
        assert (
            self._verify(tmp_path, db_path, "--expect", "1", "--min-coverage", "0.99")
            == 1
        )

    def test_truncated_history_fails_the_ratio_criterion(self, tmp_path):
        """The ratio must still catch real truncation, or it is decoration."""
        db_path = tmp_path / "k.db"
        sessions = vd.count_sessions("2023-10-17", "2026-10-16")
        self._seed_db(db_path, bars=int(sessions * 0.5))
        self._artifacts(tmp_path, gap_symbol=None)
        assert (
            self._verify(tmp_path, db_path, "--expect", "1", "--min-coverage", "0.90")
            == 1
        )

    def test_allowlist_symbol_absent_from_db_fails(self, tmp_path):
        """An allowlist entry with no rows must fail, not silently pass.

        A GROUP BY only returns symbols that have rows, so the naive form of
        this check never sees a wholly missing symbol.
        """
        db_path = tmp_path / "k.db"
        self._seed_db(db_path, symbols=("000001.SZ",))
        self._artifacts(tmp_path, gap_symbol=None)  # allowlist names 600519.SH
        assert (
            self._verify(tmp_path, db_path, "--expect", "1", "--min-coverage", "0.90")
            == 1
        )

    def test_explained_gap_passes(self, tmp_path):
        db_path = tmp_path / "k.db"
        self._seed_db(db_path, symbols=("600519.SH",))
        self._artifacts(tmp_path, gap_symbol="000670.SZ")
        susp = tmp_path / "suspensions.json"
        susp.write_text('{"symbols": ["000670.SZ"]}', encoding="utf-8")
        assert (
            self._verify(tmp_path, db_path, "--expect", "1", "--suspensions", str(susp))
            == 0
        )

    def test_missing_db_is_loud_not_silent_zero(self, tmp_path):
        self._artifacts(tmp_path, gap_symbol=None)
        with pytest.raises(SystemExit):
            self._verify(tmp_path, tmp_path / "absent.db", "--expect", "1")


class TestBBSEPeerTest:
    """The Beijing fallback in ``xcheck_suspensions``.

    Tencent carries no BSE data, so a ``.BJ`` hole cannot be cross-checked
    against a third-party quote. The peer test replaces it: the exchange was
    demonstrably open when other Beijing names traded. These assertions pin the
    two ways that must NOT become an excuse — too few peers, and a peer count
    quietly read from the symbol under test.
    """

    def _seed(
        self,
        db_path,
        *,
        target: str,
        target_days: list[str],
        peers: dict[str, list[str]],
    ) -> None:
        db = KlineDB(db_path=str(db_path))
        with db.connect():
            rows = [e for e in bf.parse_rows(_rows(target_days), target).events]
            for sym, days in peers.items():
                rows.extend(bf.parse_rows(_rows(days), sym).events)
            db.put_klines(rows)

    def test_peers_trading_inside_the_hole_support_the_excuse(self, tmp_path):
        db_path = tmp_path / "k.db"
        days = _weekdays("2023-10-17", 40)
        holed = [d for d in days if not ("2023-11-01" <= d <= "2023-11-20")]
        self._seed(
            db_path,
            target="920675.BJ",
            target_days=holed,
            peers={f"9200{i:02d}.BJ": days for i in range(25)},
        )
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            probes = xs._bse_probe_days("2023-11-01", "2023-11-20")
            assert probes, "probe days must exist inside a 19-day hole"
            counts = [xs._peer_count_on(conn, d, "920675.BJ") for d in probes]
        finally:
            conn.close()
        assert (
            min(counts) >= xs.MIN_BSE_PEERS
        ), f"25 peers trade through the hole, so peers must be seen: {counts}"

    def test_a_quiet_exchange_is_not_an_excuse(self, tmp_path):
        """If nobody else traded, the hole may be a source outage."""
        db_path = tmp_path / "k.db"
        days = _weekdays("2023-10-17", 40)
        holed = [d for d in days if not ("2023-11-01" <= d <= "2023-11-20")]
        # Only two peers, both silent during the hole: the exchange looks shut.
        self._seed(
            db_path,
            target="920675.BJ",
            target_days=holed,
            peers={"920001.BJ": holed, "920002.BJ": holed},
        )
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            counts = [
                xs._peer_count_on(conn, d, "920675.BJ")
                for d in xs._bse_probe_days("2023-11-01", "2023-11-20")
            ]
        finally:
            conn.close()
        assert (
            max(counts) < xs.MIN_BSE_PEERS
        ), f"a near-silent exchange must not clear the bar: {counts}"

    def test_the_symbol_under_test_is_excluded_from_its_own_peer_count(self, tmp_path):
        """Otherwise the test is circular: the hole would excuse itself."""
        db_path = tmp_path / "k.db"
        days = _weekdays("2023-10-17", 40)
        # The target itself trades on every probe day; if it were counted as a
        # peer, its own bars would manufacture the evidence to excuse its hole.
        self._seed(db_path, target="920675.BJ", target_days=days, peers={})
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            counts = [
                xs._peer_count_on(conn, d, "920675.BJ")
                for d in xs._bse_probe_days("2023-11-01", "2023-11-20")
            ]
        finally:
            conn.close()
        assert max(counts) == 0, f"target leaked into its own peer count: {counts}"

    def test_non_beijing_symbols_are_not_peers(self, tmp_path):
        """The peer test answers "was the Beijing exchange open?".

        A Shenzhen or Shanghai name trading on the probe day says nothing about
        that, so the count must stay inside the Beijing board — otherwise a
        hole in a BJ symbol could be excused by an unrelated market being open.
        """
        db_path = tmp_path / "k.db"
        days = _weekdays("2023-10-17", 40)
        holed = [d for d in days if not ("2023-11-01" <= d <= "2023-11-20")]
        self._seed(
            db_path,
            target="920675.BJ",
            target_days=holed,
            peers={f"0000{i:02d}.SZ": days for i in range(25)}
            | {f"6005{i:02d}.SH": days for i in range(25)},
        )
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            counts = [
                xs._peer_count_on(conn, d, "920675.BJ")
                for d in xs._bse_probe_days("2023-11-01", "2023-11-20")
            ]
        finally:
            conn.close()
        assert (
            max(counts) == 0
        ), f"50 non-BJ names traded but none is a Beijing peer: {counts}"

    def test_probe_days_are_strictly_inside_the_hole(self):
        probes = xs._bse_probe_days("2023-11-01", "2023-11-20")
        assert probes, "a 19-day hole must yield probe days"
        assert all("2023-11-01" < d < "2023-11-20" for d in probes)
        assert all(datetime.strptime(d, "%Y-%m-%d").weekday() < 5 for d in probes)

    def test_hole_too_short_yields_no_probes(self):
        # A Friday-to-Monday gap has no weekday strictly inside it; the caller
        # must treat an empty probe list as "no evidence", not as "fine".
        assert xs._bse_probe_days("2023-11-03", "2023-11-06") == []


class TestTencentRetry:
    """A vendor that stops answering must not silently excuse a symbol."""

    def test_retries_then_raises(self, monkeypatch):
        class Boom:
            def get(self, *a, **kw):
                raise xs.requests.ConnectionError("down")

        monkeypatch.setattr(xs.time, "sleep", lambda *_: None)
        with pytest.raises(ConnectionError):
            xs.tencent_days("600519.SH", "2024-01-01", "2024-02-01", Boom())

    def test_a_transient_failure_is_retried(self, monkeypatch):
        class Flaky:
            def __init__(self):
                self.calls = 0

            def get(self, *a, **kw):
                self.calls += 1
                if self.calls == 1:
                    raise xs.requests.ConnectionError("blip")
                return _Resp()

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "data": {"sh600519": {"qfqday": [["2024-01-02", 1, 1, 1, 1, 1]]}}
                }

        monkeypatch.setattr(xs.time, "sleep", lambda *_: None)
        sess = Flaky()
        days = xs.tencent_days("600519.SH", "2024-01-01", "2024-02-01", sess)
        assert days == ["2024-01-02"]
        assert sess.calls == 2, "one blip must cost one retry, not a verdict"


class TestXcheckMain:
    """``xcheck_suspensions.main`` end to end, with the vendors stubbed.

    The BSE fallback and the Tencent branch make opposite assertions about the
    same hole ("nobody traded" vs "here are the bars"), so both are driven
    through the real ``main`` rather than through the helpers alone: the exit
    code is what feeds the acceptance gate, and a helper that works while the
    wiring around it does not would still pass a green acceptance.
    """

    def _args(self, monkeypatch, tmp_path, db_path):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "xcheck_suspensions",
                "--db",
                str(db_path),
                "--data-dir",
                str(tmp_path),
                "--years",
                "3.0",
            ],
        )
        monkeypatch.setattr(xs.time, "sleep", lambda *_: None)

    def _read(self, tmp_path):
        return json.loads((tmp_path / "suspensions.json").read_text(encoding="utf-8"))

    def _hole(self, window_start: str) -> tuple[list[str], str, str]:
        """60 weekdays from the window start, minus a 19-weekday block."""
        days = _weekdays(window_start, 60)
        holed = [d for i, d in enumerate(days) if not (11 < i < 30)]
        return holed, days[11], days[30]

    def _seed(self, db_path, window_start, *, target, peers):
        days = _weekdays(window_start, 60)
        holed, lo, hi = self._hole(window_start)
        db = KlineDB(db_path=str(db_path))
        with db.connect():
            rows = list(bf.parse_rows(_rows(holed), target).events)
            for sym in peers:
                rows.extend(bf.parse_rows(_rows(days), sym).events)
            db.put_klines(rows)
        return lo, hi

    def test_bj_hole_with_peers_is_excused(self, tmp_path, monkeypatch):
        db_path = tmp_path / "k.db"
        start = bf._window_start(3.0)
        self._seed(
            db_path,
            start,
            target="920675.BJ",
            peers=[f"9200{i:02d}.BJ" for i in range(25)],
        )
        self._args(monkeypatch, tmp_path, db_path)
        assert xs.main() == 0
        data = self._read(tmp_path)
        assert data["symbols"] == ["920675.BJ"]
        assert data["inconclusive"] == {}
        assert data["evidence"]["920675.BJ"]["source"] == "bse_peer_test"

    def test_bj_hole_without_peers_is_inconclusive_not_excused(
        self, tmp_path, monkeypatch
    ):
        """A quiet Beijing tape is not proof the stock was halted."""
        db_path = tmp_path / "k.db"
        start = bf._window_start(3.0)
        self._seed(
            db_path,
            start,
            target="920675.BJ",
            peers=["920001.BJ", "920002.BJ"],
        )
        self._args(monkeypatch, tmp_path, db_path)
        assert xs.main() == 1, "an unexcused hole must keep the gate failing"
        data = self._read(tmp_path)
        assert data["symbols"] == []
        assert "920675.BJ" in data["inconclusive"]
        assert "920675.BJ" in data["evidence"], "evidence is kept for review"

    def _sh_case(self, tmp_path, monkeypatch, *, tencent):
        db_path = tmp_path / "k.db"
        start = bf._window_start(3.0)
        self._seed(db_path, start, target="600519.SH", peers=[])
        self._args(monkeypatch, tmp_path, db_path)
        monkeypatch.setattr(xs, "tencent_days", tencent)
        return xs.main()

    def test_tencent_bars_inside_the_hole_are_disputed(self, tmp_path, monkeypatch):
        days = _weekdays(bf._window_start(3.0), 60)
        rc = self._sh_case(tmp_path, monkeypatch, tencent=lambda *a, **kw: days)
        assert rc == 1
        data = self._read(tmp_path)
        assert "600519.SH" in data["disputed"]
        assert data["symbols"] == []

    def test_tencent_agreeing_the_stock_was_dark_excuses_it(
        self, tmp_path, monkeypatch
    ):
        holed, _, _ = self._hole(bf._window_start(3.0))
        rc = self._sh_case(tmp_path, monkeypatch, tencent=lambda *a, **kw: holed)
        assert rc == 0
        data = self._read(tmp_path)
        assert data["symbols"] == ["600519.SH"]
        assert data["evidence"]["600519.SH"]["tencent_bars_in_window"] == len(holed)

    def test_an_empty_control_is_inconclusive(self, tmp_path, monkeypatch):
        """No bars at all means the query failed, not that the stock is dark."""
        rc = self._sh_case(tmp_path, monkeypatch, tencent=lambda *a, **kw: [])
        assert rc == 1
        assert "600519.SH" in self._read(tmp_path)["inconclusive"]

    def test_a_vendor_outage_is_inconclusive(self, tmp_path, monkeypatch):
        def down(*a, **kw):
            raise ConnectionError("tencent unreachable")

        rc = self._sh_case(tmp_path, monkeypatch, tencent=down)
        assert rc == 1, "an outage must never read as an excuse"
        assert "600519.SH" in self._read(tmp_path)["inconclusive"]

    def test_dry_run_writes_no_artifact(self, tmp_path, monkeypatch):
        db_path = tmp_path / "k.db"
        start = bf._window_start(3.0)
        self._seed(
            db_path,
            start,
            target="920675.BJ",
            peers=[f"9200{i:02d}.BJ" for i in range(25)],
        )
        self._args(monkeypatch, tmp_path, db_path)
        monkeypatch.setattr(sys, "argv", sys.argv + ["--dry-run"])
        assert xs.main() == 0
        assert not (tmp_path / "suspensions.json").exists()

    def test_resume_reuses_prior_verdicts_without_re_requesting(
        self, tmp_path, monkeypatch
    ):
        """The cross-check is rate-limited; re-running must not re-ask."""
        db_path = tmp_path / "k.db"
        start = bf._window_start(3.0)
        self._seed(db_path, start, target="600519.SH", peers=[])
        holed, _, _ = self._hole(start)
        (tmp_path / "suspensions.json").write_text(
            json.dumps({"symbols": ["600519.SH"], "evidence": {"600519.SH": {}}}),
            encoding="utf-8",
        )
        calls = []

        def counted(*a, **kw):
            calls.append(a)
            return holed

        self._args(monkeypatch, tmp_path, db_path)
        monkeypatch.setattr(xs, "tencent_days", counted)
        monkeypatch.setattr(sys, "argv", sys.argv + ["--resume"])
        assert xs.main() == 0
        assert calls == [], "a reused verdict must not hit the vendor again"
        assert "600519.SH" in self._read(tmp_path)["symbols"]

    def test_non_string_inconclusive_value_is_rejected(self, tmp_path, monkeypatch):
        """The container guard is not the value guard.

        ``inconclusive`` is checked to be a dict, but the resume loop reads its
        *values* and substring-tests them ("unreachable" not in reason). An int
        value is therefore a bare TypeError from inside the loop, which reads as
        a bug in this script rather than as a corrupt prior file — the same
        document-shape-vs-value gap as ``gap_report``.
        """
        db_path = tmp_path / "k.db"
        start = bf._window_start(3.0)
        self._seed(db_path, start, target="600519.SH", peers=[])
        (tmp_path / "suspensions.json").write_text(
            json.dumps(
                {
                    "symbols": [],
                    "evidence": {"600519.SH": {}},
                    "inconclusive": {"600519.SH": 5},
                }
            ),
            encoding="utf-8",
        )
        self._args(monkeypatch, tmp_path, db_path)
        monkeypatch.setattr(sys, "argv", sys.argv + ["--resume"])
        with pytest.raises(SystemExit, match=r"inconclusive\['600519.SH'\]"):
            xs.main()

    def test_non_dict_evidence_value_is_rejected(self, tmp_path, monkeypatch):
        """Evidence is the frozen review surface; a scalar must not be carried in.

        Without this the value is reused verbatim and rewritten into the new
        artifact — no crash, but garbage propagates to the file a human reads to
        decide whether a hole is a genuine halt.
        """
        db_path = tmp_path / "k.db"
        start = bf._window_start(3.0)
        self._seed(db_path, start, target="600519.SH", peers=[])
        (tmp_path / "suspensions.json").write_text(
            json.dumps({"symbols": [], "evidence": {"600519.SH": 5}}),
            encoding="utf-8",
        )
        self._args(monkeypatch, tmp_path, db_path)
        monkeypatch.setattr(sys, "argv", sys.argv + ["--resume"])
        with pytest.raises(SystemExit, match=r"evidence\['600519.SH'\]"):
            xs.main()

    def test_unhashable_symbols_element_is_rejected(self, tmp_path, monkeypatch):
        """``symbols`` elements are read via ``set(...)``, which raises on a list.

        The reviewer listed this path as safe; probing showed ``set([[1, 2]])``
        raises before the membership test, so it is the same bare-traceback class.
        """
        db_path = tmp_path / "k.db"
        start = bf._window_start(3.0)
        self._seed(db_path, start, target="600519.SH", peers=[])
        (tmp_path / "suspensions.json").write_text(
            json.dumps({"symbols": [[1, 2]], "evidence": {"600519.SH": {}}}),
            encoding="utf-8",
        )
        self._args(monkeypatch, tmp_path, db_path)
        monkeypatch.setattr(sys, "argv", sys.argv + ["--resume"])
        with pytest.raises(SystemExit, match="symbols\\[0\\]"):
            xs.main()

    def test_non_list_disputed_value_is_rejected(self, tmp_path, monkeypatch):
        db_path = tmp_path / "k.db"
        start = bf._window_start(3.0)
        self._seed(db_path, start, target="600519.SH", peers=[])
        (tmp_path / "suspensions.json").write_text(
            json.dumps(
                {
                    "symbols": [],
                    "evidence": {"600519.SH": {}},
                    "disputed": {"600519.SH": "nope"},
                }
            ),
            encoding="utf-8",
        )
        self._args(monkeypatch, tmp_path, db_path)
        monkeypatch.setattr(sys, "argv", sys.argv + ["--resume"])
        with pytest.raises(SystemExit, match=r"disputed\['600519.SH'\]"):
            xs.main()

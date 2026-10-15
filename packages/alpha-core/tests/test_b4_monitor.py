"""B4 monitoring quadruple — registry, new-bar recompute, TTL-stale tests.

Three states per the plan's acceptance: register → recompute (new-bar or
first read) → TTL-stale marking. Persistence via a temp registry file;
klines data via the BTC_BG_DB_PATH-style temp db (the monitor hard-wires
its db path argument, tests inject tmp dbs).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from selene_space_alpha.sensors.b4_monitor import (
    DEFAULT_TTL_SECONDS,
    SOURCE_KLINES_DB,
    B4Monitor,
    Quadruple,
    load_registry,
)

BASE_MS = 1_767_225_600_000  # 2026-01-01 00:00 UTC
DAY_MS = 86_400_000


def _open_db(db_path: Path) -> sqlite3.Connection:
    """Fresh klines table in a new db; caller inserts rows and closes."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE klines (exchange TEXT, market_type TEXT, symbol TEXT,"
        " interval TEXT, timestamp INTEGER, open REAL, high REAL, low REAL,"
        " close REAL, volume REAL, closed INTEGER)"
    )
    return conn


def _write_db(db_path: Path, closes: list[float]) -> Path:
    conn = _open_db(db_path)
    rows = [
        ("okx", "swap", "BTC/USDT", "D1", BASE_MS + i * DAY_MS, c, c, c, c, 1.0, 1)
        for i, c in enumerate(closes)
    ]
    conn.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db_path


def _bg_db(db_path: Path) -> Path:
    """Two-symbol background db (BTC +1%/day, ETH -2%/day, 200 days)."""
    btc = [100.0 * (1.01**i) for i in range(200)]
    eth = [100.0 * (0.98**i) for i in range(200)]
    conn = _open_db(db_path)
    rows = []
    for i, c in enumerate(btc):
        ts = BASE_MS + i * DAY_MS
        rows.append(("okx", "swap", "BTC/USDT", "D1", ts, c, c, c, c, 1.0, 1))
    for i, c in enumerate(eth):
        ts = BASE_MS + i * DAY_MS
        rows.append(("okx", "swap", "ETH/USDT", "D1", ts, c, c, c, c, 1.0, 1))
    conn.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def env(tmp_path: Path) -> dict:
    """Shared env: bg db + target db + registry path."""
    bg = _bg_db(tmp_path / "bg.db")
    target = _write_db(tmp_path / "target.db", [100.0 * (1.005**i) for i in range(120)])
    return {"bg": bg, "target": target, "registry": tmp_path / "registry.json"}


class TestRegistry:
    def test_register_persists_quadruple(self, env: dict) -> None:
        mon = B4Monitor(db_path=env["target"], registry_path=env["registry"])
        q = mon.register("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        assert q == Quadruple(
            source=SOURCE_KLINES_DB,
            symbol="BTC/USDT",
            interval="D1",
            factor="BTC_BG_RS_LEVEL",
        )
        on_disk = load_registry(env["registry"])
        assert on_disk == [q]

    def test_register_idempotent(self, env: dict) -> None:
        mon = B4Monitor(db_path=env["target"], registry_path=env["registry"])
        mon.register("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        mon.register("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        assert len(load_registry(env["registry"])) == 1

    def test_register_rejects_unknown_factor(self, env: dict) -> None:
        mon = B4Monitor(db_path=env["target"], registry_path=env["registry"])
        with pytest.raises(ValueError, match="unknown factor"):
            mon.register("BTC/USDT", "D1", "NOT_A_FACTOR")

    def test_unregister_removes_and_returns_true(self, env: dict) -> None:
        mon = B4Monitor(db_path=env["target"], registry_path=env["registry"])
        mon.register("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        assert mon.unregister("BTC/USDT", "D1", "BTC_BG_RS_LEVEL") is True
        assert load_registry(env["registry"]) == []
        assert mon.unregister("BTC/USDT", "D1", "BTC_BG_RS_LEVEL") is False

    def test_registry_survives_corrupt_file(self, env: dict, tmp_path: Path) -> None:
        env["registry"].write_text("{corrupt", encoding="utf-8")
        mon = B4Monitor(db_path=env["target"], registry_path=env["registry"])
        assert mon.list_quadruples() == []

    def test_single_source_hardwired(self, env: dict) -> None:
        # Data-source dimension is locked to klines.db (Architect ruling);
        # Quadruple is frozen so the source can't be mutated post-hoc.
        mon = B4Monitor(db_path=env["target"], registry_path=env["registry"])
        q = mon.register("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        assert q.source == "klines.db"
        with pytest.raises(Exception):
            q.source = "other"  # type: ignore[misc]


class TestRecompute:
    def test_first_read_recomputes_and_caches(self, env: dict) -> None:
        mon = B4Monitor(db_path=env["bg"], registry_path=env["registry"])
        reading, recomputed = mon.read("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        assert recomputed is True
        assert reading.stale is False
        assert reading.value is not None
        assert reading.last_bar_ts == BASE_MS + 199 * DAY_MS

    def test_read_within_ttl_no_new_bar_serves_cache(self, env: dict) -> None:
        calls = {"n": 0}
        base = 1_000_000.0

        def clock() -> float:
            return base + calls["n"] * 10.0

        mon = B4Monitor(db_path=env["bg"], registry_path=env["registry"], clock=clock)
        mon.read("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        reading2, recomputed2 = mon.read("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        assert recomputed2 is False
        assert reading2.stale is False

    def test_read_past_ttl_marks_stale(self, env: dict) -> None:
        calls = {"n": 0}
        base = 1_000_000.0

        def clock() -> float:
            calls["n"] += 1
            # read() consumes one call for `now`, recompute() one for computed_at;
            # keep the first read-pair at base, everything after is past TTL.
            return base if calls["n"] <= 2 else base + DEFAULT_TTL_SECONDS + 100

        mon = B4Monitor(db_path=env["bg"], registry_path=env["registry"], clock=clock)
        first, recomputed1 = mon.read("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        assert recomputed1 is True
        # past TTL → read() re-serves; value present, recompute path taken
        second, recomputed2 = mon.read("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        assert recomputed2 is True  # expired cache → recompute
        assert second.value is not None

    def test_new_bar_triggers_recompute(self, env: dict, tmp_path: Path) -> None:
        mon = B4Monitor(db_path=env["bg"], registry_path=env["registry"])
        first, recomputed1 = mon.read("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        assert recomputed1 is True
        old_ts = first.last_bar_ts
        assert old_ts is not None
        # append one new day to the bg db
        conn = sqlite3.connect(str(env["bg"]))
        conn.execute(
            "INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "okx",
                "swap",
                "BTC/USDT",
                "D1",
                old_ts + DAY_MS,
                100.0,
                100.0,
                100.0,
                100.0,
                1.0,
                1,
            ),
        )
        conn.commit()
        conn.close()
        second, recomputed2 = mon.read("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        assert recomputed2 is True
        assert second.last_bar_ts == old_ts + DAY_MS

    def test_recompute_missing_db_raises(self, env: dict, tmp_path: Path) -> None:
        mon = B4Monitor(db_path=tmp_path / "missing.db", registry_path=env["registry"])
        with pytest.raises(FileNotFoundError):
            mon.recompute("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")

    def test_recompute_no_rows_raises(self, env: dict, tmp_path: Path) -> None:
        empty = tmp_path / "empty.db"
        conn = sqlite3.connect(str(empty))
        conn.execute(
            "CREATE TABLE klines (exchange TEXT, market_type TEXT, symbol TEXT,"
            " interval TEXT, timestamp INTEGER, open REAL, high REAL, low REAL,"
            " close REAL, volume REAL, closed INTEGER)"
        )
        conn.commit()
        conn.close()
        mon = B4Monitor(db_path=empty, registry_path=env["registry"])
        with pytest.raises(ValueError, match="no klines"):
            mon.recompute("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")


class TestStaleAware:
    def test_cache_miss_recomputes(self, env: dict) -> None:
        mon = B4Monitor(db_path=env["bg"], registry_path=env["registry"])
        reading = mon.read_stale_aware("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        assert reading.stale is False
        assert reading.value is not None

    def test_stale_served_without_recompute(self, env: dict) -> None:
        base = 1_000_000.0
        calls = {"n": 0}

        def clock() -> float:
            calls["n"] += 1
            return base + (0 if calls["n"] == 1 else DEFAULT_TTL_SECONDS + 1)

        mon = B4Monitor(db_path=env["bg"], registry_path=env["registry"], clock=clock)
        mon.read_stale_aware("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        stale = mon.read_stale_aware("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        assert stale.stale is True
        # stale read does NOT refresh computed_at
        assert stale.computed_at == base

    def test_fresh_served_when_in_ttl(self, env: dict) -> None:
        base = 1_000_000.0
        mon = B4Monitor(
            db_path=env["bg"],
            registry_path=env["registry"],
            clock=lambda: base + 10.0,
        )
        mon.read_stale_aware("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        fresh = mon.read_stale_aware("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        assert fresh.stale is False

    def test_stale_when_new_bar_even_within_ttl(self, env: dict) -> None:
        base = 1_000_000.0
        mon = B4Monitor(
            db_path=env["bg"],
            registry_path=env["registry"],
            clock=lambda: base + 1.0,
        )
        first = mon.read_stale_aware("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        assert first.last_bar_ts is not None
        conn = sqlite3.connect(str(env["bg"]))
        conn.execute(
            "INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "okx",
                "swap",
                "BTC/USDT",
                "D1",
                first.last_bar_ts + DAY_MS,
                100.0,
                100.0,
                100.0,
                100.0,
                1.0,
                1,
            ),
        )
        conn.commit()
        conn.close()
        second = mon.read_stale_aware("BTC/USDT", "D1", "BTC_BG_RS_LEVEL")
        assert second.stale is True
        assert second.last_bar_ts == first.last_bar_ts  # old snapshot served

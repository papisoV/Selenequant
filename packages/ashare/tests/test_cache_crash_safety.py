"""Crash safety for the A-share on-disk caches.

Every writer here used to open the target in place, so a process killed
mid-write (dev-server restart, container stop, power loss) left a file that
existed but could not be read. The read side then failed on every call with
nothing to repair it, because nothing ever rewrites a cache that looks
present. These tests pin both halves: writes leave no truncated file, and an
unreadable file degrades to a miss that the next fetch overwrites.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import requests as _requests

from selene_space_ashare import data_fetcher, fundamentals_pit, ths_data


@pytest.fixture()
def cache_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(data_fetcher, "CACHE_DIR", tmp_path / "df")
    monkeypatch.setattr(ths_data, "CACHE_DIR", tmp_path / "ths")
    return tmp_path


# ─── parquet caches: write must never expose a partial file ──────────


class TestParquetCacheWritesAreAtomic:
    @pytest.mark.parametrize(
        ("module", "label"),
        [(data_fetcher, "data_fetcher"), (ths_data, "ths_data")],
        ids=["data_fetcher", "ths_data"],
    )
    def test_reader_sees_the_previous_snapshot_during_a_write(
        self, cache_dirs, monkeypatch, module, label
    ):
        """The old frame stays readable for the whole write.

        Opening the target directly truncated it first, which is exactly the
        window that turns a kill into a permanently unreadable cache.
        """
        module._write_cache(pd.DataFrame({"close": [1.0]}), "probe", "sub")
        target = module._cache_path("probe", "sub")
        seen: list[tuple[str, int]] = []

        real_write = pd.DataFrame.to_parquet

        def _observing_write(self, path, *a, **kw):
            # Whatever the writer is doing, it is not the live file, and the
            # live file is still complete.
            seen.append((str(path), len(pd.read_parquet(target))))
            return real_write(self, path, *a, **kw)

        monkeypatch.setattr(pd.DataFrame, "to_parquet", _observing_write)

        module._write_cache(pd.DataFrame({"close": [1.0, 2.0, 3.0]}), "probe", "sub")

        assert [n for _, n in seen] == [1], f"{label} truncated the cache first"
        assert seen[0][0] != str(target), f"{label} wrote in place over the live cache"
        assert len(pd.read_parquet(target)) == 3

    @pytest.mark.parametrize(
        "module", [data_fetcher, ths_data], ids=["data_fetcher", "ths_data"]
    )
    def test_no_temp_files_remain(self, cache_dirs, module):
        module._write_cache(pd.DataFrame({"close": [1.0]}), "probe", "sub")
        assert list(module._cache_path("probe", "sub").parent.glob(".*tmp*")) == []

    @pytest.mark.parametrize(
        "module", [data_fetcher, ths_data], ids=["data_fetcher", "ths_data"]
    )
    def test_failed_write_keeps_the_previous_snapshot(
        self, cache_dirs, monkeypatch, module
    ):
        module._write_cache(pd.DataFrame({"close": [1.0]}), "probe", "sub")
        target = module._cache_path("probe", "sub")

        def _boom(self, path, *a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(pd.DataFrame, "to_parquet", _boom)

        with pytest.raises(OSError, match="disk full"):
            module._write_cache(pd.DataFrame({"close": [9.0]}), "probe", "sub")

        assert list(pd.read_parquet(target)["close"]) == [1.0]


# ─── parquet caches: an unreadable file is a miss, not a crash ───────


class TestUnreadableParquetCacheIsAMiss:
    @pytest.mark.parametrize(
        "module", [data_fetcher, ths_data], ids=["data_fetcher", "ths_data"]
    )
    def test_truncated_file_reads_as_absent(self, cache_dirs, module):
        target = module._cache_path("probe", "sub")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"PAR1 truncated garbage")

        assert module._read_cache("probe", "sub") is None

    @pytest.mark.parametrize(
        "module", [data_fetcher, ths_data], ids=["data_fetcher", "ths_data"]
    )
    def test_truncated_file_is_overwritten_on_the_next_write(self, cache_dirs, module):
        """A miss is only useful if the next fetch can heal the file."""
        target = module._cache_path("probe", "sub")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"PAR1 truncated garbage")

        module._write_cache(pd.DataFrame({"close": [4.0]}), "probe", "sub")

        assert module._read_cache("probe", "sub") is not None
        assert list(pd.read_parquet(target)["close"]) == [4.0]


# ─── the limit-up stats cache: JSON in a .parquet-named file ─────────


class TestLimitUpStatsCache:
    def test_failed_write_keeps_the_previous_snapshot(
        self, cache_dirs, monkeypatch, stub_limit_up_network
    ):
        """A write interrupted mid-flight must not cost the old numbers.

        The old writer opened the target in place, so this file would have
        been truncated to nothing before the dump even began.
        """
        target = ths_data._cache_path("stats_20260105", "ths_limitup")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"limit_up_today": 3}), encoding="utf-8")

        def _boom(self, *a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_bytes", _boom)

        with pytest.raises(OSError, match="disk full"):
            ths_data.fetch_limit_up_stats(date="20260105", force=True)

        assert json.loads(target.read_text(encoding="utf-8"))["limit_up_today"] == 3

    def test_unreadable_stats_cache_refetches_instead_of_raising(
        self, cache_dirs, stub_limit_up_network
    ):
        """A half-written stats file used to surface as JSONDecodeError."""
        target = ths_data._cache_path("stats_20260105", "ths_limitup")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"limit_up_today": 12,', encoding="utf-8")  # truncated

        fetched = ths_data.fetch_limit_up_stats(date="20260105")

        assert fetched["limit_up_today"] == 30
        # and the refetch repaired the file
        assert json.loads(target.read_text(encoding="utf-8"))["limit_up_today"] == 30

    def test_decodable_stats_cache_is_served_without_a_fetch(
        self, cache_dirs, monkeypatch
    ):
        target = ths_data._cache_path("stats_20260105", "ths_limitup")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"limit_up_today": 7}), encoding="utf-8")

        def _no_fetch(*a, **kw):
            raise AssertionError("cache hit must not hit the network")

        monkeypatch.setattr(_requests, "get", _no_fetch)

        assert ths_data.fetch_limit_up_stats(date="20260105") == {"limit_up_today": 7}


@pytest.fixture()
def stub_limit_up_network(monkeypatch):
    """fetch_limit_up_stats imports requests inside its body; stub the module."""

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "data": {
                    "limit_up_count": {
                        "today": {"num": 30, "rate": 0.7, "open_num": 4},
                        "yesterday": {"num": 20},
                    },
                    "limit_down_count": {"today": {"num": 2}, "yesterday": {"num": 1}},
                }
            }

    monkeypatch.setattr(_requests, "get", lambda *a, **kw: _Resp())


# ─── financials: the one cache that cannot heal itself ───────────────


class TestFinancialsCache:
    def test_a_crash_mid_write_leaves_the_previous_file_intact(
        self, tmp_path, monkeypatch
    ):
        """The 44 MB rewrite used to truncate the live file first.

        Here the dump dies partway through, standing in for a kill: whatever
        happens, the file that was already there must still parse.
        """
        target = tmp_path / "financials.json"
        fundamentals_pit._save_cache(
            str(target), {"600519|2026|1"}, [{"symbol": "600519"}]
        )
        before = target.read_bytes()

        real_dump = json.dump

        def _dump_then_die(obj, fp, *a, **kw):
            fp.write('{"done": [], "records": [{"symbol": "000')
            raise OSError("disk full")

        monkeypatch.setattr(json, "dump", _dump_then_die)

        with pytest.raises(OSError, match="disk full"):
            fundamentals_pit._save_cache(
                str(target), {"600519|2026|2"}, [{"symbol": "000001"}]
            )

        monkeypatch.setattr(json, "dump", real_dump)
        assert target.read_bytes() == before
        assert json.loads(target.read_text(encoding="utf-8"))["records"] == [
            {"symbol": "600519"}
        ]
        assert list(target.parent.glob(".*tmp*")) == []

    def test_save_leaves_no_partial_file_behind(self, tmp_path):
        target = tmp_path / "financials.json"
        fundamentals_pit._save_cache(
            str(target), {"600519|2026|1"}, [{"symbol": "600519"}]
        )

        assert json.loads(target.read_text(encoding="utf-8"))["records"] == [
            {"symbol": "600519"}
        ]
        assert list(target.parent.glob(".*tmp*")) == []

    def test_unreadable_cache_names_the_fix(self, tmp_path):
        """There is no refetch on this read path, so it must not fail silently.

        Returning [] would drop every fundamental factor from a backtest with
        no trace; a bare JSONDecodeError would not say what to do about it.
        """
        target = tmp_path / "financials.json"
        target.write_text('{"records": [', encoding="utf-8")

        with pytest.raises(RuntimeError, match="fundamentals backfill"):
            fundamentals_pit.load_financials(cache_path=str(target))

    def test_missing_cache_is_still_empty(self, tmp_path):
        assert (
            fundamentals_pit.load_financials(cache_path=str(tmp_path / "nope.json"))
            == []
        )

    @pytest.mark.parametrize(
        ("body", "why"),
        [
            ('[{"symbol": "600519"}]', "a bare list has no records key"),
            ('{"records": "600519"}', "records must be a list, not a string"),
        ],
        ids=["not-an-object", "records-not-a-list"],
    )
    def test_wrong_shaped_cache_is_named_not_silently_empty(self, tmp_path, body, why):
        """A JSON document of the wrong shape must not read as "no records".

        Returning [] here would look exactly like a legitimately empty cache.
        """
        target = tmp_path / "financials.json"
        target.write_text(body, encoding="utf-8")

        with pytest.raises(RuntimeError, match="fundamentals backfill"):
            fundamentals_pit.load_financials(cache_path=str(target))

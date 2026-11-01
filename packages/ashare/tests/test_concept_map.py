"""Concept membership map — parsing, caching, and the degradation contract.

The module's whole job is to make a volunteer-run endpoint safe to depend on, so
the tests are organised around the failure modes rather than the happy path:
a bad payload, a dead host, a corrupt cache, and a cache that is simply old.

Every test isolates the cache directory (``monkeypatch`` on ``CACHE_DIR``)
because the module writes to the real package tree by default — a test that
touched that would leak a 1.6 MB parquet into the developer's working copy and
then pass for the *next* test off that file.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from selene_space_ashare import concept_map


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Point the module's cache at a temp dir; keep the TTL at its default."""
    monkeypatch.setattr(concept_map, "CACHE_DIR", tmp_path / "concept_map")
    monkeypatch.delenv("SELENE_CONCEPT_MAP_TTL_SEC", raising=False)
    return tmp_path / "concept_map"


def _payload_ok() -> str:
    return json.dumps(
        [
            {"symbol": "600519.SH", "name": "贵州茅台", "concepts": ["白酒", "消费"]},
            {"symbol": "000001.sz", "name": "平安银行", "concepts": ["银行"]},
        ]
    )


class _FakeResponse:
    def __init__(self, text: str, status: int = 200) -> None:
        self._text = text
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self._text)


def _install_httpx(monkeypatch, responses, calls: list[str] | None = None):
    """Patch httpx.get inside concept_map; records each URL it is called with."""
    import httpx

    queue = list(responses)

    def fake_get(url, **_kwargs):
        if calls is not None:
            calls.append(url)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(httpx, "get", fake_get)


class TestParsing:
    def test_explode_uppercases_symbol_and_keeps_name(self):
        frame, dropped = concept_map._explode_payload(json.loads(_payload_ok()))

        assert dropped == 0
        assert list(frame.columns) == list(concept_map.COLUMNS)
        # The endpoint is inconsistent about case; the join key downstream is
        # the suffixed upper-case symbol, so normalise at the boundary.
        assert set(frame["symbol"]) == {"600519.SH", "000001.SZ"}
        row = frame[(frame["symbol"] == "600519.SH") & (frame["concept"] == "白酒")]
        assert len(row) == 1
        assert row["name"].iloc[0] == "贵州茅台"

    def test_one_row_per_symbol_concept_pair(self):
        frame, _ = concept_map._explode_payload(json.loads(_payload_ok()))

        # 2 concepts for 600519 + 1 for 000001.
        assert len(frame) == 3
        assert not frame.duplicated(subset=["symbol", "concept"]).any()

    def test_malformed_rows_are_dropped_and_counted(self):
        payload = [
            {"symbol": "600519.SH", "name": "贵州茅台", "concepts": ["白酒"]},
            "not-a-dict",
            {"symbol": "600000.SH", "name": "x"},  # no concepts
            {"symbol": "600001.SH", "name": "y", "concepts": "白酒"},  # not a list
            {"name": "z", "concepts": ["白酒"]},  # no symbol
            {"symbol": "600002.SH", "name": "w", "concepts": []},  # empty list
            {
                "symbol": "600003.SH",
                "name": "v",
                "concepts": [None, "   "],
            },  # junk members
        ]

        frame, dropped = concept_map._explode_payload(payload)

        assert list(frame["symbol"]) == ["600519.SH"]
        assert len(frame) == 1
        # 5 bad rows + 2 junk members; the blank-string member is not counted
        # because "an entry with a blank label" is not a shape violation.
        assert dropped == 7

    def test_non_list_payload_is_empty_not_fatal(self):
        frame, dropped = concept_map._explode_payload({"error": "rate limited"})

        assert frame.empty
        assert list(frame.columns) == list(concept_map.COLUMNS)
        assert dropped == 0


class TestCaching:
    def test_fetch_writes_cache_and_second_call_reads_it(self, monkeypatch):
        calls: list[str] = []
        _install_httpx(monkeypatch, [_FakeResponse(_payload_ok())], calls)

        first = concept_map.fetch_concept_membership()
        assert len(first) == 3
        assert len(calls) == 1
        assert (concept_map.CACHE_DIR / concept_map.CACHE_NAME).exists()

        second = concept_map.fetch_concept_membership()
        # A fresh cache means no second request — the whole point of a TTL on a
        # volunteer-run host.
        assert len(calls) == 1
        assert len(second) == 3

    def test_force_bypasses_cache(self, monkeypatch):
        calls: list[str] = []
        _install_httpx(
            monkeypatch,
            [_FakeResponse(_payload_ok()), _FakeResponse(_payload_ok())],
            calls,
        )

        concept_map.fetch_concept_membership()
        concept_map.fetch_concept_membership(force=True)

        assert len(calls) == 2

    def test_expired_cache_refetches(self, monkeypatch, isolated_cache):
        calls: list[str] = []
        _install_httpx(
            monkeypatch,
            [_FakeResponse(_payload_ok()), _FakeResponse(_payload_ok())],
            calls,
        )
        concept_map.fetch_concept_membership()

        monkeypatch.setenv("SELENE_CONCEPT_MAP_TTL_SEC", "0")
        concept_map.fetch_concept_membership()

        assert len(calls) == 2

    def test_corrupt_cache_is_a_miss_not_a_crash(self, monkeypatch, isolated_cache):
        calls: list[str] = []
        _install_httpx(monkeypatch, [_FakeResponse(_payload_ok())], calls)
        concept_map.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (concept_map.CACHE_DIR / concept_map.CACHE_NAME).write_bytes(b"not parquet")

        frame = concept_map.fetch_concept_membership()

        assert len(frame) == 3
        assert len(calls) == 1

    def test_cache_with_wrong_shape_is_a_miss(self, monkeypatch, isolated_cache):
        calls: list[str] = []
        _install_httpx(monkeypatch, [_FakeResponse(_payload_ok())], calls)
        concept_map.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Written by an older version that had no `name` column.
        pd.DataFrame({"symbol": ["600519.SH"], "concept": ["白酒"]}).to_parquet(
            concept_map.CACHE_DIR / concept_map.CACHE_NAME, index=False
        )

        frame = concept_map.fetch_concept_membership()

        # A refetch, not a crash and not a silently truncated map.
        assert len(calls) == 1
        assert list(frame.columns) == list(concept_map.COLUMNS)


class TestDegradation:
    def test_network_failure_with_no_cache_returns_empty(self, monkeypatch):
        _install_httpx(monkeypatch, [RuntimeError("timed out")])

        frame = concept_map.fetch_concept_membership()

        assert frame.empty
        assert list(frame.columns) == list(concept_map.COLUMNS)

    def test_network_failure_falls_back_to_stale_cache(
        self, monkeypatch, isolated_cache
    ):
        _install_httpx(monkeypatch, [_FakeResponse(_payload_ok())])
        concept_map.fetch_concept_membership()
        # Age the cache past the TTL, then break the network.
        import os
        import time

        path = concept_map.CACHE_DIR / concept_map.CACHE_NAME
        old = time.time() - 3 * 24 * 3600
        os.utime(path, (old, old))

        _install_httpx(monkeypatch, [RuntimeError("host down")])
        frame = concept_map.fetch_concept_membership()

        # Stale-but-real beats empty: the concept blocks can still render.
        assert len(frame) == 3

    def test_unwritable_cache_still_returns_the_frame(self, monkeypatch, tmp_path):
        _install_httpx(monkeypatch, [_FakeResponse(_payload_ok())])
        # Make the cache directory a *file* so mkdir/write must fail.
        blocker = tmp_path / "blocked"
        blocker.write_text("x")
        monkeypatch.setattr(concept_map, "CACHE_DIR", blocker)

        frame = concept_map.fetch_concept_membership()

        assert len(frame) == 3

    def test_empty_payload_from_endpoint_is_treated_as_failure(
        self, monkeypatch, isolated_cache
    ):
        _install_httpx(monkeypatch, [_FakeResponse(json.dumps([]))])

        frame = concept_map.fetch_concept_membership()

        assert frame.empty
        # Nothing was written, so the next call retries rather than trusting an
        # empty cache for a day.
        assert not (concept_map.CACHE_DIR / concept_map.CACHE_NAME).exists()


class TestLiveEndpoint:
    """One network test, opt-in: the suite must stay green offline."""

    @pytest.mark.skipif(
        not __import__("os").environ.get("SELENE_LIVE_ENDPOINT_TESTS"),
        reason="set SELENE_LIVE_ENDPOINT_TESTS=1 to probe the live concept endpoint",
    )
    def test_live_payload_is_what_the_parser_expects(self):
        frame = concept_map.fetch_concept_membership(force=True)

        assert len(frame) > 1000
        assert frame["symbol"].str.contains(r"\.(SH|SZ|BJ)$").all()
        assert frame["concept"].nunique() > 500

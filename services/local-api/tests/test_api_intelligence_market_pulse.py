"""Tests for /api/v1/intelligence/market-pulse/history (US-003 rewrite).

Covers: mock literal removal, three-source assembly, F&G degradation chain
(alt.me -> CNN -> unavailable), snapshot store accumulation/idempotency, and
the always-200 degradation contract.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.v1.intelligence as intelligence_module
from app.api.v1.intelligence import router as intelligence_router
from app.services.market_pulse_store import MarketPulseStore
from selene_space_macrodata.source import MacroSeries, SeriesKind


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(intelligence_router, prefix="/api/v1")
    return app


@pytest.fixture()
def pulse_env(tmp_path, monkeypatch):
    """Isolated store DB + cleared provider singletons + cleared TTL cache."""
    store = MarketPulseStore(tmp_path / "market_pulse.db")
    monkeypatch.setattr(intelligence_module, "_pulse_store", store)
    intelligence_module._pulse_cache.clear()

    yield {"store": store}

    intelligence_module._pulse_cache.clear()


def _series(provider: str, series_id: str, value: float) -> MacroSeries:
    return MacroSeries(
        provider=provider,
        series_id=series_id,
        kind=SeriesKind.CONFIDENCE if "FNG" in series_id else SeriesKind.OTHER,
        description="test",
        observations=pd.DataFrame([{"date": datetime.now(UTC), "value": value}]),
        fetched_at=datetime.now(UTC),
    )


def _stub_providers(
    monkeypatch,
    *,
    altme: Any,
    cnn: Any,
    coingecko: Any,
) -> None:
    """Replace all three module-level lazy singletons with stub objects.

    A value means fetch success; an Exception instance means fetch failure.
    """

    class _Stub:
        def __init__(
            self, result: Any, provider: str, series_ids: dict[str, float]
        ) -> None:
            self._result = result
            self._provider = provider
            self._series_ids = series_ids

        async def fetch_series(self, series_id: str, **kwargs: Any) -> MacroSeries:
            if isinstance(self._result, Exception):
                raise self._result
            return _series(self._provider, series_id, self._series_ids[series_id])

    monkeypatch.setattr(
        intelligence_module,
        "_altme_provider",
        _Stub(altme, "altme_fng", {intelligence_module.ALTME_FNG_SERIES_ID: 71}),
    )
    monkeypatch.setattr(
        intelligence_module,
        "_cnn_provider",
        _Stub(cnn, "cnn_fng", {intelligence_module.CNN_FNG_SERIES_ID: 63}),
    )
    monkeypatch.setattr(
        intelligence_module,
        "_coingecko_provider",
        _Stub(
            coingecko,
            "coingecko_global",
            {
                intelligence_module.BTC_DOMINANCE: 58.2,
                intelligence_module.TOTAL_MARKET_CAP: 1.23e12,
            },
        ),
    )


def _get(client: TestClient) -> dict[str, Any]:
    resp = client.get("/api/v1/intelligence/market-pulse/history")
    assert resp.status_code == 200
    body: dict[str, Any] = resp.json()
    assert body["error"] is None
    data: dict[str, Any] = body["data"]
    return data


class TestMockRemoved:
    def test_no_mock_literals_in_response(self, pulse_env, monkeypatch) -> None:
        _stub_providers(
            monkeypatch,
            altme=RuntimeError("down"),
            cnn=RuntimeError("down"),
            coingecko=RuntimeError("down"),
        )
        client = TestClient(_make_app())
        data = _get(client)
        assert data["current"]["btc_dominance"] is None
        assert data["current"]["fear_greed_index"] is None
        assert data["current"]["market_cap"] is None
        assert data["pulses"] == []

    def test_module_source_has_no_mock_literals(self) -> None:
        import inspect

        src = inspect.getsource(intelligence_module)
        assert "52.3" not in src
        assert "2_450_000_000_000" not in src
        assert "Placeholder hourly pulses" not in src


class TestAllSourcesLive:
    def test_current_fields_live_with_correct_sources(
        self, pulse_env, monkeypatch
    ) -> None:
        _stub_providers(monkeypatch, altme=None, cnn=None, coingecko=None)
        client = TestClient(_make_app())
        data = _get(client)

        current = data["current"]
        fields = current["fields"]
        assert fields["btc_dominance"]["data_status"] == "live"
        assert fields["btc_dominance"]["source"] == "coingecko_global"
        assert fields["btc_dominance"]["value"] == pytest.approx(58.2)
        assert fields["fear_greed_index"]["data_status"] == "live"
        assert fields["fear_greed_index"]["source"] == "altme_fng"
        assert fields["fear_greed_index"]["value"] == pytest.approx(71)
        assert fields["market_cap"]["data_status"] == "live"
        assert current["data_status"] == "live"
        assert current["btc_dominance"] == pytest.approx(58.2)
        assert current["fear_greed_index"] == pytest.approx(71)
        # Complete live snapshot -> enters history series -> not accumulating.
        assert data["accumulating"] is False
        assert len(data["pulses"]) == 1


class TestDegradationChain:
    def test_altme_failure_switches_to_cnn(self, pulse_env, monkeypatch) -> None:
        _stub_providers(
            monkeypatch,
            altme=RuntimeError("alt.me unreachable"),
            cnn=None,
            coingecko=None,
        )
        client = TestClient(_make_app())
        data = _get(client)

        fg = data["current"]["fields"]["fear_greed_index"]
        assert fg["source"] == "cnn_fng"
        assert fg["data_status"] == "live"
        assert fg["value"] == pytest.approx(63)
        assert data["current"]["data_status"] == "live"
        # CNN-fed snapshot is still a complete real snapshot -> saved.
        assert data["accumulating"] is False

    def test_all_sources_down_returns_200_with_unavailable(
        self, pulse_env, monkeypatch
    ) -> None:
        _stub_providers(
            monkeypatch,
            altme=RuntimeError("down"),
            cnn=RuntimeError("down"),
            coingecko=RuntimeError("down"),
        )
        client = TestClient(_make_app())
        resp = client.get("/api/v1/intelligence/market-pulse/history")
        assert resp.status_code == 200

        data = resp.json()["data"]
        for field in ("btc_dominance", "fear_greed_index", "market_cap"):
            f = data["current"]["fields"][field]
            assert f["data_status"] == "unavailable"
            assert f["value"] is None
            assert f.get("reason")
        assert data["current"]["data_status"] == "unavailable"
        assert data["pulses"] == []
        assert data["accumulating"] is True


class TestSnapshotStore:
    def test_preset_snapshots_returned_oldest_to_newest(
        self, pulse_env, monkeypatch
    ) -> None:
        store: MarketPulseStore = pulse_env["store"]
        now = int(time.time())
        payloads = [
            {
                "btc_dominance": 50.0,
                "fear_greed_index": 60,
                "market_cap": 1.0e12,
                "source": "test",
                "fetched_at": 1,
            },
            {
                "btc_dominance": 51.0,
                "fear_greed_index": 61,
                "market_cap": 1.1e12,
                "source": "test",
                "fetched_at": 2,
            },
            {
                "btc_dominance": 52.0,
                "fear_greed_index": 62,
                "market_cap": 1.2e12,
                "source": "test",
                "fetched_at": 3,
            },
        ]
        conn = sqlite3.connect(store._db_path)
        for i, payload in enumerate(payloads):
            hour_ts = now - (3 - i) * 3600
            hour_ts -= hour_ts % 3600
            import json

            conn.execute(
                "INSERT OR REPLACE INTO market_pulse_hourly (hour_ts, payload) VALUES (?, ?)",
                (hour_ts, json.dumps(payload)),
            )
        conn.commit()
        conn.close()

        _stub_providers(
            monkeypatch,
            altme=RuntimeError("down"),
            cnn=RuntimeError("down"),
            coingecko=RuntimeError("down"),
        )
        client = TestClient(_make_app())
        data = _get(client)

        pulses = data["pulses"]
        assert len(pulses) == 3
        ts_list = [p["timestamp"] for p in pulses]
        assert ts_list == sorted(ts_list)
        assert pulses[0]["btc_dominance"] == 50.0
        assert pulses[-1]["btc_dominance"] == 52.0
        assert data["accumulating"] is False

    def test_same_hour_overwrite_idempotent(self, tmp_path) -> None:
        store = MarketPulseStore(tmp_path / "pulse.db")
        store.save_snapshot(
            {
                "btc_dominance": 51.0,
                "fear_greed_index": 40,
                "market_cap": 9.9e11,
                "source": "t",
                "fetched_at": 1,
            }
        )
        store.save_snapshot(
            {
                "btc_dominance": 52.0,
                "fear_greed_index": 41,
                "market_cap": 9.95e11,
                "source": "t",
                "fetched_at": 2,
            }
        )
        assert not store.is_empty()
        rows = store.load_recent(24)
        assert len(rows) == 1
        assert rows[0]["btc_dominance"] == 52.0

    def test_incomplete_snapshot_not_saved(self, pulse_env, monkeypatch) -> None:
        _stub_providers(
            monkeypatch, altme=None, cnn=None, coingecko=RuntimeError("down")
        )
        client = TestClient(_make_app())
        data = _get(client)

        assert data["current"]["fields"]["fear_greed_index"]["data_status"] == "live"
        assert (
            data["current"]["fields"]["btc_dominance"]["data_status"] == "unavailable"
        )
        assert data["current"]["data_status"] == "stale"
        # Partial data must not enter the history series.
        assert data["pulses"] == []
        assert data["accumulating"] is True

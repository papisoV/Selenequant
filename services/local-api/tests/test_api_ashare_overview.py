# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""Tests for /api/v1/ashare/{overview,recap/stream}.

The overview follows the same **fail-open** contract as the rest of the watch
surface: 200 with a ``status`` discriminator (``ok``/``empty``/``degraded``),
never a 500 for an upstream data problem. The recap is SSE and its failure
modes are all in-band ``error`` events — a client that renders prose must never
receive prose the server did not generate.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.error_handler import install_error_handlers

pytest.importorskip("selene_space_ashare")

import app.api.v1.ashare_overview as av  # noqa: E402
from app.api.v1.ashare_overview import router as overview_router  # noqa: E402


def _make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(overview_router, prefix="/api/v1")
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


def _overview(**overrides):
    payload = {
        "status": "ok",
        "reason": None,
        "as_of": "2026-10-20",
        "adjust": "raw",
        "quote_source": "snapshot",
        "coverage": {
            "quoted": 5564,
            "universe": 5574,
            "ratio": 0.998,
            "baseline_symbols": 5574,
            "baseline_as_of": "2026-10-19",
            "baseline_sessions": 120,
            "amount_available": False,
        },
        "indices": [
            {
                "symbol": "000001.SH",
                "name": "上证指数",
                "last_price": 3333.0,
                "change_pct": 1.0,
            }
        ],
        "indices_reason": None,
        "breadth": {
            "total": 5564,
            "up": 3285,
            "down": 2141,
            "flat": 138,
            "up_pct": 59.0,
            "down_pct": 38.5,
            "avg_pct": 0.7,
            "median_pct": 0.4,
            "strong_up": 210,
            "strong_down": 120,
        },
        "amount": {"total": None, "available": False},
        "boards": [],
        "limit": {
            "limit_up": 71,
            "broken": 68,
            "limit_down": 22,
            "max_boards": 11,
            "seal_rate": 51.0,
            "tiers": [],
            "sealed_ready": False,
            "sealed_reason": "not_in_continuous_trading",
            "fake_up": 0,
            "fake_down": 0,
        },
        "distribution": [],
        "trend": {
            "above_ma5": 3395,
            "above_ma20": 2448,
            "above_ma60": 1669,
            "above_ma5_pct": 61.0,
            "above_ma20_pct": 44.0,
            "above_ma60_pct": 30.0,
            "new_high": 40,
            "new_low": 60,
        },
        "activity": {
            "avg_vol_ratio": 1.1,
            "high_vol_count": 445,
            "high_vol_pct": 8.0,
            "vol_ratio_coverage": 0.98,
        },
        "radar": [],
        "emotion": {"score": 54, "label": "震荡"},
        "top_gainers": [],
        "top_losers": [],
        "amount_leaders": [],
        "vol_ratio_leaders": [],
        "concept_rank": {"leading": [], "lagging": []},
        "industry_rank": {"leading": [], "lagging": []},
        "snapshot_reason": None,
        "rank_reason": None,
        "requested_date": None,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# /overview — fail-open three-state
# ---------------------------------------------------------------------------


def test_overview_ok_is_200_with_summary(client, monkeypatch):
    monkeypatch.setattr(av, "build_overview", lambda date="": _overview())

    response = client.get("/api/v1/ashare/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    data = body["data"]
    assert data["status"] == "ok"
    assert data["summary"].startswith("上+1.00%")
    assert "情绪54(震荡)" in data["summary"]


def test_overview_empty_is_still_200(client, monkeypatch):
    monkeypatch.setattr(
        av,
        "build_overview",
        lambda date="": _overview(
            status="empty",
            reason="no_quotes",
            breadth={},
            emotion={"score": None, "label": "无数据"},
        ),
    )

    response = client.get("/api/v1/ashare/overview")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "empty"
    # No cross-section, no temperature — and the summary says so rather than
    # printing "None".
    assert data["emotion"]["score"] is None
    assert "情绪不可用" in data["summary"]


def test_overview_degraded_has_no_summary(client, monkeypatch):
    monkeypatch.setattr(
        av,
        "build_overview",
        lambda date="": {"status": "degraded", "reason": "no_session", "as_of": None},
    )

    response = client.get("/api/v1/ashare/overview")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "degraded"
    assert "summary" not in data


def test_overview_assembly_exception_is_a_degraded_200(client, monkeypatch):
    def boom(date=""):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(av, "build_overview", boom)

    response = client.get("/api/v1/ashare/overview")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "degraded"
    assert "kaboom" in data["reason"]


def test_overview_passes_the_date_through(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        av,
        "build_overview",
        lambda date="": seen.update(date=date) or _overview(as_of=date or "2026-10-20"),
    )

    client.get("/api/v1/ashare/overview?date=2026-10-16")

    assert seen["date"] == "2026-10-16"


# ---------------------------------------------------------------------------
# /recap/stream — SSE
# ---------------------------------------------------------------------------


def _frames(text: str) -> list[dict]:
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block.startswith("data:"):
            continue
        events.append(json.loads(block[len("data:") :].strip()))
    return events


class _FakeProvider:
    def __init__(self, chunks):
        self._chunks = chunks

    async def chat_stream(self, messages, **kwargs):
        for chunk in self._chunks:
            yield chunk


def _patch_llm(monkeypatch, provider):
    """Patch the lazily-imported provider builder.

    ``build_provider`` is imported inside the generator, so the module
    attribute is what must be replaced.
    """
    import app.core.llm_helper as helper

    async def fake_build(settings):
        return provider

    monkeypatch.setattr(helper, "build_provider", fake_build)


@pytest.fixture(autouse=True)
def _no_lhb(monkeypatch):
    """The LHB pull is a live network call; it is optional by design."""
    from selene_space_ashare import lhb_daily

    monkeypatch.setattr(lhb_daily, "fetch_lhb_day", lambda day: ([], "stubbed"))


def test_recap_without_an_llm_config_is_an_in_band_error(client, monkeypatch):
    monkeypatch.setattr(av, "build_overview", lambda date="": _overview())
    _patch_llm(monkeypatch, None)

    response = client.get("/api/v1/ashare/recap/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _frames(response.text)
    assert events[0]["type"] == "meta"
    assert events[-1]["type"] == "error"
    assert events[-1]["reason"] == "llm_not_configured"
    assert not [e for e in events if e["type"] == "done"]


def test_recap_without_a_session_is_an_error_not_prose(client, monkeypatch):
    monkeypatch.setattr(
        av,
        "build_overview",
        lambda date="": {"status": "degraded", "reason": "no_session", "as_of": None},
    )

    response = client.get("/api/v1/ashare/recap/stream")

    events = _frames(response.text)
    assert events[0]["type"] == "error"
    assert events[0]["reason"] == "no_session"


def test_recap_streams_meta_then_deltas_then_done(client, monkeypatch):
    monkeypatch.setattr(av, "build_overview", lambda date="": _overview())
    _patch_llm(monkeypatch, _FakeProvider(["## 1. 定调\n", "放量普涨。"]))

    response = client.get("/api/v1/ashare/recap/stream")

    events = _frames(response.text)
    assert events[0]["type"] == "meta"
    assert events[0]["as_of"] == "2026-10-20"
    assert events[0]["emotion_score"] == 54
    assert [e["type"] for e in events[1:]] == ["delta", "delta", "delta", "done"]
    text = "".join(e.get("content", "") for e in events if e["type"] == "delta")
    assert "放量普涨" in text
    # The model omitted section 8; the service appended the disclaimer.
    assert "不构成任何投资建议" in text


def test_recap_does_not_duplicate_a_disclaimer_the_model_wrote(client, monkeypatch):
    from selene_space_ashare.market_recap_prompt import DISCLAIMER

    monkeypatch.setattr(av, "build_overview", lambda date="": _overview())
    _patch_llm(monkeypatch, _FakeProvider([f"报告正文\n\n{DISCLAIMER}"]))

    response = client.get("/api/v1/ashare/recap/stream")

    events = _frames(response.text)
    text = "".join(e.get("content", "") for e in events if e["type"] == "delta")
    assert text.count("不构成任何投资建议") == 1


def test_recap_empty_completion_is_an_error_not_an_empty_report(client, monkeypatch):
    monkeypatch.setattr(av, "build_overview", lambda date="": _overview())
    _patch_llm(monkeypatch, _FakeProvider([]))

    response = client.get("/api/v1/ashare/recap/stream")

    events = _frames(response.text)
    assert events[-1]["type"] == "error"
    assert not [e for e in events if e["type"] == "done"]


def test_recap_llm_failure_surfaces_as_an_error_event(client, monkeypatch):
    class _Broken:
        async def chat_stream(self, messages, **kwargs):
            raise RuntimeError("upstream 529")
            yield ""  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(av, "build_overview", lambda date="": _overview())
    _patch_llm(monkeypatch, _Broken())

    response = client.get("/api/v1/ashare/recap/stream")

    events = _frames(response.text)
    assert events[-1]["type"] == "error"
    assert "529" in events[-1]["message"]


def test_sse_frame_format_is_data_prefixed_and_utf8(client, monkeypatch):
    monkeypatch.setattr(av, "build_overview", lambda date="": _overview())
    _patch_llm(monkeypatch, _FakeProvider(["中文"]))

    response = client.get("/api/v1/ashare/recap/stream")

    raw = response.text
    assert raw.startswith("data: ")
    assert raw.endswith("\n\n")
    # ensure_ascii=False: the frame carries readable Chinese, not \uXXXX.
    assert "情绪54(震荡)" in raw


# ---------------------------------------------------------------------------
# _baseline_for — the cache must not hand one db's baseline to another
# ---------------------------------------------------------------------------


def test_baseline_cache_never_serves_a_recycled_id(monkeypatch):
    """A bare ``id()`` key is not an identity guarantee; a retained db is."""
    import selene_space_ashare.market_overview as mo

    calls: list[object] = []

    class _Baseline:
        def __init__(self, owner):
            self.owner = owner

    def fake_compute(db, *, today):  # noqa: ANN001
        calls.append(db)
        return _Baseline(db)

    monkeypatch.setattr(mo, "compute_baseline", fake_compute)
    monkeypatch.setattr(av, "_baseline_cache", {})

    class FakeDB:
        pass

    first = FakeDB()
    av._baseline_for(first, "2026-10-20")

    # Simulate address recycling: a *different* object deliberately placed at the
    # same id. Without the retained reference + ``is`` check this would hand back
    # the first db's baseline for a db that never had one computed.
    second = FakeDB()
    recycled = av._baseline_cache.pop((id(first), "2026-10-20"))
    av._baseline_cache[(id(second), "2026-10-20")] = (first, recycled[1])

    baseline = av._baseline_for(second, "2026-10-20")

    assert baseline.owner is second
    assert calls == [first, second]


def test_baseline_cache_reuses_a_live_entry(monkeypatch):
    """The cache must actually cache — one compute per (db, session)."""
    import selene_space_ashare.market_overview as mo

    calls: list[object] = []

    def fake_compute(db, *, today):  # noqa: ANN001
        calls.append(db)
        return object()

    monkeypatch.setattr(mo, "compute_baseline", fake_compute)
    monkeypatch.setattr(av, "_baseline_cache", {})

    db = object()
    first = av._baseline_for(db, "2026-10-20")
    second = av._baseline_for(db, "2026-10-20")

    assert first is second
    assert calls == [db]

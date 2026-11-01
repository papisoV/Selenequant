"""Tests for /api/v1/ashare/concept-rps (概念 RPS).

Same fail-open contract as ``/rotation`` — 200 + a ``status`` discriminator,
never a 5xx for an upstream or data problem — but three things are specific to
this endpoint and are what these tests pin:

* **``lookback`` is bounded by FastAPI *and* the bound matches the module
  clamp.** The response echoes the requested lookback on the degraded paths, so
  an API bound wider than the clamp would publish a window length the module
  never produced.
* **``is_strong`` is passed through untouched.** The endpoint must not
  re-derive it: the rule is ``rps >= 90 AND stability == solid`` and the second
  half lives in the module. A stub returning ``is_strong=False`` on a
  ``rps=99`` row must survive to the client verbatim.
* **``drift_note`` is present on every path**, including degraded ones. It is
  the caveat that makes the ranking readable; dropping it on an error path
  would leave a page that renders scores with no explanation of why a
  three-name concept's rank is not actionable.
"""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.error_handler import install_error_handlers

pytest.importorskip("selene_space_ashare")

from app.api.v1.ashare_watch import router as ashare_watch_router  # noqa: E402
from selene_space_ashare import concept_map as cm_mod  # noqa: E402
from selene_space_ashare import concept_rps as rps_mod  # noqa: E402


def _make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(ashare_watch_router, prefix="/api/v1")
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


class _Manager:
    def __init__(self, db=None):
        self._db = db


def _membership() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["600001.SH", "600002.SH", "600003.SH"],
            "name": ["甲", "乙", "丙"],
            "concept": ["题材", "题材", "题材"],
        }
    )


def _stub_membership(
    monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame | None = None
) -> None:
    monkeypatch.setattr(
        cm_mod,
        "fetch_concept_membership",
        lambda *a, **k: frame if frame is not None else _membership(),
    )


def _stub_membership_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a, **_k):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(cm_mod, "fetch_concept_membership", _boom)


def _stub_rps(monkeypatch: pytest.MonkeyPatch, result: dict) -> None:
    monkeypatch.setattr(rps_mod, "build_concept_rps", lambda *a, **k: result)


_FAKE_DB = object()


def _use_db(monkeypatch: pytest.MonkeyPatch, db=_FAKE_DB) -> None:
    import app.api.v1.ashare_watch as watch_mod

    monkeypatch.setattr(watch_mod, "get_kline_manager", lambda: _Manager(db))


def _row(**overrides) -> dict:
    row = {
        "concept": "题材",
        "rps": 95.0,
        "ret": 12.34,
        "member_count": 30,
        "stability": "solid",
        "is_strong": True,
    }
    row.update(overrides)
    return row


def _result(rows: list[dict], **overrides) -> dict:
    base = {
        "as_of": "2026-10-16",
        "lookback": 120,
        "concepts": rows,
        "concept_count": len(rows),
        "membership_note": rps_mod.MEMBERSHIP_NOTE,
        "drift_note": rps_mod.DRIFT_NOTE,
        "reason": None,
    }
    base.update(overrides)
    return base


# -- happy path -------------------------------------------------------------


def test_concept_rps_happy_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_db(monkeypatch)
    _stub_membership(monkeypatch)
    _stub_rps(monkeypatch, _result([_row()]))

    body = client.get("/api/v1/ashare/concept-rps").json()["data"]
    assert body["status"] == "ok"
    assert body["reason"] is None
    assert body["lookback"] == 120
    assert body["as_of"] == "2026-10-16"
    assert body["concept_count"] == 1
    assert body["concepts"] == [
        {
            "concept": "题材",
            "rps": 95.0,
            "ret": 12.34,
            "member_count": 30,
            "stability": "solid",
            "is_strong": True,
        }
    ]


def test_concept_rps_default_lookback_is_120(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, int] = {}
    _use_db(monkeypatch)
    _stub_membership(monkeypatch)
    monkeypatch.setattr(
        rps_mod,
        "build_concept_rps",
        lambda *a, **k: (
            seen.update(k)
            or _result([], reason="daily_base_empty")
        ),
    )

    client.get("/api/v1/ashare/concept-rps")
    assert seen["lookback"] == 120


def test_concept_rps_passes_the_requested_lookback_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, int] = {}
    _use_db(monkeypatch)
    _stub_membership(monkeypatch)
    monkeypatch.setattr(
        rps_mod,
        "build_concept_rps",
        lambda *a, **k: seen.update(k) or _result([_row()]),
    )

    client.get("/api/v1/ashare/concept-rps", params={"lookback": "60"})
    assert seen["lookback"] == 60


def test_is_strong_is_forwarded_not_recomputed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A thin concept at rps=99 must stay ``is_strong=False`` through the API.

    The endpoint has no business re-deriving the rule; if it did (``rps >= 90``
    alone), this row would flip to true and the page would highlight a
    four-name concept.
    """
    _use_db(monkeypatch)
    _stub_membership(monkeypatch)
    _stub_rps(
        monkeypatch,
        _result([_row(concept="薄", rps=99.0, member_count=4, stability="thin", is_strong=False)]),
    )

    row = client.get("/api/v1/ashare/concept-rps").json()["data"]["concepts"][0]
    assert row["is_strong"] is False
    assert row["rps"] == 99.0
    assert row["stability"] == "thin"


def test_concept_rps_publishes_the_drift_note(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_db(monkeypatch)
    _stub_membership(monkeypatch)
    _stub_rps(monkeypatch, _result([_row()]))

    body = client.get("/api/v1/ashare/concept-rps").json()["data"]
    assert body["drift_note"] == rps_mod.DRIFT_NOTE
    assert "thin" in body["drift_note"]


# -- bounds -----------------------------------------------------------------


@pytest.mark.parametrize("lookback", ["19", "0", "-1", "251", "1000"])
def test_concept_rps_rejects_out_of_range_lookback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, lookback: str
) -> None:
    """422 before any work: the computation must not even be reached."""
    _use_db(monkeypatch)
    called: list[int] = []

    def _spy(*a: object, **k: object) -> dict:
        called.append(1)
        return {}

    monkeypatch.setattr(rps_mod, "build_concept_rps", _spy)

    assert (
        client.get(
            "/api/v1/ashare/concept-rps", params={"lookback": lookback}
        ).status_code
        == 422
    )
    assert called == []


def test_api_lookback_bounds_match_the_module_clamp() -> None:
    """FastAPI's ge/le and the module clamp must not drift.

    The endpoint echoes ``lookback`` on every path, including degraded ones, so
    a wider API bound would publish a window the module silently clamped (ask
    300, get 250, body says 300).
    """
    import app.api.v1.ashare_watch as watch_mod

    assert (
        watch_mod._CONCEPT_RPS_LOOKBACK_MIN,
        watch_mod._CONCEPT_RPS_LOOKBACK_MAX,
    ) == (rps_mod.MIN_LOOKBACK, rps_mod.MAX_LOOKBACK)


# -- degradation ------------------------------------------------------------


def test_concept_rps_without_kline_manager_is_degraded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.v1.ashare_watch as watch_mod

    monkeypatch.setattr(watch_mod, "get_kline_manager", lambda: None)

    body = client.get("/api/v1/ashare/concept-rps").json()["data"]
    assert body["status"] == "degraded"
    assert body["reason"] == "kline_manager_unavailable"
    assert body["concepts"] == []
    assert body["drift_note"], "the caveat must survive the degraded path"


def test_concept_rps_missing_membership_is_degraded_not_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable membership source is an outage, not a quiet market."""
    _use_db(monkeypatch)
    _stub_membership_raises(monkeypatch)
    _stub_rps(monkeypatch, _result([], reason="membership_empty"))

    body = client.get("/api/v1/ashare/concept-rps").json()["data"]
    assert body["status"] == "degraded"
    assert body["reason"] == "concept_membership_unavailable"


def test_concept_rps_computation_exception_is_degraded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_db(monkeypatch)
    _stub_membership(monkeypatch)

    def _boom(*_a, **_k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(rps_mod, "build_concept_rps", _boom)

    resp = client.get("/api/v1/ashare/concept-rps")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "degraded"
    assert body["reason"] == "computation_failed"


def test_concept_rps_empty_result_passes_the_module_reason_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_db(monkeypatch)
    _stub_membership(monkeypatch)
    _stub_rps(monkeypatch, _result([], reason="insufficient_sessions(37)"))

    body = client.get("/api/v1/ashare/concept-rps").json()["data"]
    assert body["status"] == "empty"
    assert body["reason"] == "insufficient_sessions(37)"


def test_concept_rps_distinguishes_the_three_degradation_reasons(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The three reasons must not collapse into one string.

    Collapsing them would leave an operator unable to tell "no database" from
    "the membership upstream is dead" from "the code threw" — three different
    fixes behind one label.
    """
    import app.api.v1.ashare_watch as watch_mod

    seen: set[str] = set()

    monkeypatch.setattr(watch_mod, "get_kline_manager", lambda: None)
    seen.add(client.get("/api/v1/ashare/concept-rps").json()["data"]["reason"])

    monkeypatch.setattr(watch_mod, "get_kline_manager", lambda: _Manager(_FAKE_DB))
    _stub_membership_raises(monkeypatch)
    _stub_rps(monkeypatch, _result([], reason="membership_empty"))
    seen.add(client.get("/api/v1/ashare/concept-rps").json()["data"]["reason"])

    _stub_membership(monkeypatch)
    monkeypatch.setattr(
        rps_mod, "build_concept_rps", lambda *a, **k: (_ for _ in ()).throw(RuntimeError())
    )
    seen.add(client.get("/api/v1/ashare/concept-rps").json()["data"]["reason"])

    assert seen == {
        "kline_manager_unavailable",
        "concept_membership_unavailable",
        "computation_failed",
    }

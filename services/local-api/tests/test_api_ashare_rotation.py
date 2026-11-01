"""Tests for /api/v1/ashare/{rotation,mainline} (概念轮动 / 主线识别).

Same fail-open contract as the rest of ``ashare_watch``: 200 + a ``status``
discriminator, never a 5xx for an upstream problem. What is specific to these
two endpoints and therefore what these tests pin:

* **``days`` is bounded by FastAPI, not only by the module.** An out-of-range
  value must 422 before any DB or network work happens.
* **A missing membership source is ``degraded``, not ``empty``.** The two say
  different things — "we cannot group anything" vs "nothing scored" — and
  collapsing them hides an upstream outage behind an ordinary-looking page.
* **``streak_capped`` is always present on mainline**, including on the
  degraded paths, because it describes the daily base rather than this request.
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
from selene_space_ashare import concept_rotation as cr_mod  # noqa: E402
from selene_space_ashare import mainline as ml_mod  # noqa: E402


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
            "symbol": ["600001.SH", "600002.SH"],
            "name": ["甲", "乙"],
            "concept": ["题材", "题材"],
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


def _stub_rotation(monkeypatch: pytest.MonkeyPatch, result: dict) -> None:
    monkeypatch.setattr(cr_mod, "build_rotation_matrix", lambda *a, **k: result)


def _stub_mainline(monkeypatch: pytest.MonkeyPatch, result: dict) -> None:
    monkeypatch.setattr(ml_mod, "compute_mainline", lambda *a, **k: result)


_FAKE_DB = object()


def _use_db(monkeypatch: pytest.MonkeyPatch, db=_FAKE_DB) -> None:
    """Present a kline manager whose database is a placeholder.

    The DB is never touched: every test that gets this far stubs the
    computation itself. What matters is that ``_daily_db`` finds *something*,
    so the early ``kline_manager_unavailable`` return is not what is under test.
    """
    import app.api.v1.ashare_watch as watch_mod

    monkeypatch.setattr(watch_mod, "get_kline_manager", lambda: _Manager(db))


# -- rotation ---------------------------------------------------------------


def test_rotation_happy_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_db(monkeypatch)
    _stub_membership(monkeypatch)
    _stub_rotation(
        monkeypatch,
        {
            "dates": ["2026-10-16"],
            "columns": {"2026-10-16": [["题材", 5.22]]},
            "concept_count": 1,
            "as_of": "2026-10-16",
            "reason": None,
        },
    )

    body = client.get("/api/v1/ashare/rotation", params={"days": "12"}).json()["data"]
    assert body["status"] == "ok"
    assert body["reason"] is None
    assert body["days"] == 12
    assert body["dates"] == ["2026-10-16"]
    assert body["columns"]["2026-10-16"] == [["题材", 5.22]]
    assert body["concept_count"] == 1
    assert body["membership_note"], "the snapshot caveat must always be published"


def test_rotation_default_window_is_twelve_days(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, int] = {}
    _use_db(monkeypatch)
    _stub_membership(monkeypatch)
    monkeypatch.setattr(
        cr_mod,
        "build_rotation_matrix",
        lambda *a, **k: (
            seen.update(k)
            or {
                "dates": [],
                "columns": {},
                "concept_count": 0,
                "as_of": None,
                "reason": "daily_base_empty",
            }
        ),
    )

    client.get("/api/v1/ashare/rotation")
    assert seen["days"] == 12


@pytest.mark.parametrize("days", ["0", "6", "31", "-1"])
def test_rotation_rejects_out_of_range_days(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, days: str
) -> None:
    """422 before any work: the computation must not even be reached."""
    _use_db(monkeypatch)
    called: list[int] = []

    def _spy(*a: object, **k: object) -> dict[str, object]:
        called.append(1)
        return {}

    monkeypatch.setattr(cr_mod, "build_rotation_matrix", _spy)

    assert (
        client.get("/api/v1/ashare/rotation", params={"days": days}).status_code == 422
    )
    assert called == []


def test_rotation_without_kline_manager_is_degraded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.v1.ashare_watch as watch_mod

    monkeypatch.setattr(watch_mod, "get_kline_manager", lambda: None)

    body = client.get("/api/v1/ashare/rotation").json()["data"]
    assert body["status"] == "degraded"
    assert body["reason"] == "kline_manager_unavailable"
    assert body["dates"] == []


def test_rotation_missing_membership_is_degraded_not_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable membership source is an outage, not a quiet market.

    Reporting ``empty`` here would make a dead upstream look like a day with
    no movers — the exact failure this endpoint exists to make visible.
    """
    _use_db(monkeypatch)
    _stub_membership_raises(monkeypatch)
    _stub_rotation(
        monkeypatch,
        {
            "dates": [],
            "columns": {},
            "concept_count": 0,
            "as_of": None,
            "reason": "membership_empty",
        },
    )

    body = client.get("/api/v1/ashare/rotation").json()["data"]
    assert body["status"] == "degraded"
    assert body["reason"] == "concept_membership_unavailable"


def test_rotation_computation_exception_is_degraded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_db(monkeypatch)
    _stub_membership(monkeypatch)

    def _boom(*_a, **_k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cr_mod, "build_rotation_matrix", _boom)

    resp = client.get("/api/v1/ashare/rotation")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "degraded"
    assert body["reason"] == "computation_failed"


def test_rotation_empty_result_passes_the_module_reason_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_db(monkeypatch)
    _stub_membership(monkeypatch)
    _stub_rotation(
        monkeypatch,
        {
            "dates": [],
            "columns": {},
            "concept_count": 0,
            "as_of": None,
            "reason": "insufficient_sessions(1)",
        },
    )

    body = client.get("/api/v1/ashare/rotation").json()["data"]
    assert body["status"] == "empty"
    assert body["reason"] == "insufficient_sessions(1)"


def test_api_day_bounds_match_the_module_clamp() -> None:
    """FastAPI's ge/le and the module clamp must not drift.

    The response echoes the *requested* ``days``, not the clamped one, so an API
    bound wider than the clamp would publish a window length the matrix never
    produced (ask 30, get 20, body says 30). The four constants are duplicated
    on purpose — the endpoint file imports the package lazily — and this is what
    keeps the duplication honest.
    """
    import app.api.v1.ashare_watch as watch_mod

    assert (watch_mod._ROTATION_DAYS_MIN, watch_mod._ROTATION_DAYS_MAX) == (
        cr_mod.MIN_DAYS,
        cr_mod.MAX_DAYS,
    )
    assert (watch_mod._MAINLINE_DAYS_MIN, watch_mod._MAINLINE_DAYS_MAX) == (
        cr_mod.MIN_DAYS,
        cr_mod.MAX_DAYS,
    )


# -- mainline ---------------------------------------------------------------


def test_mainline_happy_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_db(monkeypatch)
    _stub_membership(monkeypatch)
    _stub_mainline(
        monkeypatch,
        {
            "dates": ["2026-10-16"],
            "columns": {"2026-10-16": [["题材", 100.0, 5, 3, "600001.SH"]]},
            "as_of": "2026-10-16",
            "streak_capped": True,
            "membership_note": ml_mod.MEMBERSHIP_NOTE,
            "reason": None,
        },
    )

    body = client.get("/api/v1/ashare/mainline").json()["data"]
    assert body["status"] == "ok"
    assert body["days"] == 20
    assert body["columns"]["2026-10-16"] == [["题材", 100.0, 5, 3, "600001.SH"]]
    assert body["streak_capped"] is True


def test_mainline_streak_capped_is_reported_even_when_degraded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``streak_capped`` describes the daily base, not this request, so it must
    survive every early return — including the two degraded ones."""
    import app.api.v1.ashare_watch as watch_mod

    monkeypatch.setattr(watch_mod, "get_kline_manager", lambda: None)
    body = client.get("/api/v1/ashare/mainline").json()["data"]
    assert body["status"] == "degraded"
    assert body["streak_capped"] is True

    _use_db(monkeypatch)
    _stub_membership_raises(monkeypatch)
    _stub_mainline(
        monkeypatch,
        {
            "dates": [],
            "columns": {},
            "as_of": None,
            "streak_capped": True,
            "membership_note": ml_mod.MEMBERSHIP_NOTE,
            "reason": "membership_empty",
        },
    )
    body = client.get("/api/v1/ashare/mainline").json()["data"]
    assert body["status"] == "degraded"
    assert body["streak_capped"] is True


@pytest.mark.parametrize("days", ["6", "31"])
def test_mainline_rejects_out_of_range_days(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, days: str
) -> None:
    _use_db(monkeypatch)
    called: list[int] = []

    def _spy(*a: object, **k: object) -> dict[str, object]:
        called.append(1)
        return {}

    monkeypatch.setattr(ml_mod, "compute_mainline", _spy)

    assert (
        client.get("/api/v1/ashare/mainline", params={"days": days}).status_code == 422
    )
    assert called == []


def test_mainline_exclude_st_flag_reaches_the_module(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}
    _use_db(monkeypatch)
    _stub_membership(monkeypatch)
    monkeypatch.setattr(
        ml_mod,
        "compute_mainline",
        lambda *a, **k: (
            seen.update(k)
            or {
                "dates": [],
                "columns": {},
                "as_of": None,
                "streak_capped": True,
                "membership_note": ml_mod.MEMBERSHIP_NOTE,
                "reason": "daily_base_empty",
            }
        ),
    )

    client.get("/api/v1/ashare/mainline", params={"exclude_st": "false"})
    assert seen["exclude_st_names"] is False


def test_mainline_computation_exception_is_degraded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_db(monkeypatch)
    _stub_membership(monkeypatch)

    def _boom(*_a, **_k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(ml_mod, "compute_mainline", _boom)

    resp = client.get("/api/v1/ashare/mainline")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "degraded"
    assert body["reason"] == "computation_failed"
    assert body["streak_capped"] is True


def test_mainline_missing_membership_is_degraded_not_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_db(monkeypatch)
    _stub_membership(
        monkeypatch, frame=pd.DataFrame(columns=["symbol", "name", "concept"])
    )
    _stub_mainline(
        monkeypatch,
        {
            "dates": [],
            "columns": {},
            "as_of": None,
            "streak_capped": True,
            "membership_note": ml_mod.MEMBERSHIP_NOTE,
            "reason": "membership_empty",
        },
    )

    body = client.get("/api/v1/ashare/mainline").json()["data"]
    assert body["status"] == "degraded"
    assert body["reason"] == "concept_membership_empty"

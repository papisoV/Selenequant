"""Tests for /api/v1/ashare/{limit-up-pool,ladder,phase,boards} (看盘台).

These endpoints follow a **fail-open** contract — opposite of
``ashare_deep`` (empty->404, failure->502): each returns 200 with a
``status`` discriminator (``ok``/``empty``/``degraded``) and never raises
for upstream data problems. Genuine request errors (bad ``kind``) still 422.

Fetchers are imported lazily inside each handler, so monkeypatching the
source module attribute is picked up at call time.
"""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.error_handler import install_error_handlers

pytest.importorskip("selene_space_ashare")

from app.api.v1.ashare_watch import router as ashare_watch_router  # noqa: E402
from selene_space_ashare import data_fetcher as df_mod  # noqa: E402
from selene_space_ashare import market_phase as mp_mod  # noqa: E402
from selene_space_ashare import ths_data as ths_mod  # noqa: E402


def _make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(ashare_watch_router, prefix="/api/v1")
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


def _no_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the panel at a path that does not exist (fresh checkout)."""
    monkeypatch.setattr(
        mp_mod, "PANEL_PATH", mp_mod.PANEL_PATH.parent / "_nope.parquet"
    )


def _no_live_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the live ladder history yield nothing (no endpoint walk in tests).

    The live history is a ~57s cold endpoint walk; every endpoint test that is
    not specifically about it must stub it out, or the suite would hit the
    network and take a minute per case.
    """
    import app.api.v1.ashare_watch as watch_mod
    from selene_space_ashare import ladder_live as ll_mod

    monkeypatch.setattr(ll_mod, "live_ladder_history", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(
        watch_mod, "_live_ladder_history", lambda *a, **k: ([], "stubbed")
    )


def _stub_snapshot_source(monkeypatch: pytest.MonkeyPatch, rows: dict) -> None:
    """Register a fake snapshot source on the market service (live phase path)."""
    import app.api.v1.ashare_watch as watch_mod

    class _Src:
        def get_market_rows(self):
            return rows

    class _Svc:
        def get_source(self, exchange, market_type):
            return _Src()

    monkeypatch.setattr(watch_mod, "get_market_service", lambda: _Svc())


# ---------------------------------------------------------------------------
# /limit-up-pool
# ---------------------------------------------------------------------------


def test_limit_up_pool_populated_returns_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = pd.DataFrame(
        {
            "code": ["600519", "000001"],
            "name": ["贵州茅台", "平安银行"],
            "change_rate": [10.0, 9.98],
        }
    )
    stats = {
        "limit_up_today": 42,
        "seal_rate": 0.81,
        "open_count": 7,
        "limit_down_today": 3,
    }
    monkeypatch.setattr(ths_mod, "fetch_limit_up_pool_full", lambda *a, **k: pool)
    monkeypatch.setattr(ths_mod, "fetch_limit_up_stats", lambda *a, **k: stats)

    resp = client.get("/api/v1/ashare/limit-up-pool")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "ok"
    assert body["pool_count"] == 2
    assert body["pool"][0]["name"] == "贵州茅台"
    assert body["stats"]["limit_up_today"] == 42


def test_limit_up_pool_failure_degrades_to_empty_not_5xx(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead upstream must not blank the page — 200/empty, never 5xx."""

    def _boom(*a, **k):
        raise RuntimeError("10jqka unreachable")

    monkeypatch.setattr(ths_mod, "fetch_limit_up_pool_full", _boom)
    monkeypatch.setattr(ths_mod, "fetch_limit_up_stats", _boom)

    resp = client.get("/api/v1/ashare/limit-up-pool")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "empty"
    assert body["pool"] == []
    assert body["stats"] == {}


# ---------------------------------------------------------------------------
# /ladder
# ---------------------------------------------------------------------------


def test_ladder_today_from_pool_and_continuous(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """今日行由 pool + continuous 之差合成 —— 首板层来自 pool。

    只用 continuous 会漏掉整个首板层(其 first_board 恒为 0),所以这里两个
    端点都给,且断言 first_board 来自 pool 的差额。
    """
    # pool: 4 只涨停;continuous 只列其中 2 只(3 板 / 2 板),差集 2 只首板。
    pool = pd.DataFrame(
        {
            "code": ["600001", "600002", "600003", "600004"],
            "name": ["甲", "乙", "丙", "丁"],
            "date": ["20241101"] * 4,
        }
    )
    cont = pd.DataFrame(
        {
            "code": ["600001", "600002"],
            "consecutive_boards": [3, 2],
            "date": ["20241101", "20241101"],
        }
    )
    monkeypatch.setattr(ths_mod, "fetch_continuous_limit_up", lambda *a, **k: cont)
    monkeypatch.setattr(ths_mod, "fetch_limit_up_pool_full", lambda *a, **k: pool)
    _no_panel(monkeypatch)
    _no_live_history(monkeypatch)

    resp = client.get("/api/v1/ashare/ladder")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "ok"
    assert body["date"] == "20241101"
    assert body["today"]["max_consecutive"] == 3
    assert body["today"]["ge2_count"] == 2  # 600001, 600002
    assert body["today"]["first_board"] == 2  # 600003, 600004
    assert body["history"] == []


def test_ladder_one_sided_fetch_stays_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺任一端点都不出今日行 —— 单端点会产出假梯队,不是"空盘"。"""
    cont = pd.DataFrame(
        {"code": ["600001"], "consecutive_boards": [4], "date": ["20241101"]}
    )
    monkeypatch.setattr(ths_mod, "fetch_continuous_limit_up", lambda *a, **k: cont)

    def _boom(*a, **k):
        raise RuntimeError("pool down")

    monkeypatch.setattr(ths_mod, "fetch_limit_up_pool_full", _boom)
    _no_panel(monkeypatch)
    _no_live_history(monkeypatch)

    resp = client.get("/api/v1/ashare/ladder")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["today"] is None
    assert body["status"] == "empty"


def test_ladder_failure_degrades_to_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*a, **k):
        raise RuntimeError("10jqka down")

    monkeypatch.setattr(ths_mod, "fetch_continuous_limit_up", _boom)
    monkeypatch.setattr(ths_mod, "fetch_limit_up_pool_full", _boom)
    _no_panel(monkeypatch)
    _no_live_history(monkeypatch)

    resp = client.get("/api/v1/ashare/ladder")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "empty"


def test_ladder_asks_the_fetchers_in_strict_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint's own "both endpoints required" guard depends on strict.

    The fetchers swallow a transport failure into an empty frame by default, so
    a lenient call would return ``ok=True`` with an empty frame for a dead
    endpoint and the ``if ok_cont and ok_pool`` guard would never fire — the
    endpoint would report an authoritative empty tape for a one-sided fetch,
    the exact thing its own comment says it guards against. This fails if
    ``strict=True`` is dropped from either call.
    """
    calls: list[dict] = []

    def _records(*a, **k):
        calls.append(k)
        return pd.DataFrame()

    monkeypatch.setattr(ths_mod, "fetch_continuous_limit_up", _records)
    monkeypatch.setattr(ths_mod, "fetch_limit_up_pool_full", _records)
    _no_panel(monkeypatch)
    _no_live_history(monkeypatch)

    client.get("/api/v1/ashare/ladder")
    assert [c.get("strict") for c in calls] == [True, True]
    # Both fetchers must be pinned to the same session, and both must bypass
    # the date-keyed caches (which have no intra-day age check), or this "今日"
    # row freezes at the first poll of the day and can mix two sessions.
    assert len({c.get("date") for c in calls}) == 1
    assert all(c.get("date") for c in calls)
    assert [c.get("force") for c in calls] == [True, True]


# ---------------------------------------------------------------------------
# /phase
# ---------------------------------------------------------------------------


def test_phase_missing_panel_reports_degraded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_panel(monkeypatch)

    resp = client.get("/api/v1/ashare/phase")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "degraded"
    assert body["reason"] == "panel_missing"
    assert body["current"] is None


def test_phase_labels_panel_last_day_as_as_of(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """``as_of`` must be the panel's last day, NOT wall-clock today."""
    panel = pd.DataFrame(
        {
            "date": ["20241030", "20241031"],
            "max_consecutive": [5, 6],
            "first_board": [10, 12],
            "ge2_count": [4, 5],
            "promo_rate": [0.3, 0.35],
            "seal_rate": [0.7, 0.72],
        }
    )
    path = tmp_path / "ladder_panel.parquet"
    panel.to_parquet(path)
    monkeypatch.setattr(mp_mod, "PANEL_PATH", path)
    monkeypatch.setattr(mp_mod, "calibration_status", lambda *a, **k: (True, "test"))

    def _classify(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.copy()
        out["phase"] = ["rally", "climax"]
        return out

    monkeypatch.setattr(mp_mod, "classify_phase_series", _classify)

    resp = client.get("/api/v1/ashare/phase")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "ok"
    assert body["as_of"] == "20241031"  # 面板最后一日,非自然今日
    assert body["current"]["phase"] == "climax"
    assert len(body["series"]) == 2


def test_phase_classify_error_degrades(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    path = tmp_path / "ladder_panel.parquet"
    pd.DataFrame({"date": ["20241031"]}).to_parquet(path)
    monkeypatch.setattr(mp_mod, "PANEL_PATH", path)
    monkeypatch.setattr(mp_mod, "calibration_status", lambda *a, **k: (True, "test"))

    def _bad_classify(*a, **k):
        raise ValueError("missing cols")

    monkeypatch.setattr(mp_mod, "classify_phase_series", _bad_classify)

    resp = client.get("/api/v1/ashare/phase")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "degraded"
    assert body["reason"].startswith("classify_failed")


# ---------------------------------------------------------------------------
# /phase — live snapshot path
# ---------------------------------------------------------------------------


class _SnapRow:
    """Duck-typed snapshot row (the endpoints only touch these fields)."""

    def __init__(
        self,
        symbol: str,
        name: str,
        last: float,
        prev_close: float,
        high: float,
        timestamp: int,
    ):
        self.symbol = symbol
        self.name = name
        self.last = last
        self.prev_close = prev_close
        self.high = high
        self.timestamp = timestamp


def test_phase_live_snapshot_labels_last_row_provisional(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """实时档: 快照合成今日行,``label_scope`` 必须是 intraday_provisional。"""
    from selene_space_ashare import ladder_live

    called: dict[str, object] = {}

    def _fake_series(snapshot_rows, days_back=20, **k):
        called["rows"] = len(snapshot_rows)
        labeled = pd.DataFrame(
            {
                "date": ["20241014", "20241015"],
                "phase": ["ignite", "rally"],
                "max_consecutive": [3, 5],
                "ge2_count": [2, 4],
                "promo_rate": [0.3, 0.5],
                "seal_rate": [0.7, 0.8],
            }
        )
        return labeled, "intraday_provisional"

    monkeypatch.setattr(ladder_live, "live_phase_series", _fake_series)
    rows = {
        "600001.SH": _SnapRow("600001.SH", "甲", 11.0, 10.0, 11.0, 1728959735000),
    }
    _stub_snapshot_source(monkeypatch, rows)

    resp = client.get("/api/v1/ashare/phase")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "ok"
    assert body["source"] == "live_snapshot"
    assert body["label_scope"] == "intraday_provisional"
    assert body["as_of"] == "20241015"
    assert body["current"]["phase"] == "rally"
    assert called["rows"] == 1  # the snapshot rows reached the live path


def test_phase_live_source_absent_falls_back_to_panel(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """快照源未注册 → 退回回填面板,并带 ``live_reason`` 说明为何退回。"""
    panel = pd.DataFrame(
        {
            "date": ["20241031"],
            "max_consecutive": [6],
            "first_board": [12],
            "ge2_count": [5],
            "promo_rate": [0.35],
            "seal_rate": [0.72],
        }
    )
    path = tmp_path / "ladder_panel.parquet"
    panel.to_parquet(path)
    monkeypatch.setattr(mp_mod, "PANEL_PATH", path)
    monkeypatch.setattr(mp_mod, "calibration_status", lambda *a, **k: (True, "test"))
    monkeypatch.setattr(
        mp_mod,
        "classify_phase_series",
        lambda frame: frame.assign(phase=["climax"] * len(frame)),
    )
    import app.api.v1.ashare_watch as watch_mod

    monkeypatch.setattr(watch_mod, "get_market_service", lambda: None)

    resp = client.get("/api/v1/ashare/phase")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "ok"
    assert body["source"] == "backfill_panel"
    assert body["as_of"] == "20241031"
    assert body["live_reason"] == "market_service_unavailable"


def test_phase_live_false_skips_snapshot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """``live=false`` 直接走面板,不读快照。"""
    panel = pd.DataFrame(
        {
            "date": ["20241031"],
            "max_consecutive": [6],
            "first_board": [12],
            "ge2_count": [5],
            "promo_rate": [0.35],
            "seal_rate": [0.72],
        }
    )
    path = tmp_path / "ladder_panel.parquet"
    panel.to_parquet(path)
    monkeypatch.setattr(mp_mod, "PANEL_PATH", path)
    monkeypatch.setattr(mp_mod, "calibration_status", lambda *a, **k: (True, "test"))
    monkeypatch.setattr(
        mp_mod,
        "classify_phase_series",
        lambda frame: frame.assign(phase=["climax"] * len(frame)),
    )

    def _explode(*a, **k):
        raise AssertionError("live=false must not read the snapshot")

    import app.api.v1.ashare_watch as watch_mod

    monkeypatch.setattr(watch_mod, "get_market_service", _explode)

    resp = client.get("/api/v1/ashare/phase", params={"live": "false"})
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["source"] == "backfill_panel"


# ---------------------------------------------------------------------------
# /boards
# ---------------------------------------------------------------------------


def test_boards_concept_uses_concept_fetcher(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    df = pd.DataFrame(
        {
            "rank": [1],
            "concept": ["AI算力"],
            "change_pct": [3.2],
            "up_count": [40],
            "down_count": [5],
            "leader": ["中际旭创"],
        }
    )
    monkeypatch.setattr(df_mod, "get_concept_boards", lambda *a, **k: df)

    resp = client.get("/api/v1/ashare/boards", params={"kind": "concept"})
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "ok"
    assert body["kind"] == "concept"
    assert body["boards"][0]["concept"] == "AI算力"


def test_boards_industry_uses_industry_fetcher(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    df = pd.DataFrame({"rank": [1], "industry": ["半导体"], "change_pct": [-1.1]})
    monkeypatch.setattr(df_mod, "get_industry_comparison", lambda *a, **k: df)

    resp = client.get("/api/v1/ashare/boards", params={"kind": "industry"})
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["kind"] == "industry"
    assert body["boards"][0]["industry"] == "半导体"


def test_boards_bad_kind_returns_422(client: TestClient) -> None:
    resp = client.get("/api/v1/ashare/boards", params={"kind": "sector"})
    assert resp.status_code == 422


def test_boards_failure_degrades_to_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*a, **k):
        raise RuntimeError("push2 down")

    monkeypatch.setattr(df_mod, "get_concept_boards", _boom)

    resp = client.get("/api/v1/ashare/boards", params={"kind": "concept"})
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "empty"
    assert body["boards"] == []


# ---------------------------------------------------------------------------
# /deviation  (B2 — 交易所异动偏离)
# ---------------------------------------------------------------------------


def test_deviation_no_ashare_favorites_is_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.v1.ashare_watch as watch_mod

    class _Settings:
        def get_favorite_symbols(self):
            return ["BTC/USDT"]

    monkeypatch.setattr(watch_mod, "get_settings_service", lambda: _Settings())

    resp = client.get("/api/v1/ashare/deviation")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "empty"
    assert body["reason"] == "no_ashare_favorites"
    assert body["rows"] == []


def test_deviation_kline_store_missing_degrades(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.v1.ashare_watch as watch_mod

    class _Settings:
        def get_favorite_symbols(self):
            return ["600519.SH"]

    monkeypatch.setattr(watch_mod, "get_settings_service", lambda: _Settings())
    monkeypatch.setattr(watch_mod, "get_kline_manager", lambda: None)

    resp = client.get("/api/v1/ashare/deviation")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "degraded"
    assert body["reason"] == "kline_manager_unavailable"
    assert body["skipped"] == [{"symbol": "600519.SH", "reason": "no_kline_store"}]


class _FakeKlineDB:
    def __init__(self, series):
        self._series = series

    def get_klines(self, exchange, market_type, symbol, interval, *a, **k):
        if market_type != "spot" or symbol not in self._series:
            return pd.DataFrame(columns=["timestamp", "close"])
        from datetime import datetime, timedelta, timezone

        cst = timezone(timedelta(hours=8))
        rows = []
        for day, close in self._series[symbol]:
            dt = datetime.strptime(day, "%Y%m%d").replace(tzinfo=cst)
            rows.append({"timestamp": int(dt.timestamp() * 1000), "close": close})
        return pd.DataFrame(rows)


def _flat_index():
    from datetime import datetime, timedelta

    base = datetime(2026, 9, 1)
    days = [(base + timedelta(days=i)).strftime("%Y%m%d") for i in range(40)]
    return pd.DataFrame({"date": days, "close": [100.0] * 40})


def _ramp_stock(mult):
    from datetime import datetime, timedelta

    base = datetime(2026, 9, 1)
    days = [(base + timedelta(days=i)).strftime("%Y%m%d") for i in range(40)]
    return [(d, 10.0 if i < 37 else 10.0 * mult) for i, d in enumerate(days)]


def test_deviation_daily_ranks_by_closeness(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.v1.ashare_watch as watch_mod

    class _Settings:
        def get_favorite_symbols(self):
            return ["600519.SH", "300750.SZ"]

    class _Manager:
        def __init__(self):
            self._db = _FakeKlineDB(
                {"600519.SH": _ramp_stock(1.30), "300750.SZ": _ramp_stock(1.40)}
            )

    monkeypatch.setattr(watch_mod, "get_settings_service", lambda: _Settings())
    monkeypatch.setattr(watch_mod, "get_kline_manager", lambda: _Manager())
    monkeypatch.setattr(watch_mod, "get_market_service", lambda: None)
    monkeypatch.setattr(df_mod, "get_index_daily", lambda code: _flat_index())

    resp = client.get("/api/v1/ashare/deviation", params={"live": "false"})
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "ok"
    assert body["live"] is False
    # 主板 +30% => 接近度 1.5 已触发;创业板 +40%/0.30 => 1.333 也触发
    assert [r["symbol"] for r in body["rows"]] == ["600519.SH", "300750.SZ"]
    assert body["rows"][0]["status"] == "triggered"
    assert body["rows"][0]["bench"] == "000002.SH"  # TSP 首选:上证A指
    assert body["rows"][1]["bench"] == "399102.SZ"  # TSP 首选:创业板综指
    assert body["rows"][0]["intraday"] is False
    assert body["counts"]["triggered"] == 2


def test_deviation_no_daily_bars_reports_skipped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.v1.ashare_watch as watch_mod

    class _Settings:
        def get_favorite_symbols(self):
            return ["600519.SH"]

    class _Manager:
        def __init__(self):
            self._db = _FakeKlineDB({})

    monkeypatch.setattr(watch_mod, "get_settings_service", lambda: _Settings())
    monkeypatch.setattr(watch_mod, "get_kline_manager", lambda: _Manager())
    monkeypatch.setattr(watch_mod, "get_market_service", lambda: None)
    monkeypatch.setattr(df_mod, "get_index_daily", lambda code: _flat_index())

    resp = client.get("/api/v1/ashare/deviation", params={"live": "false"})
    body = resp.json()["data"]
    assert body["status"] == "empty"
    assert body["skipped"] == [{"symbol": "600519.SH", "reason": "no_daily_bars"}]

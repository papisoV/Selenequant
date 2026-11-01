"""S7 PA price alert tests: service one-shot semantics + HTTP endpoints.

Covers:
1. Trigger: price touches entry → hit record, notify called once, rule deleted.
2. No touch: price far away → no fire, rule retained.
3. WS down: get_price returns None → skip honestly (no fire, no delete).
4. Short direction: price >= stop_loss fires.
5. direction None: first round records baseline (no fire), crossing round fires.
6. HTTP: POST → GET → DELETE full chain with response shapes.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.core.price_alert_service import PriceAlertService, get_price_alert_service


def _svc_with_alert(**overrides: Any) -> tuple[PriceAlertService, int]:
    svc = PriceAlertService()
    payload: dict[str, Any] = {
        "symbol": "BTC/USDT",
        "interval": "1h",
        "direction": "long",
        "input_id": "in-1",
        "session_id": "s-1",
        "levels": {
            "entry": 100.0,
            "stop_loss": 95.0,
            "take_profit": 110.0,
        },
    }
    payload.update(overrides)
    rule = svc.create_alert(**payload)
    return svc, rule["id"]


# ---------------------------------------------------------------------------
# Service-level
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_on_entry_fires_once_and_deletes_rule() -> None:
    svc, alert_id = _svc_with_alert()
    notified: list[str] = []

    hits = await svc.check_alerts(
        lambda symbol, interval, mt: 100.0, notify_fn=notified.append
    )

    assert len(hits) == 1
    hit = hits[0]
    assert hit["level"] == "entry"
    assert hit["level_price"] == 100.0
    assert hit["price"] == 100.0
    assert hit["rule"]["id"] == alert_id
    assert hit["rule"]["symbol"] == "BTC/USDT"
    assert len(notified) == 1
    assert "100.0" in notified[0]
    # one-shot: rule removed after firing
    assert svc.list_alerts() == []


@pytest.mark.asyncio
async def test_no_touch_keeps_rule() -> None:
    svc, _alert_id = _svc_with_alert()
    notified: list[str] = []

    hits = await svc.check_alerts(
        lambda symbol, interval, mt: 105.0, notify_fn=notified.append
    )

    assert hits == []
    assert notified == []
    assert len(svc.list_alerts()) == 1


@pytest.mark.asyncio
async def test_ws_down_none_price_skips_honestly() -> None:
    svc, _alert_id = _svc_with_alert()

    hits = await svc.check_alerts(
        lambda symbol, interval, mt: None, notify_fn=lambda t: None
    )

    assert hits == []
    assert len(svc.list_alerts()) == 1, "None price must not delete the rule"


@pytest.mark.asyncio
async def test_short_direction_stop_loss_touch() -> None:
    svc, _alert_id = _svc_with_alert(direction="short")
    notified: list[str] = []

    hits = await svc.check_alerts(
        lambda symbol, interval, mt: 96.0, notify_fn=notified.append
    )

    assert len(hits) == 1
    assert hits[0]["level"] == "stop_loss"
    assert hits[0]["level_price"] == 95.0
    assert svc.list_alerts() == []


@pytest.mark.asyncio
async def test_direction_none_baseline_then_crossing() -> None:
    svc, _alert_id = _svc_with_alert(direction=None)
    notified: list[str] = []

    # Round 1 (price above entry): records baseline, never fires.
    hits1 = await svc.check_alerts(
        lambda symbol, interval, mt: 105.0, notify_fn=notified.append
    )
    assert hits1 == []
    assert notified == []
    assert len(svc.list_alerts()) == 1
    assert svc.list_alerts()[0]["baseline_price"] == 105.0

    # Round 2: price crosses entry (105 → 99) → fires.
    hits2 = await svc.check_alerts(
        lambda symbol, interval, mt: 99.0, notify_fn=notified.append
    )
    assert len(hits2) == 1
    assert hits2[0]["level"] == "entry"
    assert svc.list_alerts() == []


@pytest.mark.asyncio
async def test_long_take_profit_high_touch() -> None:
    svc, _alert_id = _svc_with_alert()
    hits = await svc.check_alerts(
        lambda symbol, interval, mt: 111.0, notify_fn=lambda t: None
    )
    assert len(hits) == 1
    assert hits[0]["level"] == "take_profit"


def test_singleton_shared() -> None:
    assert get_price_alert_service() is get_price_alert_service()


@pytest.mark.asyncio
async def test_check_alerts_awaits_async_notify_off_thread() -> None:
    """Advisory ①: check_alerts is async and dispatches the notifier
    off-thread. The default notifier stays synchronous (blocking network
    I/O) — that is exactly why it is offloaded rather than awaited."""
    import inspect

    import app.core.price_alert_service as mod

    assert inspect.iscoroutinefunction(PriceAlertService.check_alerts)
    assert not inspect.iscoroutinefunction(mod._default_notify)

    svc, _alert_id = _svc_with_alert()
    notified: list[str] = []
    hits = await svc.check_alerts(
        lambda symbol, interval, mt: 100.0, notify_fn=notified.append
    )
    assert len(hits) == 1
    assert len(notified) == 1


# ---------------------------------------------------------------------------
# Drawn lines (批3): a bare `price` level with no trade direction.
# ---------------------------------------------------------------------------


def _svc_with_drawn_line(price: float = 100.0) -> tuple[PriceAlertService, int]:
    svc = PriceAlertService()
    rule = svc.create_alert(symbol="BTC/USDT", interval="1h", levels={"price": price})
    return svc, rule["id"]


@pytest.mark.asyncio
async def test_drawn_line_anchors_then_fires_on_crossing() -> None:
    """A drawn line has no direction, so it can only be a crossing: the first
    round anchors the side price started from and must NOT fire."""
    svc, _id = _svc_with_drawn_line(100.0)
    notified: list[str] = []

    # Round 1: price above the line — anchor only.
    hits1 = await svc.check_alerts(
        lambda symbol, interval, mt: 105.0, notify_fn=notified.append
    )
    assert hits1 == [], "anchoring round must not fire"
    assert notified == []
    assert svc.list_alerts()[0]["baseline_price"] == 105.0

    # Round 2: crosses down through the line → fires, one-shot delete.
    hits2 = await svc.check_alerts(
        lambda symbol, interval, mt: 99.0, notify_fn=notified.append
    )
    assert len(hits2) == 1
    assert hits2[0]["level"] == "price"
    assert hits2[0]["level_price"] == 100.0
    assert svc.list_alerts() == []
    assert "画线" in notified[0]


@pytest.mark.asyncio
async def test_drawn_line_fires_crossing_upward_too() -> None:
    """Crossings are two-sided: anchoring below then crossing up must fire.
    (A direction-based low-touch rule would miss this entirely.)"""
    svc, _id = _svc_with_drawn_line(100.0)

    await svc.check_alerts(lambda s, i, m: 95.0, notify_fn=lambda t: None)
    hits = await svc.check_alerts(lambda s, i, m: 101.0, notify_fn=lambda t: None)

    assert len(hits) == 1
    assert hits[0]["level"] == "price"


@pytest.mark.asyncio
async def test_drawn_line_same_side_never_fires() -> None:
    """Price staying on one side of the line is not a crossing — the rule must
    survive indefinitely rather than fire on proximity."""
    svc, _id = _svc_with_drawn_line(100.0)

    for p in (105.0, 108.0, 120.0, 115.0):
        hits = await svc.check_alerts(
            lambda s, i, m, _p=p: _p, notify_fn=lambda t: None
        )
        assert hits == []
    assert len(svc.list_alerts()) == 1


@pytest.mark.asyncio
async def test_drawn_line_ignores_direction_field() -> None:
    """A rule carrying BOTH a drawn line and a direction still treats the line
    as a crossing — the long/short branches must not reinterpret it."""
    svc = PriceAlertService()
    # direction=long would say "entry fires on price <= level"; the drawn line
    # is at 100 and price moves 95 -> 101, i.e. never touches 100 on the low
    # side from above. Only the crossing semantics can fire this.
    svc.create_alert(
        symbol="BTC/USDT",
        interval="1h",
        levels={"price": 100.0},
        direction="long",
    )
    await svc.check_alerts(lambda s, i, m: 95.0, notify_fn=lambda t: None)
    hits = await svc.check_alerts(lambda s, i, m: 101.0, notify_fn=lambda t: None)
    assert len(hits) == 1
    assert hits[0]["level"] == "price"


@pytest.mark.asyncio
async def test_drawn_line_ws_down_keeps_rule() -> None:
    """None price must never anchor or fire — a WS outage cannot silently
    consume the alert."""
    svc, _id = _svc_with_drawn_line(100.0)
    hits = await svc.check_alerts(lambda s, i, m: None, notify_fn=lambda t: None)
    assert hits == []
    assert len(svc.list_alerts()) == 1
    assert svc.list_alerts()[0]["baseline_price"] is None


@pytest.mark.asyncio
async def test_drawn_line_anchored_exactly_on_the_line_still_waits() -> None:
    """The anchoring round can only observe, never judge.

    This is the one case where the anchor-vs-fire decision is observable: if
    price sits *exactly* on the line at first sight, the crossing product is
    already <= 0 and a guard that returns False (i.e. skips the early exit)
    would fire on a line the price never actually crossed.

    Both drawn-line rules are anchored at 100 and then move a hair to 99 —
    the first round must stay silent even though `price == level`.
    """
    svc, _id = _svc_with_drawn_line(100.0)
    notified: list[str] = []

    on_line = await svc.check_alerts(lambda s, i, m: 100.0, notify_fn=notified.append)
    assert on_line == [], "price sitting on the line is not a crossing"
    assert notified == []
    assert len(svc.list_alerts()) == 1

    # Now it genuinely crosses.
    crossed = await svc.check_alerts(lambda s, i, m: 99.0, notify_fn=notified.append)
    assert len(crossed) == 1
    assert crossed[0]["level"] == "price"

    # Same guard on the semantic direction-None path.
    svc2 = PriceAlertService()
    svc2.create_alert(
        symbol="BTC/USDT",
        interval="1h",
        levels={"entry": 100.0},
        direction=None,
    )
    assert (
        await svc2.check_alerts(lambda s, i, m: 100.0, notify_fn=lambda t: None) == []
    )
    assert len(svc2.list_alerts()) == 1


@pytest.mark.asyncio
async def test_semantic_levels_unaffected_by_drawn_line_support() -> None:
    """Regression guard: adding the generic slot must not change how PA
    decision-card rules (entry/SL/TP with a direction) evaluate."""
    svc, _id = _svc_with_alert(direction="long")
    # entry=100 with long fires on price <= 100; must still fire immediately
    # on the FIRST round (no anchoring delay for directed rules).
    hits = await svc.check_alerts(lambda s, i, m: 100.0, notify_fn=lambda t: None)
    assert len(hits) == 1
    assert hits[0]["level"] == "entry"


@pytest.mark.asyncio
async def test_http_drawn_line_round_trip(_fresh_singleton: None) -> None:
    """The endpoint accepts a bare `price` level and returns it intact."""
    from app.main import create_app
    from httpx import ASGITransport, AsyncClient

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/decision-workflow/price-alerts",
            json={
                "symbol": "BTC/USDT",
                "interval": "1d",
                "levels": {"price": 63000.0},
            },
        )
        assert resp.status_code == 201

        listed = await client.get("/api/v1/decision-workflow/price-alerts")
        alert = listed.json()["alerts"][0]
        assert alert["levels"]["price"] == 63000.0
        assert alert["direction"] is None


# ---------------------------------------------------------------------------
# HTTP endpoints (create_app + dependency-free real singleton)
# ---------------------------------------------------------------------------


@pytest.fixture
def _fresh_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the module singleton so HTTP tests see isolated state."""
    import app.core.price_alert_service as mod

    monkeypatch.setattr(mod, "_price_alert_service", None)


@pytest.mark.asyncio
async def test_http_post_get_delete_chain(_fresh_singleton: None) -> None:
    from app.main import create_app
    from httpx import ASGITransport, AsyncClient

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/decision-workflow/price-alerts",
            json={
                "symbol": "BTC/USDT",
                "interval": "1h",
                "direction": "long",
                "input_id": "in-http",
                "session_id": "s-http",
                "levels": {"entry": 100.0, "stop_loss": 95.0, "take_profit": 110.0},
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "registered"
        assert isinstance(body["id"], int)

        alert_id = body["id"]

        listed = await client.get("/api/v1/decision-workflow/price-alerts")
        assert listed.status_code == 200
        alerts = listed.json()["alerts"]
        assert len(alerts) == 1
        assert alerts[0]["id"] == alert_id
        assert alerts[0]["symbol"] == "BTC/USDT"
        assert alerts[0]["levels"]["entry"] == 100.0

        deleted = await client.delete(
            f"/api/v1/decision-workflow/price-alerts/{alert_id}"
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True}

        listed_again = await client.get("/api/v1/decision-workflow/price-alerts")
        assert listed_again.json()["alerts"] == []


@pytest.mark.asyncio
async def test_http_delete_unknown_id_returns_false(
    _fresh_singleton: None,
) -> None:
    from app.main import create_app
    from httpx import ASGITransport, AsyncClient

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete("/api/v1/decision-workflow/price-alerts/99999")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": False}


# ---------------------------------------------------------------------------
# Loop registration gate (source-level assertion — env only gates the loop,
# endpoints are always available by design)
# ---------------------------------------------------------------------------


def test_main_py_loop_gate_source_present() -> None:
    """SELENE_PA_TRADE_ALERTS 门控存在于 main.py(默认关 = 不注册 loop)。"""
    from pathlib import Path

    main_py = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "local-api"
        / "app"
        / "main.py"
    )
    source = main_py.read_text(encoding="utf-8")
    assert "SELENE_PA_TRADE_ALERTS" in source
    assert "_pa_trade_alert_task" in source

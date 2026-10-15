"""``GET /api/v1/strategies/qd-templates`` -- the QuantDinger backup library.

The endpoint's contract is that it mirrors the library rather than curating
it: every export appears, including the ones that cannot run. A card that
silently hid ``src41`` would leave the user wondering why their strategy is
missing; a card that offered it would fail at run time. So the listing says
what each source declares and whether it is drivable, and the front end
renders the gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_strategy.qd.catalog import list_qd_strategy_files

_SRC48 = Path(
    r"<local-path-redacted>"
)
_needs_backup = pytest.mark.skipif(
    not _SRC48.exists(), reason="QuantDinger strategy backup library not installed"
)

_PATH = "/api/v1/strategies/qd-templates"


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@_needs_backup
@pytest.mark.asyncio
async def test_lists_the_whole_backup_library() -> None:
    async with _client(create_app()) as c:
        resp = await c.get(_PATH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["error"] is None
    data = body["data"]
    # Every src*.py export is listed -- the library is the source of truth.
    assert len(data) == len(list_qd_strategy_files())
    ids = [e["id"] for e in data]
    assert len(ids) == len(set(ids)), "ids must not collide"
    assert all(i.startswith("qd:") for i in ids)


@_needs_backup
@pytest.mark.asyncio
async def test_every_entry_carries_the_card_fields() -> None:
    """The front end renders from this shape alone; nothing else to fetch."""
    async with _client(create_app()) as c:
        data = (await c.get(_PATH)).json()["data"]

    for entry in data:
        for key in (
            "id",
            "name",
            "file",
            "symbol",
            "interval",
            "warmup",
            "runnable",
            "reason",
            "qd_baseline",
            "flags",
        ):
            assert key in entry, f"{entry.get('id')} is missing {key!r}"


@_needs_backup
@pytest.mark.asyncio
async def test_src48_reports_its_index_baseline() -> None:
    """The baseline is read from the index, not invented for the card."""
    async with _client(create_app()) as c:
        data = (await c.get(_PATH)).json()["data"]

    src48 = next(e for e in data if e["id"] == "qd:src48")
    assert src48["symbol"] == "XLM/USDT"
    assert src48["interval"] == "1h"
    assert src48["runnable"] is True
    metrics = src48["qd_baseline"]["metrics"]
    assert metrics["total_return"] == pytest.approx(0.946)
    assert metrics["max_drawdown"] == pytest.approx(-0.252)
    assert metrics["sharpe"] == pytest.approx(1.61)


@_needs_backup
@pytest.mark.asyncio
async def test_undrivable_exports_are_listed_but_gated() -> None:
    """A portfolio export appears with runnable=False and a stated reason."""
    async with _client(create_app()) as c:
        data = (await c.get(_PATH)).json()["data"]

    gated = [e for e in data if not e["runnable"]]
    assert gated, "the real library contains undrivable exports"
    for entry in gated:
        assert entry["reason"], f"{entry['id']} is gated without saying why"

    src41 = next(e for e in data if e["id"] == "qd:src41")
    assert src41["runnable"] is False
    assert "multi" in str(src41["reason"]).lower() or "handle_data" in str(
        src41["reason"]
    )


@pytest.mark.asyncio
async def test_an_absent_library_lists_nothing_rather_than_failing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No backup library is an empty catalogue, not a 500."""
    monkeypatch.setenv("SELENE_QD_STRATEGY_DIR", str(tmp_path))
    async with _client(create_app()) as c:
        resp = await c.get(_PATH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"] == []
    assert body["error"] is None


@pytest.mark.asyncio
async def test_the_static_route_is_not_read_as_a_strategy_id() -> None:
    """`qd-templates` must not fall through to ``/{strategy_id}``."""
    async with _client(create_app()) as c:
        resp = await c.get(_PATH)

    # The parameterized route would answer 404 NotFoundError for that id.
    assert resp.status_code == 200, resp.text
    assert resp.json()["error"] is None

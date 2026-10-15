"""AuditLogRepository locks: append defaults and id handling, query filters
(event_type / since / limit), newest-first ordering, detail JSON roundtrips."""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.audit_logs import AuditLogRepository
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def logs():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield AuditLogRepository(db)


async def _write(logs: AuditLogRepository, kind: str, **fields) -> str:
    return await logs.append({"event_type": kind, **fields})


@pytest.mark.asyncio
async def test_append_returns_id_and_details_read_back(logs: AuditLogRepository):
    written = await _write(
        logs,
        "order.submitted",
        severity="info",
        actor="user:carol",
        action="submit_order",
        resource="paper_account:p9",
        outcome="success",
        details={"symbol": "ETH/USDT", "quantity": 2.5},
    )
    assert isinstance(written, str) and written != ""

    found = await logs.query()
    assert len(found) == 1
    assert found[0]["event_type"] == "order.submitted"
    assert found[0]["details"]["symbol"] == "ETH/USDT"


@pytest.mark.asyncio
async def test_generated_ids_never_collide(logs: AuditLogRepository):
    first = await _write(logs, "probe")
    second = await _write(logs, "probe")
    assert first != second


@pytest.mark.asyncio
async def test_caller_supplied_id_is_honored(logs: AuditLogRepository):
    assert await logs.append({"id": "evt-fixed-42", "event_type": "probe"}) == "evt-fixed-42"


@pytest.mark.asyncio
async def test_filter_narrows_to_one_event_type(logs: AuditLogRepository):
    await _write(logs, "order.submitted")
    await _write(logs, "settings.updated")
    await _write(logs, "order.submitted")

    hits = await logs.query(event_type="order.submitted")
    assert len(hits) == 2
    assert {e["event_type"] for e in hits} == {"order.submitted"}


@pytest.mark.asyncio
async def test_since_cutoff_excludes_old_rows(logs: AuditLogRepository):
    await _write(logs, "old-era", timestamp="2025-01-01T00:00:00")
    await _write(logs, "new-era", timestamp="2025-06-01T00:00:00")

    hits = await logs.query(since="2025-03-01T00:00:00")
    assert [e["event_type"] for e in hits] == ["new-era"]


@pytest.mark.asyncio
async def test_limit_caps_returned_rows(logs: AuditLogRepository):
    for day in range(1, 11):
        await _write(logs, "batch-write", timestamp=f"2025-01-{day:02d}T00:00:00")

    assert len(await logs.query(limit=4)) == 4


@pytest.mark.asyncio
async def test_newest_entries_come_first(logs: AuditLogRepository):
    await _write(logs, "jan-event", timestamp="2025-01-01T00:00:00")
    await _write(logs, "jun-event", timestamp="2025-06-01T00:00:00")
    await _write(logs, "mar-event", timestamp="2025-03-01T00:00:00")

    kinds = [e["event_type"] for e in await logs.query()]
    assert kinds == ["jun-event", "mar-event", "jan-event"]


@pytest.mark.asyncio
async def test_combined_filters_intersect(logs: AuditLogRepository):
    await _write(logs, "order", timestamp="2025-01-01T00:00:00")
    await _write(logs, "order", timestamp="2025-06-01T00:00:00")
    await _write(logs, "config", timestamp="2025-06-01T00:00:00")

    hits = await logs.query(event_type="order", since="2025-03-01T00:00:00")
    assert len(hits) == 1
    assert hits[0]["event_type"] == "order"


@pytest.mark.asyncio
async def test_minimal_event_fills_defaults(logs: AuditLogRepository):
    await _write(logs, "bare-bones")
    row = (await logs.query())[0]
    assert row["severity"] == "info"
    assert row["actor"] == ""
    assert row["details"] == {}


@pytest.mark.asyncio
async def test_string_details_roundtrip(logs: AuditLogRepository):
    await _write(logs, "detail-str", details="plain text payload")
    rows = await logs.query(event_type="detail-str")
    assert rows[0]["details"] == "plain text payload"


@pytest.mark.asyncio
async def test_list_details_roundtrip(logs: AuditLogRepository):
    payload = ["SOL/USDT", 9, False]
    await _write(logs, "detail-list", details=payload)
    rows = await logs.query(event_type="detail-list")
    assert rows[0]["details"] == payload


@pytest.mark.asyncio
async def test_null_details_roundtrip(logs: AuditLogRepository):
    await _write(logs, "detail-none", details=None)
    rows = await logs.query(event_type="detail-none")
    assert rows[0]["details"] is None

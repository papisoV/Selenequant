"""Diagnostics locks for selene_space_core.diagnostics.health — registration,
healthy/unhealthy/degraded outcomes, latency capture, name listing."""

import pytest

from selene_space_core.diagnostics.health import HealthCheckResult, HealthRegistry


async def _db_ok():
    return {"status": "healthy", "detail": "sqlite ok"}


async def _db_broken():
    raise ConnectionError("db down")


async def _ws_degraded():
    return HealthCheckResult(name="ws", status="degraded", latency_ms=0, detail={"lag": 5})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "probe", "expected_status"),
    [
        ("db", _db_ok, "healthy"),
        ("ws", _ws_degraded, "degraded"),
    ],
)
async def test_probe_outcomes_flow_through_registry(label, probe, expected_status):
    reg = HealthRegistry()
    reg.register_check(label, probe)
    results = await reg.run_all()
    assert len(results) == 1
    checks = (results[0].name == label, results[0].status == expected_status)
    assert all(checks)


@pytest.mark.asyncio
async def test_healthy_row_carries_non_negative_latency():
    reg = HealthRegistry()
    reg.register_check("db", _db_ok)
    row = (await reg.run_all())[0]
    assert row.latency_ms >= 0


@pytest.mark.asyncio
async def test_raising_probe_is_reported_unhealthy_with_reason():
    reg = HealthRegistry()
    reg.register_check("db", _db_broken)
    row = (await reg.run_all())[0]
    checks = (row.status == "unhealthy", "db down" in row.detail["error"])
    assert all(checks)


def test_check_names_lists_every_registration():
    reg = HealthRegistry()
    for label in ("alpha", "beta"):
        reg.register_check(label, lambda: None)
    assert set(reg.check_names) == {"alpha", "beta"}

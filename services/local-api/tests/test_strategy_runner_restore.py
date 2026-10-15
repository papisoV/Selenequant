"""Restore failure classification + desired-state retry tests.

Absorbed from QD's trading-worker pattern: transient restore failures keep
the deployment queued for retry; permanent failures drop it; unknown errors
fail closed (treated as transient).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace


from app.core.strategy_runner import (
    _RESTORE_MAX_ATTEMPTS,
    _classify_restore_failure,
    StrategyRunner,
)


class _FakeEngine:
    async def place_order(self, **kwargs) -> SimpleNamespace:
        return SimpleNamespace(id="order-1")


def _make_runner(tmp_path) -> StrategyRunner:
    return StrategyRunner(
        paper_engine=_FakeEngine(),
        market_service=None,
        state_dir=tmp_path,
    )


def test_classify_permanent_markers() -> None:
    assert _classify_restore_failure("Deployment d1 already running") == "permanent"
    assert (
        _classify_restore_failure(
            "Strategy s1 is already deployed (d2). Stop existing deployment(s) first."
        )
        == "permanent"
    )
    assert _classify_restore_failure("Compilation failed: bad yaml") == "permanent"
    assert (
        _classify_restore_failure("BLOCKED: regime mismatch 当前行情不适配该策略类型")
        == "permanent"
    )


def test_classify_unknown_is_transient() -> None:
    # Fail-closed: unclassified errors default to transient (QD rule).
    assert _classify_restore_failure("SSL timeout") == "transient"
    assert _classify_restore_failure("something exploded") == "transient"


def test_restore_transient_failure_queued_then_retried(tmp_path, monkeypatch) -> None:
    runner = _make_runner(tmp_path)
    state = {
        "dep-1": {
            "deployment_id": "dep-1",
            "strategy_id": "s1",
            "account_id": "auto",
            "config_snapshot": {"id": "s1", "entry_rules": {"a": 1}},
        }
    }
    (tmp_path / "deployments.json").write_text(json.dumps(state), encoding="utf-8")

    calls = {"n": 0}

    async def fake_deploy(deployment_id, strategy_config, account_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return "connection timeout"
        runner._slots[deployment_id] = SimpleNamespace(deployment_id=deployment_id)
        return None

    monkeypatch.setattr(runner, "deploy", fake_deploy)
    monkeypatch.setattr("app.core.strategy_runner._RESTORE_RETRY_INTERVAL", 0.01)

    async def _run() -> None:
        await runner.start()
        for _ in range(200):
            await asyncio.sleep(0.01)
            if not runner._pending_restores:
                return

    asyncio.run(_run())

    assert runner._slots  # restored on retry
    assert not runner._pending_restores
    assert calls["n"] == 2
    asyncio.run(runner.stop())


def test_restore_permanent_failure_dropped(tmp_path, monkeypatch) -> None:
    runner = _make_runner(tmp_path)
    state = {
        "dep-1": {
            "deployment_id": "dep-1",
            "strategy_id": "s1",
            "account_id": "auto",
            "config_snapshot": {"id": "s1", "entry_rules": {"a": 1}},
        }
    }
    (tmp_path / "deployments.json").write_text(json.dumps(state), encoding="utf-8")

    async def fake_deploy(deployment_id, strategy_config, account_id):
        return "Compilation failed: broken"

    monkeypatch.setattr(runner, "deploy", fake_deploy)

    asyncio.run(runner.start())

    assert not runner._slots
    assert not runner._pending_restores  # dropped, not queued
    asyncio.run(runner.stop())


def test_restore_transient_exhausts_attempts(tmp_path, monkeypatch) -> None:
    runner = _make_runner(tmp_path)
    state = {
        "dep-1": {
            "deployment_id": "dep-1",
            "strategy_id": "s1",
            "account_id": "auto",
            "config_snapshot": {"id": "s1", "entry_rules": {"a": 1}},
        }
    }
    (tmp_path / "deployments.json").write_text(json.dumps(state), encoding="utf-8")

    async def fake_deploy(deployment_id, strategy_config, account_id):
        return "connection reset by peer"

    monkeypatch.setattr(runner, "deploy", fake_deploy)
    monkeypatch.setattr("app.core.strategy_runner._RESTORE_RETRY_INTERVAL", 0.01)

    async def _run() -> None:
        await runner.start()
        for _ in range(300):
            await asyncio.sleep(0.01)
            if not runner._pending_restores:
                return

    asyncio.run(_run())

    assert not runner._pending_restores
    assert runner._restore_attempts.get("dep-1") in (None, _RESTORE_MAX_ATTEMPTS)
    asyncio.run(runner.stop())


def test_restore_alpha_config_not_rejected(tmp_path, monkeypatch) -> None:
    """Alpha strategies have no rules; restore must not reject them (deploy
    grants an exemption — restore used to be missing it, so alpha deployments
    could never survive a restart)."""
    runner = _make_runner(tmp_path)
    state = {
        "dep-a": {
            "deployment_id": "dep-a",
            "strategy_id": "alpha-1",
            "account_id": "auto",
            "config_snapshot": {"id": "alpha-1", "source": "alpha:abc123"},
        }
    }
    (tmp_path / "deployments.json").write_text(json.dumps(state), encoding="utf-8")

    deployed: list[str] = []

    async def fake_deploy(deployment_id, strategy_config, account_id):
        deployed.append(deployment_id)
        return None

    monkeypatch.setattr(runner, "deploy", fake_deploy)

    asyncio.run(runner.start())

    assert deployed == ["dep-a"]
    asyncio.run(runner.stop())


def test_restore_one_bad_deployment_does_not_block_others(
    tmp_path, monkeypatch
) -> None:
    """One deployment raising inside restore must not abort the rest (the old
    single-try-block behavior)."""
    runner = _make_runner(tmp_path)
    state = {
        "dep-bad": {
            "deployment_id": "dep-bad",
            "strategy_id": "s-bad",
            "account_id": "auto",
            "config_snapshot": {"id": "s-bad", "entry_rules": {"a": 1}},
        },
        "dep-good": {
            "deployment_id": "dep-good",
            "strategy_id": "s-good",
            "account_id": "auto",
            "config_snapshot": {"id": "s-good", "entry_rules": {"a": 1}},
        },
    }
    (tmp_path / "deployments.json").write_text(json.dumps(state), encoding="utf-8")
    real_load = runner._load_fresh_config

    async def flaky_load(strategy_id, fallback):
        if strategy_id == "s-bad":
            raise RuntimeError("db not ready")
        return await real_load(strategy_id, fallback)

    monkeypatch.setattr(runner, "_load_fresh_config", flaky_load)

    async def ok_deploy(deployment_id, strategy_config, account_id):
        runner._slots[deployment_id] = SimpleNamespace(deployment_id=deployment_id)
        return None

    monkeypatch.setattr(runner, "deploy", ok_deploy)

    asyncio.run(runner.start())

    assert "dep-good" in runner._slots  # restored despite dep-bad raising
    assert "dep-bad" in runner._pending_restores  # queued for retry, not lost
    asyncio.run(runner.stop())

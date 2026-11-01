"""E2E test for Phase 4.2a — Vibe ChannelManager live wiring end-to-end.

Verifies the complete env → credential → manager → send path. Mocks the
selene keyring and the vibe ``ChannelManager`` SDK imports so the test
runs without real credentials or SDKs installed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


_SELENE_ROOT = Path(__file__).resolve().parents[3]
if str(_SELENE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SELENE_ROOT))


@pytest.fixture(autouse=True)
def _reset_lifespan() -> Any:
    from subsystems.vibe.bridge.channels import lifespan as _lifespan
    _lifespan.reset_for_test()
    yield
    _lifespan.reset_for_test()


def _mock_secret_manager(values: dict[str, str]) -> Any:
    class _Resolved:
        def __init__(self, value: str) -> None:
            self._value = value

        def use(self) -> str:
            return self._value

    class _MockSM:
        async def resolve(self, ref: Any) -> Any:
            return _Resolved(values.get(ref.id, ""))

    return _MockSM()


def test_e2e_env_off_returns_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E: env unset → preset.send_is_stub=True, send returns False."""
    monkeypatch.delenv("SELENE_VIBE_CHANNELS_LIVE", raising=False)
    from subsystems.vibe.bridge.channels.preset import (
        vibe_channels_preset,
        send_via_vibe,
    )

    descriptor = vibe_channels_preset()
    assert descriptor.send_is_stub is True
    assert descriptor.live_enabled is False
    assert send_via_vibe("telegram", "test") is False


def test_e2e_env_on_no_credentials_returns_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2E: env set but no creds → manager not started, send returns False."""
    monkeypatch.setenv("SELENE_VIBE_CHANNELS_LIVE", "1")

    sm = _mock_secret_manager({})

    from subsystems.vibe.bridge.channels.lifespan import init_vibe_channels
    from subsystems.vibe.bridge.channels.preset import (
        vibe_channels_preset,
        send_via_vibe,
    )

    result = asyncio.run(init_vibe_channels(secret_manager=sm))
    assert result is False
    descriptor = vibe_channels_preset()
    assert descriptor.live_enabled is True
    # Manager not running → still stub
    assert descriptor.send_is_stub is True
    assert send_via_vibe("telegram", "test") is False


def test_e2e_full_path_env_creds_manager_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2E: env set + creds → manager started → send returns True."""
    monkeypatch.setenv("SELENE_VIBE_CHANNELS_LIVE", "1")

    sm = _mock_secret_manager({
        "selene.vibe.telegram.bot_token": "abc123",
        "selene.vibe.telegram.chat_id": "-100",
    })

    class _FakeChannel:
        is_running = True
        config = {"chat_id": "-100", "bot_token": "abc123"}

    class _FakeManager:
        def __init__(self, config: Any, bus: Any, **kwargs: Any) -> None:
            self.config = config
            self.bus = bus
            self.channels = {"telegram": _FakeChannel()}
            self._dispatch_task = None

        async def start_all(self) -> None:
            pass

        async def stop_all(self) -> None:
            pass

        def get_channel(self, name: str) -> Any:
            return self.channels.get(name)

        @property
        def enabled_channels(self) -> list[str]:
            return list(self.channels.keys())

    class _FakeBus:
        def __init__(self) -> None:
            self.published: list[Any] = []

        async def publish_outbound(self, msg: Any) -> None:
            self.published.append(msg)

    fake_bus = _FakeBus()

    with patch(
        "src.channels.manager.ChannelManager",
        _FakeManager,
    ), patch(
        "src.channels.bus.queue.MessageBus",
        lambda: fake_bus,
    ):
        from subsystems.vibe.bridge.channels.lifespan import (
            init_vibe_channels,
            shutdown_vibe_channels,
        )
        from subsystems.vibe.bridge.channels.preset import (
            vibe_channels_preset,
            send_via_vibe,
            send_alert_via_vibe,
        )

        # Init + send must run in the same event loop — the bridge
        # schedules publish_outbound on the running loop, so calling
        # send from sync code after asyncio.run exits would have no
        # loop to schedule onto.
        async def _driver() -> None:
            started = await init_vibe_channels(secret_manager=sm)
            assert started is True

            descriptor = vibe_channels_preset()
            assert descriptor.live_enabled is True
            assert descriptor.send_is_stub is False

            # Send to configured channel
            assert send_via_vibe("telegram", "hello world") is True
            # Send alert — preset signature is (channel, message)
            assert send_alert_via_vibe("telegram", "alert message") is True
            # Send to unknown channel
            assert send_via_vibe("slack", "nope") is False

            await shutdown_vibe_channels()

        asyncio.run(_driver())

    # After shutdown, descriptor flips back to stub
    descriptor_after = vibe_channels_preset()
    assert descriptor_after.send_is_stub is True


def test_e2e_partial_credentials_skips_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2E: env set + partial creds → telegram skipped, manager starts empty."""
    monkeypatch.setenv("SELENE_VIBE_CHANNELS_LIVE", "1")

    # Only bot_token, no chat_id → telegram skipped
    sm = _mock_secret_manager({
        "selene.vibe.telegram.bot_token": "abc123",
    })

    class _FakeManager:
        def __init__(self, config: Any, bus: Any, **kwargs: Any) -> None:
            self.config = config
            self.bus = bus
            self.channels = {}
            self._dispatch_task = None

        async def start_all(self) -> None:
            pass

        async def stop_all(self) -> None:
            pass

        def get_channel(self, name: str) -> Any:
            return None

        @property
        def enabled_channels(self) -> list[str]:
            return []

    with patch(
        "src.channels.manager.ChannelManager",
        _FakeManager,
    ):
        from subsystems.vibe.bridge.channels.lifespan import init_vibe_channels
        # build_vibe_channels_config returns empty → init returns False
        result = asyncio.run(init_vibe_channels(secret_manager=sm))
        assert result is False


def test_e2e_shutdown_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E: calling shutdown multiple times is safe."""
    from subsystems.vibe.bridge.channels.lifespan import shutdown_vibe_channels

    asyncio.run(shutdown_vibe_channels())
    asyncio.run(shutdown_vibe_channels())

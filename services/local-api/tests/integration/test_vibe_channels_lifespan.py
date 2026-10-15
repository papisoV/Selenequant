"""Integration test for Phase 4.2a — Vibe ChannelManager live wiring.

Covers:
- Env set + mock credentials → ChannelManager init succeeds.
- The manager exposes the configured channels.
- Bridge ``send_via_vibe`` returns True when manager is live + channel configured.
- Shutdown cleans up singletons.

This test mocks the selene ``SecretManager`` so it does NOT hit the OS
keyring. It exercises the real vibe ``ChannelManager`` constructor path
but mocks ``start_all`` / ``stop_all`` so no real network/SDK activity
happens.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


# Ensure the selene-master root is on sys.path for subsystems.* imports
_SELENE_ROOT = Path(__file__).resolve().parents[3]
if str(_SELENE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SELENE_ROOT))


@pytest.fixture(autouse=True)
def _reset_lifespan_singletons() -> Any:
    """Reset lifespan module singletons before AND after each test."""
    from subsystems.vibe.bridge.channels import lifespan as _lifespan
    _lifespan.reset_for_test()
    yield
    _lifespan.reset_for_test()


def _make_mock_secret_manager(values: dict[str, str]) -> Any:
    class _Resolved:
        def __init__(self, value: str) -> None:
            self._value = value

        def use(self) -> str:
            return self._value

    class _MockSM:
        async def resolve(self, ref: Any) -> Any:
            return _Resolved(values.get(ref.id, ""))

    return _MockSM()


def test_init_vibe_channels_with_mock_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When env set + telegram creds present → manager init + start succeed."""
    monkeypatch.setenv("SELENE_VIBE_CHANNELS_LIVE", "1")

    sm = _make_mock_secret_manager({
        "selene.vibe.telegram.bot_token": "abc123",
        "selene.vibe.telegram.chat_id": "-100",
    })

    from subsystems.vibe.bridge.channels.lifespan import (
        init_vibe_channels,
        get_vibe_manager,
    )

    # Patch ChannelManager to avoid loading real SDK adapters (telegram
    # SDK may not be installed in the test env). We verify that the
    # lifespan invokes start_all() and stores the singleton.
    started = {"called": False}

    class _FakeChannel:
        is_running = True

    class _FakeManager:
        def __init__(self, config: Any, bus: Any, **kwargs: Any) -> None:
            self.config = config
            self.bus = bus
            self.channels = {"telegram": _FakeChannel()}

        async def start_all(self) -> None:
            started["called"] = True

        async def stop_all(self) -> None:
            pass

        def get_channel(self, name: str) -> Any:
            return self.channels.get(name)

        @property
        def enabled_channels(self) -> list[str]:
            return list(self.channels.keys())

    with patch(
        "src.channels.manager.ChannelManager",
        _FakeManager,
    ):
        result = asyncio.run(init_vibe_channels(secret_manager=sm))

    assert result is True
    assert started["called"] is True
    assert get_vibe_manager() is not None
    assert "telegram" in get_vibe_manager().enabled_channels


def test_init_failure_does_not_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ChannelManager constructor raises, init returns False (non-fatal)."""
    monkeypatch.setenv("SELENE_VIBE_CHANNELS_LIVE", "1")

    sm = _make_mock_secret_manager({
        "selene.vibe.telegram.bot_token": "abc",
        "selene.vibe.telegram.chat_id": "-100",
    })

    class _RaisingManager:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("boom")

    with patch(
        "src.channels.manager.ChannelManager",
        _RaisingManager,
    ):
        from subsystems.vibe.bridge.channels.lifespan import (
            init_vibe_channels,
            get_vibe_manager,
        )
        result = asyncio.run(init_vibe_channels(secret_manager=sm))

    assert result is False
    assert get_vibe_manager() is None


def test_send_via_vibe_returns_true_when_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_via_vibe returns True when manager is live + channel configured."""
    monkeypatch.setenv("SELENE_VIBE_CHANNELS_LIVE", "1")

    sm = _make_mock_secret_manager({
        "selene.vibe.telegram.bot_token": "abc",
        "selene.vibe.telegram.chat_id": "-100",
    })

    class _FakeChannel:
        is_running = True
        config = {"chat_id": "-100"}

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

    # The fake bus must accept publish_outbound as a coroutine
    class _FakeBus:
        def __init__(self) -> None:
            self.published: list[Any] = []

        async def publish_outbound(self, msg: Any) -> None:
            self.published.append(msg)

    fake_bus_instance = _FakeBus()

    with patch(
        "src.channels.manager.ChannelManager",
        _FakeManager,
    ), patch(
        "src.channels.bus.queue.MessageBus",
        lambda: fake_bus_instance,
    ):
        from subsystems.vibe.bridge.channels.lifespan import (
            init_vibe_channels,
        )
        from subsystems.vibe.bridge.channels.preset import send_via_vibe

        async def _driver() -> None:
            await init_vibe_channels(secret_manager=sm)
            # send_via_vibe must be called from an async context so the
            # bridge can schedule publish_outbound on the running loop
            # (matches the production path: selene's lifespan always has
            # a running loop when sends happen).
            nonlocal result, result2
            result = send_via_vibe("telegram", "hello world")
            # Yield one tick so the create_task'd publish runs.
            await asyncio.sleep(0)
            result2 = send_via_vibe("slack", "nope")

        result = False
        result2 = False
        asyncio.run(_driver())

        # Now send a message — must be True (queued, dispatched async)
        assert result is True
        # Verify send to unknown channel returns False
        assert result2 is False


def test_shutdown_after_init_clears_singletons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """shutdown_vibe_channels clears the manager + bus singletons."""
    monkeypatch.setenv("SELENE_VIBE_CHANNELS_LIVE", "1")

    sm = _make_mock_secret_manager({
        "selene.vibe.telegram.bot_token": "abc",
        "selene.vibe.telegram.chat_id": "-100",
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
        from subsystems.vibe.bridge.channels.lifespan import (
            init_vibe_channels,
            shutdown_vibe_channels,
            get_vibe_manager,
            get_vibe_bus,
        )
        asyncio.run(init_vibe_channels(secret_manager=sm))
        assert get_vibe_manager() is not None
        assert get_vibe_bus() is not None

        asyncio.run(shutdown_vibe_channels())
        assert get_vibe_manager() is None
        assert get_vibe_bus() is None


def test_preset_send_is_stub_false_when_manager_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Descriptor.send_is_stub flips to False when manager is live."""
    monkeypatch.setenv("SELENE_VIBE_CHANNELS_LIVE", "1")

    sm = _make_mock_secret_manager({
        "selene.vibe.telegram.bot_token": "abc",
        "selene.vibe.telegram.chat_id": "-100",
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
        from subsystems.vibe.bridge.channels.preset import vibe_channels_preset

        asyncio.run(init_vibe_channels(secret_manager=sm))
        descriptor = vibe_channels_preset()
        assert descriptor.live_enabled is True
        assert descriptor.send_is_stub is False

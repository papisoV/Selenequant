"""Abstract stream client: lifecycle contract + callback fan-out plumbing."""

from __future__ import annotations

import asyncio, logging  # noqa: E401
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from selene_space_exchange.types import WSClientConfig

logger = logging.getLogger(__name__)
Callback = Callable[..., Any]

#: (attribute slot, constructor keyword) pairs wired in ``__init__``.
_HOOK_SLOTS: tuple[tuple[str, str], ...] = (
    ("on_message", "on_message"),
    ("on_error", "on_error"),
    ("on_connect", "on_connect"),
    ("on_disconnect", "on_disconnect"),
)

_NORMAL_CLOSURE_CODE = 1000


def _fire(callback: Callback | None, *args: Any) -> Any:
    """Call *callback* if set; return the result only when it is awaitable.

    Sync hooks fire and forget; coroutine-returning hooks come back here so
    the caller can ``await`` them.
    """
    if callback is None:
        return None
    outcome = callback(*args)
    return outcome if isinstance(outcome, Awaitable) else None


class BaseWSClient(ABC):
    """Lifecycle + fan-out plumbing for venue stream clients.

    Concrete venues implement :meth:`connect` / :meth:`subscribe` /
    :meth:`unsubscribe` / :meth:`close`; everything around those calls —
    hook wiring, connection state, subscription bookkeeping — lives here.
    """

    def __init__(self, config: WSClientConfig, **hooks: Any) -> None:
        self._config = config
        for slot, keyword in _HOOK_SLOTS:
            setattr(self, slot, hooks.get(keyword))
        self._subscriptions: set[str] = set()
        self._is_connected: bool = False
        self._closed_once: asyncio.Event | None = None

    # -- venue-supplied interface ---------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """Open the socket; dispatch _dispatch_connect when established."""

    @abstractmethod
    async def subscribe(self, streams: list[str]) -> None:
        """Register streams with the venue and record them in _subscriptions."""

    @abstractmethod
    async def unsubscribe(self, streams: list[str]) -> None:
        """Drop streams both locally and at the venue."""

    @abstractmethod
    async def close(self) -> None:
        """Tear down the socket; dispatch _dispatch_disconnect."""

    # -- introspection ----------------------------------------------------------

    @property
    def config(self) -> WSClientConfig:
        return self._config

    @property
    def subscriptions(self) -> frozenset[str]:
        return frozenset(self._subscriptions)

    @property
    def is_connected(self) -> bool:
        return bool(self._is_connected)

    # -- disconnect wait ---------------------------------------------------------

    def _drop_event(self) -> asyncio.Event:
        """Lazily create the socket-dropped event.

        Created already-set when the socket is currently down, so late waiters
        fall straight through instead of blocking forever.
        """
        if self._closed_once is None:
            created = asyncio.Event()
            if not self._is_connected:
                created.set()
            self._closed_once = created
        return self._closed_once

    async def wait_disconnected(self) -> None:
        """Block until the socket drops (reconnect supervisor waits on this)."""
        if not self._is_connected:
            return
        await self._drop_event().wait()

    # -- hook fan-out -------------------------------------------------------------

    async def _dispatch_message(self, data: dict[str, Any]) -> None:
        awaited = _fire(self.on_message, data)
        if awaited is not None:
            await awaited

    async def _dispatch_error(self, error: Exception) -> None:
        awaited = _fire(self.on_error, error)
        if awaited is not None:
            await awaited

    async def _dispatch_connect(self) -> None:
        self._is_connected = True
        self._closed_once = None
        awaited = _fire(self.on_connect)
        if awaited is not None:
            await awaited

    async def _dispatch_disconnect(
        self, code: int = _NORMAL_CLOSURE_CODE, reason: str = ""
    ) -> None:
        self._is_connected = False
        self._drop_event().set()
        awaited = _fire(self.on_disconnect, code, reason)
        if awaited is not None:
            await awaited

    @staticmethod
    async def _invoke(callback: Callback | None, *args: Any) -> None:
        """Legacy fan-out entry kept for subclasses that call it directly."""
        awaited = _fire(callback, *args)
        if awaited is not None:
            await awaited

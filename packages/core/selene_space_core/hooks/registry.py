"""Priority-ordered hook registry: (event, handler) pairs with safe dispatch."""

from __future__ import annotations

import inspect
from collections.abc import Callable

from selene_space_core.hooks.types import HookPriority


class HookRegistry:
    """Handlers run HIGH -> NORMAL -> LOW. Exceptions in one handler never
    propagate to the emitter or to later handlers."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[tuple[HookPriority, Callable]]] = {}

    def register(
        self,
        event: str,
        handler: Callable,
        priority: HookPriority = HookPriority.NORMAL,
    ) -> None:
        self._hooks.setdefault(event, []).append((priority, handler))

    def _ordered(self, event: str) -> list[Callable]:
        bucket = self._hooks.get(event, [])
        return [h for _, h in sorted(bucket, key=lambda pair: -pair[0].value)]

    def emit(self, event: str, payload=None) -> None:
        for handler in self._ordered(event):
            try:
                result = handler(event, payload)
                if inspect.isawaitable(result):
                    # Sync emit cannot await; schedule if a loop is running.
                    import asyncio

                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        pass
                    else:
                        loop.create_task(result)
            except Exception:
                pass

    async def emit_async(self, event: str, payload=None) -> None:
        for handler in self._ordered(event):
            try:
                result = handler(event, payload)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                pass

    def list_events(self) -> list[str]:
        return list(self._hooks.keys())

    def clear(self) -> None:
        self._hooks.clear()

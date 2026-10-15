"""Process-wide plugin registry keyed by (capability, name)."""

from __future__ import annotations

import threading


class PluginRegistry:
    """Singleton. register() twice for the same slot raises ValueError."""

    _instance: "PluginRegistry | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "PluginRegistry":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._plugins = {}
                    cls._instance = instance
        return cls._instance

    # -- test seam ----------------------------------------------------------

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    # -- registry API ---------------------------------------------------------

    def register(self, capability: str, name: str, plugin: object) -> None:
        bucket = self._plugins.setdefault(capability, {})
        if name in bucket:
            raise ValueError(f"plugin already registered: {capability}/{name}")
        bucket[name] = plugin

    def get(self, capability: str, name: str) -> object | None:
        return self._plugins.get(capability, {}).get(name)

    def list(self, capability: str) -> list[str]:
        return list(self._plugins.get(capability, {}).keys())

    def list_capabilities(self) -> list[str]:
        return list(self._plugins.keys())

    def clear(self) -> None:
        self._plugins.clear()

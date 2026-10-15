"""Named component registry for the open-core architecture."""

from __future__ import annotations


class ComponentRegistry:
    """Store one component per kind, with replace-by-name semantics.

    Kinds are free-form strings ("memory", "model_router", ...). The
    registry is intentionally tiny: it exists so the community edition
    can swap any slot with a conforming implementation.
    """

    def __init__(self) -> None:
        self._components: dict[str, object] = {}

    def register(self, kind: str, component: object) -> None:
        if kind in self._components:
            raise KeyError(f"Component kind '{kind}' already registered")
        self._components[kind] = component

    def replace(self, kind: str, component: object) -> None:
        if kind not in self._components:
            raise KeyError(f"Component kind '{kind}' not registered")
        self._components[kind] = component

    def get(self, kind: str) -> object:
        if kind not in self._components:
            raise KeyError(f"Component kind '{kind}' not registered")
        return self._components[kind]

    def is_registered(self, kind: str) -> bool:
        return kind in self._components

    def list_registered(self) -> dict[str, str]:
        return {kind: type(c).__name__ for kind, c in self._components.items()}

    def __len__(self) -> int:
        return len(self._components)

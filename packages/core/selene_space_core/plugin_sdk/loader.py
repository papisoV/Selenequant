"""Import-based loading of plugin modules and "module:attr" entry points."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field


@dataclass
class PluginManifest:
    name: str
    version: str = "0.0.0"
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    entry_point: str = ""


@dataclass
class LoadedPlugin:
    name: str
    module: object
    manifest: PluginManifest | None = None
    setup_fn: object | None = None


class PluginLoader:
    """load() imports a module; load_entry_point() imports "mod:attr"
    and exposes the attribute as setup_fn."""

    def load(self, module_name: str) -> LoadedPlugin:
        module = importlib.import_module(module_name)
        return LoadedPlugin(name=module_name, module=module)

    def load_entry_point(self, entry_point: str) -> LoadedPlugin:
        if ":" not in entry_point:
            raise ValueError(f"entry point must be 'module:attr', got {entry_point!r}")
        module_name, attr = entry_point.split(":", 1)
        module = importlib.import_module(module_name)
        try:
            setup_fn = getattr(module, attr)
        except AttributeError as exc:
            raise ImportError(
                f"module {module_name!r} has no attribute {attr!r}"
            ) from exc
        return LoadedPlugin(name=module_name, module=module, setup_fn=setup_fn)

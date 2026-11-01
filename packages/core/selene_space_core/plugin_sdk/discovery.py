"""Locate installable plugins: shipped-in, pip entry points, workspace dirs,
user YAML — first tier that claims a name wins."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SOURCE_BUNDLED = "bundled"
_SOURCE_PIP = "pip"
_SOURCE_WORKSPACE = "workspace"
_SOURCE_USER = "user"


@dataclass
class DiscoveredPlugin:
    """A plugin candidate: importable module plus provenance."""

    name: str
    module: str
    source: str  # one of bundled / pip / workspace / user
    metadata: dict[str, Any] = field(default_factory=dict)


class PluginDiscovery:
    """Aggregate the four sources into a de-duplicated candidate list.

    The list is memoized; invalidate_cache() or discover(force_refresh=True)
    rescans. Bundled entries always sort ahead of later tiers on conflicts."""

    ENTRY_POINT_GROUP = "selenespace.plugins"

    def __init__(
        self,
        bundled: list[DiscoveredPlugin] | None = None,
        workspace_dir: str | os.PathLike | None = None,
        user_config_path: str | os.PathLike | None = None,
    ) -> None:
        self._bundled = list(bundled or [])
        self._workspace_root = Path(workspace_dir) if workspace_dir else None
        self._yaml_path = (
            Path(user_config_path)
            if user_config_path
            else Path.home() / ".selenespace" / "plugins.yaml"
        )
        self._memo: list[DiscoveredPlugin] | None = None

    def discover(self, *, force_refresh: bool = False) -> list[DiscoveredPlugin]:
        if self._memo is not None and not force_refresh:
            return list(self._memo)

        claimed: set[str] = set()
        ordered: list[DiscoveredPlugin] = []
        for candidate in (
            self._bundled
            + self._scan_pip()
            + self._scan_workspace()
            + self._scan_yaml()
        ):
            if candidate.name in claimed:
                continue
            claimed.add(candidate.name)
            ordered.append(candidate)

        self._memo = ordered
        return list(ordered)

    def invalidate_cache(self) -> None:
        self._memo = None

    # -- scanners ---------------------------------------------------------------

    def _scan_pip(self) -> list[DiscoveredPlugin]:
        import importlib.metadata

        hits: list[DiscoveredPlugin] = []
        try:
            registry = importlib.metadata.entry_points()
            if hasattr(registry, "select"):
                matches = list(registry.select(group=self.ENTRY_POINT_GROUP))
            else:  # legacy mapping interface (Python < 3.10)
                matches = list(registry.get(self.ENTRY_POINT_GROUP, []))  # type: ignore[call-overload]
            for ep in matches:
                hits.append(
                    DiscoveredPlugin(name=ep.name, module=ep.value, source=_SOURCE_PIP)
                )
        except Exception as exc:
            logger.debug("entry-point scan skipped: %s", exc)
        return hits

    def _scan_workspace(self) -> list[DiscoveredPlugin]:
        if self._workspace_root is None or not self._workspace_root.is_dir():
            return []
        hits: list[DiscoveredPlugin] = []
        for child in sorted(self._workspace_root.iterdir()):
            if child.is_dir() and (child / "__init__.py").is_file():
                hits.append(
                    DiscoveredPlugin(
                        name=child.name, module=str(child), source=_SOURCE_WORKSPACE
                    )
                )
        return hits

    def _scan_yaml(self) -> list[DiscoveredPlugin]:
        if not self._yaml_path.is_file():
            return []
        try:
            import yaml

            document = yaml.safe_load(self._yaml_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("user plugin yaml unreadable: %s", exc)
            return []
        if not isinstance(document, dict):
            return []
        rows = document.get("plugins") or []
        return [
            DiscoveredPlugin(
                name=str(row["name"]),
                module=str(row.get("module", row["name"])),
                source=_SOURCE_USER,
            )
            for row in rows
            if isinstance(row, dict) and row.get("name")
        ]

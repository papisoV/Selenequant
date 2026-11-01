"""Category-routed diagnostic feed with a catch-all channel and a nesting cap.
Distinct from the domain EventBus (events.bus): coarse string categories,
no payload typing, fire-and-forget delivery."""

from __future__ import annotations
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

_NEST_LIMIT = 100


@dataclass
class DiagnosticRecord:
    """A single observable occurrence: routing key + human text + extras."""

    category: str
    message: str
    attributes: dict[str, Any] = field(default_factory=dict)


Handler = Callable[[DiagnosticRecord], None]


class DiagnosticBus:
    """Delivers each record to the matching category plus ``*`` listeners.
    Guarantees: (1) a raising handler never disturbs siblings or the
    emitter; (2) handler-triggered re-emits stop descending at exactly
    _NEST_LIMIT frames (behavior-lock pinned)."""

    def __init__(self) -> None:
        self._by_category: dict[str, list[Handler]] = {}
        self._mutex = threading.Lock()
        self._nesting = 0

    @contextmanager
    def _admission(self):
        """Permit delivery unless already nested to the cap."""
        admitted = self._nesting < _NEST_LIMIT
        if admitted:
            self._nesting += 1
        try:
            yield admitted
        finally:
            if admitted:
                self._nesting -= 1

    def subscribe(self, category: str, handler: Handler) -> None:
        """Attach ``handler`` to one category, or to everything via "*"."""
        with self._mutex:
            self._by_category.setdefault(category, []).append(handler)

    def emit(self, record: DiagnosticRecord) -> None:
        with self._admission() as admitted:
            if not admitted:
                return
            # Snapshot under lock so handlers may subscribe/emit freely.
            with self._mutex:
                targets = [
                    *self._by_category.get(record.category, ()),
                    *self._by_category.get("*", ()),
                ]
            for deliver in targets:
                try:
                    deliver(record)
                except Exception:
                    pass

    def clear(self) -> None:
        with self._mutex:
            self._by_category.clear()

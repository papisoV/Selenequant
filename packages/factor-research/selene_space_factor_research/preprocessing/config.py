"""Configuration for a single preprocessing step.

`PreprocessConfig` fixes the AlphaPurify `ResearchConfig` bug (missing
`@dataclass`) — it is a real dataclass and can be instantiated directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PreprocessConfig:
    """One step in a preprocessing chain.

    Attributes:
        method: Registry key such as ``"winsorize_mad"`` (or a bare key
            like ``"mad"`` when unambiguous across categories).
        params: Keyword arguments for the method — must include the
            data-column arguments it needs (e.g. ``trade_date_col``,
            ``factor_col``, and for neutralize ``neutralizer_cols``).
    """

    method: str
    params: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.params is None:
            self.params = {}

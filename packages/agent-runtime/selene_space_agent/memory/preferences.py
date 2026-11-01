"""Persistent user preferences for the trading assistant."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError


class RiskAppetite(str, Enum):
    """How much risk the user is comfortable taking."""

    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


class UserPreferences(BaseModel):
    """Serializable preference record persisted as JSON."""

    risk_appetite: RiskAppetite = RiskAppetite.MODERATE
    preferred_symbols: list[str] = []
    preferred_timeframes: list[str] = []
    preferred_strategy_types: list[str] = []


def _validate_path(path: Path) -> Path:
    return Path(path)


def save_preferences(prefs: UserPreferences, path: Path) -> None:
    target = _validate_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(prefs.model_dump_json(indent=2), encoding="utf-8")


def load_preferences(path: Path) -> UserPreferences:
    target = _validate_path(path)
    if not target.exists():
        return UserPreferences()
    try:
        data: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
        return UserPreferences.model_validate(data)
    except (json.JSONDecodeError, ValidationError, OSError):
        return UserPreferences()


def update_preference(key: str, value: Any, path: Path) -> UserPreferences:
    current = load_preferences(path)
    if key not in current.model_fields:
        raise ValueError(f"Unknown preference key: {key}")
    updated = current.model_copy(update={key: value})
    save_preferences(updated, path)
    return updated

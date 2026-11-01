"""Macro data models — frozen dataclasses for FRED/yfinance macro events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MacroEventType(str, Enum):
    FOMC = "FOMC"
    CPI = "CPI"
    NFP = "NFP"
    FED_FUNDS = "FED_FUNDS"


@dataclass(frozen=True)
class MacroEvent:
    event_type: MacroEventType
    date: str  # ISO "YYYY-MM-DD" in US Eastern
    release_time: str  # "08:30" or "14:00" or "" if unknown
    actual_value: float | None
    forecast_value: float | None
    previous_value: float | None
    surprise: float | None  # (actual - forecast) / |forecast|
    fred_series: str  # FRED series ID
    timestamp_utc: int  # ms epoch in UTC for kline alignment


@dataclass(frozen=True)
class VixDaily:
    date: str  # "YYYY-MM-DD"
    open: float
    high: float
    low: float
    close: float
    timestamp: int  # ms epoch


@dataclass(frozen=True)
class DxyDaily:
    date: str  # "YYYY-MM-DD"
    close: float
    timestamp: int  # ms epoch

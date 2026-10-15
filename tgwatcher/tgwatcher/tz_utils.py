"""Timezone utilities for TGWatcher.

Convention: all datetimes stored in SQLite are naive UTC (no tzinfo).
This matches SQLite's `datetime('now')` which returns UTC.

User-facing times (API responses, CLI output) are converted to local time
at the boundary layer. SQL aggregations (heatmap, trend) shift UTC to local
before grouping.

NOTE: Existing rows in `crawled_at`/`updated_at`/`created_at` columns were
written with `datetime.now()` (local time) before this module was introduced.
New rows use `utc_now()` (UTC). There is a ~8-hour inconsistency in metadata
columns for old data. This is acceptable because these columns are not used
for user-facing queries or aggregations — only `Message.date` (always UTC)
matters for filtering and grouping.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Configurable offset; defaults to UTC+8 (China Standard Time)
_TZ_OFFSET_HOURS: int = 8


def set_tz_offset(hours: int) -> None:
    """Set the local timezone offset. Called once at startup from config."""
    global _TZ_OFFSET_HOURS
    _TZ_OFFSET_HOURS = hours


def tz_offset_hours() -> int:
    """Return the configured local timezone offset in hours."""
    return _TZ_OFFSET_HOURS


def utc_now() -> datetime:
    """Return current UTC time as a naive datetime (SQLite convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local_to_utc(dt: datetime) -> datetime:
    """Convert a local datetime to naive UTC datetime.

    Used when the API/frontend sends a local date and we need to query
    against UTC-stored Message.date.

    Accepts both naive (interpreted as configured local time) and aware
    datetimes (normalized to UTC). Aware inputs are converted to UTC, then
    dropped to naive to match the SQLite convention.
    """
    if dt.tzinfo is not None:
        # Aware: convert to UTC and drop tzinfo
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    # Naive: interpret as configured local time
    return dt - timedelta(hours=_TZ_OFFSET_HOURS)


def utc_to_local(dt: datetime) -> datetime:
    """Convert a naive UTC datetime to naive local datetime.

    Used when displaying DB timestamps to the user.

    If an aware datetime is passed, it is first converted to UTC, then to
    local — so passing UTC-aware values also works correctly.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt + timedelta(hours=_TZ_OFFSET_HOURS)


def utc_to_local_iso(dt: datetime | None) -> str | None:
    """Convert naive UTC datetime to local ISO string for API responses."""
    if dt is None:
        return None
    return (dt + timedelta(hours=_TZ_OFFSET_HOURS)).isoformat()


def local_date_to_utc_range(date_str: str) -> tuple[datetime, datetime]:
    """Convert a local date string 'YYYY-MM-DD' to UTC datetime range.

    Returns (start_utc, end_utc) covering the full local day.
    E.g., '2026-07-15' in UTC+8 ->
      start = 2026-07-14 16:00:00 UTC
      end   = 2026-07-15 15:59:59 UTC
    """
    local_start = datetime.fromisoformat(date_str)
    local_end = local_start.replace(hour=23, minute=59, second=59, microsecond=999999)
    return local_to_utc(local_start), local_to_utc(local_end)


def sql_tz_shift() -> str:
    """Return the SQLite modifier string to shift UTC to local time.

    E.g., for UTC+8 returns '8 hours', for UTC-5 returns '-5 hours'.
    Used in func.strftime/func.date.
    """
    return f"{_TZ_OFFSET_HOURS} hours"

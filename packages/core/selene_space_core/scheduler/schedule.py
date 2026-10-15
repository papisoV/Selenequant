"""Schedule model: fixed-interval, one-shot datetime, or five-field cron."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from datetime import datetime, timedelta


class ScheduleType(enum.Enum):
    EVERY = "every"
    AT = "at"
    CRON = "cron"


_INTERVAL = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_interval(spec: str) -> timedelta:
    match = _INTERVAL.match(spec.strip())
    if not match:
        raise ValueError(
            f"invalid interval spec: {spec!r} (expected e.g. '30s', '5m', '1h', '7d')"
        )
    return timedelta(seconds=int(match.group(1)) * _UNIT_SECONDS[match.group(2)])


@dataclass
class Schedule:
    schedule_type: ScheduleType
    spec: str
    task_name: str

    def next_run(self, after: datetime | None = None) -> datetime | None:
        now = after if after is not None else datetime.now()

        if self.schedule_type is ScheduleType.EVERY:
            return now + _parse_interval(self.spec)

        if self.schedule_type is ScheduleType.AT:
            target = datetime.fromisoformat(self.spec)
            return target if target > now else None

        # CRON: five fields min hour dom month dow; advance to the next matching
        # minute strictly after *now* (bounded scan, ~366 days of minutes).
        fields = self._parse_cron()
        candidate = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        for _ in range(527040):  # one leap year of minutes
            if self._matches(fields, candidate):
                return candidate
            candidate += timedelta(minutes=1)
        return None

    def _parse_cron(self) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
        parts = self.spec.split()
        if len(parts) != 5:
            raise ValueError(f"cron spec must have 5 fields, got {self.spec!r}")
        return (
            self._cron_field(parts[0], 0, 59),
            self._cron_field(parts[1], 0, 23),
            self._cron_field(parts[2], 1, 31),
            self._cron_field(parts[3], 1, 12),
            self._cron_field(parts[4], 0, 6),
        )

    # -- cron helpers --------------------------------------------------------

    @staticmethod
    def _cron_field(field: str, low: int, high: int) -> set[int]:
        values: set[int] = set()
        for part in field.split(","):
            step = 1
            if "/" in part:
                part, step_s = part.split("/", 1)
                step = int(step_s)
            if part == "*":
                start, end = low, high
            elif "-" in part:
                a, b = part.split("-", 1)
                start, end = int(a), int(b)
            else:
                start = end = int(part)
            values.update(range(start, end + 1, step))
        return values

    def _matches(self, fields: tuple[set[int], ...], moment: datetime) -> bool:
        minute_set, hour_set, dom_set, month_set, dow_set = fields
        if moment.minute not in minute_set or moment.hour not in hour_set:
            return False
        if moment.month not in month_set:
            return False
        # Standard cron semantics: dom and dow combine with OR when both are restricted.
        dom_ok = moment.day in dom_set
        dow_ok = (moment.weekday() + 1) % 7 in dow_set  # cron: 0=Sunday
        if dom_set == set(range(1, 32)):
            return dow_ok
        if dow_set == set(range(7)):
            return dom_ok
        return dom_ok or dow_ok

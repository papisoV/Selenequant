from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Iterable

from selene_space_types.stock import DailyReport, HistoryPoint

if sys.platform == "win32":
    import msvcrt

    def _lock_file(handle) -> None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_file(handle) -> None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_file(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    def _unlock_file(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

# "close" is intentionally excluded: HistoryPoint types it as `float | None`
# because a failed fetch legitimately records close=None (see records_from_report).
# Treating it as required here would silently discard those legitimate rows.
REQUIRED_HISTORY_FIELDS = ("date", "symbol", "score", "stance", "risk_level")


def load_history(path: str | Path) -> list[HistoryPoint]:
    history_path = Path(path)
    if not history_path.exists():
        return []
    return _parse_history_lines(history_path.read_text(encoding="utf-8").splitlines())


def _parse_history_lines(lines: Iterable[str]) -> list[HistoryPoint]:
    records: list[HistoryPoint] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            records.append(HistoryPoint(**_filter_history(raw)))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return records


def _dedupe_by_symbol_and_date(records: list[HistoryPoint]) -> list[HistoryPoint]:
    by_key: dict[tuple[str, str], HistoryPoint] = {}
    for record in records:
        by_key[(record.symbol, record.date)] = record
    return sorted(by_key.values(), key=lambda item: (item.date, item.symbol))


def attach_history(report: DailyReport, records: Iterable[HistoryPoint], max_points: int = 30) -> DailyReport:
    merged = _dedupe_by_symbol_and_date(list(records) + records_from_report(report))

    by_symbol: dict[str, list[HistoryPoint]] = {}
    for point in merged:
        by_symbol.setdefault(point.symbol, []).append(point)

    trimmed = {
        symbol: sorted(points, key=lambda item: item.date)[-max_points:]
        for symbol, points in by_symbol.items()
    }
    return report.model_copy(update={"history": trimmed})


def append_history(path: str | Path, report: DailyReport) -> None:
    history_path = Path(path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.touch(exist_ok=True)
    lock_path = history_path.with_name(history_path.name + ".lock")
    with lock_path.open("w", encoding="utf-8") as lock_handle:
        _lock_file(lock_handle)
        try:
            existing = _parse_history_lines(history_path.read_text(encoding="utf-8").splitlines())
            merged = _dedupe_by_symbol_and_date(existing + records_from_report(report))
            # Write to a temp file and atomically replace the target so a crash or
            # kill mid-write can never leave history.jsonl empty or half-written --
            # readers always see either the old file or the fully-written new one.
            tmp_path = history_path.with_name(history_path.name + ".tmp")
            tmp_path.write_text(
                "".join(
                    json.dumps(record.model_dump(), ensure_ascii=False, sort_keys=True) + "\n"
                    for record in merged
                ),
                encoding="utf-8",
            )
            for attempt in range(10):
                try:
                    os.replace(tmp_path, history_path)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        finally:
            _unlock_file(lock_handle)


def records_from_report(report: DailyReport) -> list[HistoryPoint]:
    report_date = report.generated_at.date().isoformat()
    records: list[HistoryPoint] = []
    for analysis in report.analyses:
        records.append(
            HistoryPoint(
                date=report_date,
                symbol=analysis.asset.symbol,
                close=analysis.snapshot.last_close if analysis.snapshot.rows > 0 else None,
                score=analysis.signal.score,
                stance=analysis.signal.stance,
                risk_level=analysis.signal.risk_level,
                currency=analysis.position.currency if analysis.position else analysis.snapshot.currency,
                change_pct=analysis.snapshot.change_pct,
                market_value=analysis.position.market_value if analysis.position else None,
                day_pnl=analysis.position.day_pnl if analysis.position else None,
                unrealized_pnl=analysis.position.unrealized_pnl if analysis.position else None,
                unrealized_pnl_pct=analysis.position.unrealized_pnl_pct if analysis.position else None,
                benchmark_symbol=analysis.benchmark.symbol if analysis.benchmark else None,
                relative_return_20d=analysis.benchmark.relative_return_20d if analysis.benchmark else None,
                relative_return_60d=analysis.benchmark.relative_return_60d if analysis.benchmark else None,
                latest_data_date=analysis.freshness.latest_date if analysis.freshness else analysis.snapshot.end_date,
                data_age_days=analysis.freshness.age_days if analysis.freshness else None,
                freshness_status=analysis.freshness.status if analysis.freshness else None,
            )
        )
    return records


def _filter_history(raw: dict) -> dict:
    allowed = set(HistoryPoint.model_fields.keys())
    filtered = {key: raw[key] for key in allowed if key in raw}
    missing_or_null = [field for field in REQUIRED_HISTORY_FIELDS if filtered.get(field) is None]
    if missing_or_null:
        raise ValueError(f"history row missing required fields: {missing_or_null}")
    return filtered

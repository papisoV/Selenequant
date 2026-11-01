"""Event-Kline attribution tool — align TG events with forward returns.

Reads tg_events from macro_events.db (raw SQL) and klines via
``KlineDB.get_klines()``.  For each event it finds the entry close (last
kline at or before the event), then measures forward returns at T+1h/4h/24h,
aggregated per (event_type, alert_level) bucket.

A gap guard rejects samples where the next kline is farther than 2× the
horizon away, so sparse data yields "n=0" instead of misleading returns.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from selene_space_types.risk import RiskLevel

from selene_space_agent.tools.base import BaseTool, ToolResult
from selene_space_core.kline_paths import default_klines_db_path

_DEFAULT_MACRO_DB = "data/macro_events.db"
_DEFAULT_KLINES_DB = str(default_klines_db_path())
_HOUR_MS = 3_600_000


def _load_events(db_path: str | Path, days: int) -> list[dict] | None:
    """Return tg_events rows in window; None if DB file missing."""
    path = Path(db_path)
    if not path.exists():
        return None
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        now_ms = pd.Timestamp.now(tz=None).value // 10**6
        cutoff = now_ms - days * 86_400_000
        rows = conn.execute(
            "SELECT timestamp, event_type, alert_level FROM tg_events "
            "WHERE timestamp >= ? ORDER BY timestamp ASC",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


class EventKlineAttributionTool(BaseTool):
    def __init__(
        self,
        macro_db_path: str | Path = _DEFAULT_MACRO_DB,
        klines_db_path: str | Path = _DEFAULT_KLINES_DB,
    ) -> None:
        self._macro_db_path = macro_db_path
        self._klines_db_path = klines_db_path

    @property
    def name(self) -> str:
        return "event_kline_attribution"

    @property
    def description(self) -> str:
        return (
            "Compute average forward kline returns at T+1h/4h/24h after past "
            "TG events, grouped by event type and alert level."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Kline symbol, e.g. BTCUSDT",
                },
                "days": {
                    "type": "integer",
                    "description": "Event lookback in days (default 30)",
                },
                "horizons": {
                    "type": "array",
                    "description": "Forward horizons in hours (default [1, 4, 24])",
                },
            },
            "required": ["symbol"],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        symbol = args.get("symbol")
        if not symbol:
            return ToolResult(output="", error="Missing required parameter: symbol")

        try:
            days = max(1, int(args.get("days", 30)))
        except (TypeError, ValueError):
            days = 30
        horizons_raw = args.get("horizons") or [1, 4, 24]
        try:
            horizons = [max(1, int(h)) for h in horizons_raw]
        except (TypeError, ValueError):
            horizons = [1, 4, 24]

        # Imported lazily at call time so tests can monkeypatch the module attr.
        from selene_space_market import kline_db as kline_db_mod

        events = _load_events(self._macro_db_path, days)
        if events is None:
            return ToolResult(
                output=f"macro_events.db not found at '{self._macro_db_path}'"
            )
        if not events:
            return ToolResult(output=f"No events in last {days}d")

        df: pd.DataFrame | None = None
        try:
            db = kline_db_mod.KlineDB(self._klines_db_path)
            df = db.get_klines(
                exchange="binance",
                market_type="spot",
                symbol=symbol,
                interval="1h",
            )
        except Exception:
            df = None

        ts_series: list[int] = []
        close_series: list[float] = []
        if (
            df is not None
            and len(df) > 0
            and {"timestamp", "close"}.issubset(df.columns)
        ):
            ts_series = [int(t) for t in df["timestamp"].tolist()]
            close_series = [float(c) for c in df["close"].tolist()]

        # buckets[(event_type, alert_level)][horizon] → list[float|None]
        buckets: dict[tuple[str, str], dict[int, list[float | None]]] = {}
        for ev in events:
            etype = str(ev.get("event_type", "?") or "?").upper()
            alevel = str(ev.get("alert_level", "?") or "?").upper()
            bucket = buckets.setdefault((etype, alevel), {h: [] for h in horizons})

            event_ts = int(ev["timestamp"])
            entry_close: float | None = None
            entry_ts: int | None = None
            for ts, close in zip(ts_series, close_series):
                if ts <= event_ts:
                    entry_close = close
                    entry_ts = ts
                else:
                    break

            for h in horizons:
                sample: float | None = None
                if entry_close is not None and entry_ts is not None:
                    # Exit = first kline strictly after the entry bar, within
                    # event_ts + h (plus one extra h as gap-guard allowance).
                    window_end = event_ts + 2 * h * _HOUR_MS
                    exit_close = None
                    exit_ts: int | None = None
                    for ts, close in zip(ts_series, close_series):
                        if ts > entry_ts and ts <= window_end:
                            exit_close = close
                            exit_ts = ts
                            break
                    if exit_close is not None and exit_ts is not None:
                        if exit_ts - event_ts > 2 * h * _HOUR_MS:
                            sample = None
                        else:
                            sample = (exit_close - entry_close) / entry_close
                bucket[h].append(sample)

        lines = [f"Event attribution for {symbol} (last {days}d):"]
        for (etype, alevel), per_horizon in sorted(buckets.items()):
            n_matched = sum(
                1 for lst in per_horizon.values() for s in lst if s is not None
            )
            lines.append(f"[{etype} {alevel}] n={n_matched}")
            for h in horizons:
                lst = per_horizon[h]
                valid = [s for s in lst if s is not None]
                if valid:
                    avg = sum(valid) / len(valid)
                    lines.append(f"T+{h}h: n={len(valid)} avg={avg:+.4f}")
                else:
                    lines.append(f"T+{h}h: n=0")
        return ToolResult(output="\n".join(lines))

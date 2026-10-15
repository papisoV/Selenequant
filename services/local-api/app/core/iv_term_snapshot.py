"""Daily IV term-structure snapshot: JSONL append on first successful tick.

Why: IV/HV bubble rule (advanced_rules.py IvHvBubbleRule) consumes spot ATM
IV; the options poller (main.py _options_poll_loop) refreshes the chain every
5 minutes but discards the term structure — per-expiry summaries exist only in
memory. Persisting one snapshot per currency per day accumulates the history
needed for any future IV term-structure Question (contango/backwardation
regime, 25d skew) without re-fetching history from Deribit.

Shape: one JSON line per (utc_date, currency) with the full expiry ladder.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_IV_DIR = Path.home() / ".selenespace" / "iv_term"


def snapshot_path(currency: str) -> Path:
    return _IV_DIR / f"{currency.lower()}_iv_term.jsonl"


def build_snapshot(
    currency: str, summaries: Iterable[Any], now_ms: int | None = None
) -> dict[str, Any] | None:
    """Full expiry ladder for one currency; None when no positive-IV rows."""
    now_ms = (
        now_ms
        if now_ms is not None
        else int(datetime.now(timezone.utc).timestamp() * 1000)
    )
    rows = []
    for s in summaries:
        if s.base_currency != currency.upper():
            continue
        ivs = [iv for iv in (s.call_iv_atm, s.put_iv_atm) if iv and iv > 0]
        if not ivs:
            continue
        rows.append(
            {
                "days_to_expiry": float(s.days_to_expiry),
                "expiration_timestamp": int(s.expiration_timestamp),
                "call_iv_atm": float(s.call_iv_atm),
                "put_iv_atm": float(s.put_iv_atm),
                "atm_iv": sum(ivs) / len(ivs),
                "put_call_ratio": float(s.put_call_ratio),
                "max_pain": float(s.max_pain),
                "total_call_oi": float(s.total_call_oi),
                "total_put_oi": float(s.total_put_oi),
            }
        )
    if not rows:
        return None
    rows.sort(key=lambda r: r["days_to_expiry"])
    return {
        "ts": now_ms,
        "utc_date": datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
        .date()
        .isoformat(),
        "currency": currency.upper(),
        "expiries": rows,
    }


def append_snapshot(snapshot: dict[str, Any]) -> bool:
    """Append one line; skip when utc_date+currency already present.

    Reads the file tail once per call — fine at 1 write/day. Returns True
    when a line was appended.
    """
    path = snapshot_path(snapshot["currency"])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        prev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (
                        prev.get("utc_date") == snapshot["utc_date"]
                        and prev.get("currency") == snapshot["currency"]
                    ):
                        return False
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot) + "\n")
        return True
    except OSError:
        logger.warning("iv term snapshot append failed: %s", path, exc_info=True)
        return False

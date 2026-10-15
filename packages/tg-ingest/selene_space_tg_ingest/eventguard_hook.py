"""EventGuard hook builder shared by the news_listen / tg_listen entrypoints.

Both entrypoints push real-time signal factors into selene's macro_events.db
via TGSource.ingest_llm_factor. This module is the single source of truth for
that bridge:

- news_listen.py previously called a non-existent `EventGuard.ingest_tg_factor`
  method (EventGuard has no such method; the correct API is
  `TGSource.ingest_llm_factor`). The error was swallowed, so the hook never
  worked.
- Keeping the bridge in one module prevents the two entrypoints from drifting
  again into divergent (broken) hook logic.

Uses ingest_llm_factor (not ingest_message) so the LLM-produced
event_type/magnitude/urgency are passed through directly — no keyword
reclassification. Keyword reclassification was the legacy path and wasted
the LLM output.

selene_space_alpha is NOT a hard dep of tg-ingest: it's lazily imported only
when a hook is actually requested, so the package installs/imports cleanly
without alpha-core.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def build_eventguard_hook(macro_db: str | Path) -> Callable[[dict], None] | None:
    """Build an on_signal_factor callback that ingests real signals into EventGuard.

    Args:
        macro_db: Path to macro_events.db.

    Returns:
        The hook callable, or None if selene_space_alpha is unavailable
        (caller then skips the hook and the deduper).
    """
    try:
        from selene_space_alpha.event_guard.tg_source import TGSource
    except ImportError:
        logger.warning("selene_space_alpha not available, EventGuard hook disabled")
        return None

    macro_db_path = Path(macro_db)

    def _hook(factor: dict) -> None:
        try:
            ts_str = factor.get("created_at")
            if ts_str is None:
                logger.debug("skip EventGuard hook: no created_at on factor %s", factor.get("message_id"))
                return
            try:
                dt = datetime.fromisoformat(str(ts_str).replace(" ", "T"))
                ts_ms = int(dt.timestamp() * 1000)
            except (ValueError, TypeError):
                logger.debug("skip EventGuard hook: bad ts %r", ts_str)
                return

            # Skip conditions (mirror classify_message in TGSource):
            # - is_signal=False: did not pass the EVENT gate. signal_engine
            #   _compute_is_signal is event gating (event_type valid + magnitude
            #   > 0 + confidence >= 0.3); direction is NOT required (US-002). So
            #   this is "the event passed the gate", NOT a directional-signal
            #   assumption — neutral events (direction=0) that passed the gate
            #   are valid EventGuard inputs (event-sensor semantics).
            # - reasoning starts with "noise:": explicit noise tag
            is_signal = factor.get("is_signal", True)
            if not is_signal:
                return
            reasoning = (factor.get("reasoning") or "")
            if reasoning.lower().startswith("noise:"):
                return

            llm_event_type = factor.get("event_type") or ""
            magnitude = float(factor.get("magnitude") or 0.0)
            urgency = float(factor.get("urgency") or 0.0)
            symbols_raw = factor.get("symbols") or ""
            # symbols is a JSON list string like '["BTC","ETH"]' or '["*"]'
            try:
                sym_list = json.loads(symbols_raw) if symbols_raw else []
            except (ValueError, TypeError):
                sym_list = []
            symbols = sym_list[0] if sym_list else "*"

            src = TGSource(db_path=macro_db_path)
            alert = src.ingest_llm_factor(
                timestamp_ms=ts_ms,
                message=reasoning or "",
                llm_event_type=llm_event_type,
                magnitude=magnitude,
                urgency=urgency,
                symbols=symbols,
            )
            if alert is not None:
                logger.info(
                    "EventGuard ingested LLM factor: ts=%d type=%s level=%s mult=%.2f",
                    ts_ms, alert.event_type, alert.alert_level, alert.multiplier,
                )
        except Exception as e:
            logger.warning("EventGuard hook failed for msg %s: %s", factor.get("message_id"), e)

    return _hook

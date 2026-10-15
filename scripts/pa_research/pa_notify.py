"""P4-A: PA diagnosis brief rendering + env-gated notification.

render_pa_brief is a pure function (no I/O) so tests can pin the format.
maybe_notify_pa gates on SELENE_PA_NOTIFY_ENABLED=1 and hands the brief to
selene_space_pulse.notify.send_trade_alert (fire-and-forget, never raises).
Callers: scripts/run_pa_diagnosis.py (after record persistence) and
scripts/keltner_revalidate.py (after verdict lands).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_ENV_FLAG = "SELENE_PA_NOTIFY_ENABLED"


def render_pa_brief(record: dict[str, Any]) -> str:
    """Compress a PA AnalysisRecord dict into a notification brief.

    Pure: reads only the passed dict, no I/O. Robust to missing keys —
    every field degrades to a placeholder rather than raising, so a
    malformed record can never break the notification path.
    """
    meta = record.get("meta") if isinstance(record, dict) else None
    meta = meta if isinstance(meta, dict) else {}
    stage1_raw = record.get("stage1") if isinstance(record, dict) else None
    stage1 = stage1_raw if isinstance(stage1_raw, dict) else {}
    stage2_raw = record.get("stage2") if isinstance(record, dict) else None
    stage2 = stage2_raw if isinstance(stage2_raw, dict) else {}
    diag1 = stage1.get("diagnosis_or_decision") or {}
    diag2 = stage2.get("diagnosis_or_decision") or {}
    summary_raw = record.get("input_data") if isinstance(record, dict) else None
    summary = (
        summary_raw.get("summary") if isinstance(summary_raw, dict) else {}
    ) or {}

    symbol = summary.get("symbol", meta.get("tree_id", "?"))
    interval = summary.get("interval", "?")
    close = summary.get("last_close")
    state = diag1.get("state", "unknown")
    gate = diag1.get("gate_result", "unknown")
    outcome = diag2.get("terminal_outcome") or "wait"
    decision = diag2.get("decision") or outcome
    order = diag2.get("order")

    lines = [
        f"PA 诊断 {symbol} {interval}",
        f"state={state} gate={gate} → {decision} (terminal {outcome})",
    ]
    if isinstance(close, (int, float)):
        lines.append(f"last_close={close}")
    # next_period (P4-B) surfaces when present; absent stays absent — the
    # field is declared optional in the output contract.
    next_period = (diag2.get("decision_summary") or {}).get("next_period") or (
        diag2.get("next_period")
    )
    if isinstance(next_period, dict):
        direction = next_period.get("direction")
        if direction:
            parts = [f"next_period direction={direction}"]
            levels = next_period.get("watch_levels")
            if isinstance(levels, (list, tuple)) and levels:
                parts.append(f"watch={levels}")
            lines.append(" ".join(parts))
    if order:
        lines.append(f"order={json.dumps(order, ensure_ascii=False)}")
    reason = (diag2.get("decision_summary") or {}).get("reject_reason")
    if reason:
        lines.append(f"reject_reason={reason}")
    return "\n".join(lines)


def maybe_notify_pa(record: dict[str, Any]) -> list[str] | None:
    """Send the brief when SELENE_PA_NOTIFY_ENABLED=1; else no-op.

    Returns delivery statuses when notified, None when disabled. Never
    raises: notification is best-effort telemetry, not a critical path.
    """
    if os.environ.get(_ENV_FLAG, "").strip() != "1":
        return None
    try:
        from selene_space_pulse.notify import send_trade_alert

        statuses: list[str] = list(send_trade_alert(render_pa_brief(record)))
        return statuses
    except Exception:  # noqa: BLE001
        logger.exception("PA notify failed (non-fatal)")
        return None

"""Daily LLM usage ledger and the settings-driven spend ceiling.

Why tokens and not dollars
--------------------------
The provider reports real ``usage`` on every response, but this project has
no authoritative price table — ``TokenCostTracker`` falls back to a hardcoded
$0.25/M input + $1/M output guess. A dollar ceiling built on that guess would
print a confident number that is not the user's actual bill, which is exactly
the "visible falsehood" this work removes. Tokens are measured, so the cap is
counted in tokens; the settings page says so in the same breath.

Where the number lives
----------------------
``<repo-root>/data/llm_usage.json`` (``SELENE_LLM_USAGE_PATH`` overrides),
resolved from this module's location like ``kline_paths`` — never CWD-relative,
so the backend and any script agree on one file. The ledger is persisted
because an in-memory counter resets on restart, and a ceiling that a restart
silently lifts is not a ceiling.

Scope of the ceiling
--------------------
The cap gates **unattended** spend: helper calls (``llm_helper.call_llm``),
the PA two-stage diagnosis (heartbeat and the manual PA panel alike). An
interactive agent chat turn is metered but never blocked — cutting off the
person who is sitting there typing would be a worse failure than the overspend
it prevents. The settings UI states this carve-out next to the number instead
of leaving it implicit.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from selene_space_core.infra.atomic_write import atomic_write
from selene_space_core.kline_paths import repo_root

logger = logging.getLogger(__name__)

_FILE_NAME = "llm_usage.json"


def default_llm_usage_path() -> Path:
    """Resolve the ledger path (always absolute).

    ``SELENE_LLM_USAGE_PATH`` wins when set; otherwise ``<repo-root>/data/``.
    """
    env = os.environ.get("SELENE_LLM_USAGE_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(repo_root()) / "data" / _FILE_NAME


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _empty(day: str) -> dict[str, Any]:
    return {
        "day": day,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "calls": 0,
        "by_source": {},
    }


class LLMUsageLedger:
    """Accumulate per-UTC-day token usage and persist it across restarts.

    Every mutating path takes one lock: ``record`` can be reached from the
    event loop (helper calls) and from worker threads (the decision workflow
    runs ``wf.analyze`` via ``asyncio.to_thread``).
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else default_llm_usage_path()
        self._lock = threading.Lock()
        self._state: dict[str, Any] | None = None

    # -- state -------------------------------------------------------------

    def _load_locked(self) -> dict[str, Any]:
        """Read the file once; missing or corrupt starts a fresh day.

        A corrupt file is treated as "no data", not as "no cap": the cap comes
        from settings, so zeroed counters can only ever let *more* calls
        through than the user asked for — logged loudly so it is not silent.
        """
        if self._state is not None:
            return self._state
        day = _today()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._state = _empty(day)
            return self._state
        except Exception:
            logger.warning(
                "LLM usage ledger unreadable at %s — starting a fresh day",
                self._path,
                exc_info=True,
            )
            self._state = _empty(day)
            return self._state
        if not isinstance(raw, dict) or raw.get("day") != day:
            self._state = _empty(day)
            return self._state
        state = _empty(day)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "calls"):
            value = raw.get(key)
            if isinstance(value, int) and value >= 0:
                state[key] = value
        by_source = raw.get("by_source")
        if isinstance(by_source, dict):
            state["by_source"] = {
                str(k): v for k, v in by_source.items() if isinstance(v, int)
            }
        self._state = state
        return state

    def _persist_locked(self) -> None:
        assert self._state is not None
        try:
            atomic_write(self._path, json.dumps(self._state, indent=2))
        except Exception:
            # The in-memory count stays correct for this process; only the
            # restart guarantee is lost, and that must be visible.
            logger.warning(
                "Failed to persist LLM usage ledger at %s", self._path, exc_info=True
            )

    def _roll_locked(self) -> dict[str, Any]:
        state = self._load_locked()
        day = _today()
        if state.get("day") != day:
            self._state = _empty(day)
            self._persist_locked()
        assert self._state is not None
        return self._state

    # -- public ------------------------------------------------------------

    def record(
        self,
        *,
        source: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int | None = None,
        model: str = "",
    ) -> None:
        """Add one call's usage. Never raises — accounting must not break a call."""
        prompt = max(0, int(prompt_tokens or 0))
        completion = max(0, int(completion_tokens or 0))
        total = (
            max(0, int(total_tokens))
            if total_tokens is not None
            else prompt + completion
        )
        # A provider that reports only a total leaves the split at zero; the
        # day total still advances, which is what the ceiling reads.
        total = max(total, prompt + completion)
        with self._lock:
            state = self._roll_locked()
            state["prompt_tokens"] += prompt
            state["completion_tokens"] += completion
            state["total_tokens"] += total
            state["calls"] += 1
            by_source = state["by_source"]
            prev = by_source.get(source)
            entry: dict[str, Any] = (
                prev if isinstance(prev, dict) else {"calls": 0, "total_tokens": 0}
            )
            # ``model`` was previously accepted and dropped (an empty "models"
            # dict was created and never filled). Count it instead: attribution
            # to the model that actually spent the tokens is the point of a
            # per-source breakdown.
            models = dict(entry.get("models") or {})
            if model:
                models[model] = int(models.get(model, 0)) + total
            entry = {
                "calls": int(entry.get("calls", 0)) + 1,
                "total_tokens": int(entry.get("total_tokens", 0)) + total,
            }
            if models:
                entry["models"] = models
            by_source[source] = entry
            self._persist_locked()

    def snapshot(self) -> dict[str, Any]:
        """Today's counters (a copy — callers never mutate the ledger)."""
        with self._lock:
            state = self._roll_locked()
            copy: dict[str, Any] = json.loads(json.dumps(state))
            return copy

    def reset(self) -> None:
        """Zero today's counters (tests and an explicit user reset)."""
        with self._lock:
            self._state = _empty(_today())
            self._persist_locked()


_ledger: LLMUsageLedger | None = None
_ledger_lock = threading.Lock()


def get_ledger() -> LLMUsageLedger:
    """Process-wide ledger (lazily built so tests can point the env elsewhere)."""
    global _ledger
    with _ledger_lock:
        if _ledger is None:
            _ledger = LLMUsageLedger()
        return _ledger


def reset_ledger_for_tests(path: Path | None = None) -> LLMUsageLedger:
    """Replace the process ledger (tests only; keeps prod callers untouched)."""
    global _ledger
    with _ledger_lock:
        _ledger = LLMUsageLedger(path)
        return _ledger


def daily_cap(settings_service: Any = None) -> int:
    """Daily token ceiling from settings; 0 means no cap.

    A read failure resolves to 0 (no cap) rather than a guessed ceiling: the
    user's stated default on this machine is "no cap", and inventing one would
    silently stop their automated work.
    """
    try:
        if settings_service is None:
            from app.core.dependencies import get_settings_service

            settings_service = get_settings_service()
        if settings_service is None:
            return 0
        section = settings_service.get_llm_budget()
        return max(0, int(section.get("daily_tokens", 0) or 0))
    except Exception:
        logger.debug("LLM budget settings read failed", exc_info=True)
        return 0


def budget_state(settings_service: Any = None) -> dict[str, Any]:
    """Usage + ceiling + whether the ceiling has been reached, for API/UI."""
    snap = get_ledger().snapshot()
    cap = daily_cap(settings_service)
    used = int(snap.get("total_tokens", 0))
    exceeded = cap > 0 and used >= cap
    return {
        "day": snap.get("day"),
        "tokens_today": used,
        "prompt_tokens": snap.get("prompt_tokens", 0),
        "completion_tokens": snap.get("completion_tokens", 0),
        "calls_today": snap.get("calls", 0),
        "daily_tokens": cap,
        "exceeded": exceeded,
        "remaining": None if cap <= 0 else max(0, cap - used),
        "by_source": snap.get("by_source", {}),
    }


def budget_exceeded(settings_service: Any = None) -> bool:
    return bool(budget_state(settings_service)["exceeded"])


__all__ = [
    "LLMUsageLedger",
    "budget_exceeded",
    "budget_state",
    "daily_cap",
    "default_llm_usage_path",
    "get_ledger",
    "reset_ledger_for_tests",
]

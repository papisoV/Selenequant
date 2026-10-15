"""Resumable A-share backfill: exponential-backoff retry + periodic reconnect.

Original Selene implementation of the "retry + reconnect + resume" pattern
surveyed in public-reference (no public-reference code used — that project ships no
license). The fetch layer here is sync (akshare/ths/httpx); async endpoints
wrap calls with ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_PROGRESS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "ashare_backfill_state.json"
)

SleepFn = Callable[[float], None]


@dataclass(frozen=True)
class BackfillReport:
    """Outcome of one backfill run over a symbol list."""

    n_ok: int
    n_failed: int
    failed_symbols: list[str]
    elapsed: float
    retries_used: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_ok": self.n_ok,
            "n_failed": self.n_failed,
            "failed_symbols": list(self.failed_symbols),
            "elapsed": round(self.elapsed, 3),
            "retries_used": self.retries_used,
        }


def _load_progress(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"completed": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("ashare backfill progress unreadable, starting empty: %s", path)
        return {"completed": {}}
    if isinstance(data, dict) and isinstance(data.get("completed"), dict):
        return data
    logger.warning("ashare backfill progress malformed, starting empty: %s", path)
    return {"completed": {}}


def _save_progress(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def backfill(
    symbols: Iterable[str],
    fetch_fn: Callable[[str], pd.DataFrame],
    store_fn: Callable[[str, pd.DataFrame], str | None],
    *,
    max_retries: int = 3,
    base_delays: tuple[float, ...] = (2.0, 4.0, 8.0),
    reconnect_every: int = 200,
    reset_hook: Callable[[], None] | None = None,
    progress_path: str | Path = DEFAULT_PROGRESS_PATH,
    sleep_fn: SleepFn | None = None,
) -> BackfillReport:
    """Fetch and store each symbol with per-symbol retry/backoff.

    - retry: ``max_retries`` extra attempts after the first; waits
      ``base_delays[i]`` (clamped to the last entry) between attempts. Empty
      results count as failures so upstream flakiness never lands silently
      in the store.
    - reconnect: ``reset_hook()`` runs every ``reconnect_every`` attempted
      symbols, for fetchers that hold a session/connection. akshare opens a
      fresh connection per call, so the hook is optional here.
    - resume: each success persists ``{symbol: marker}`` (marker from
      ``store_fn``, e.g. MAX(date)) to ``progress_path``; on restart
      completed symbols are skipped — completed ranges are never re-fetched.
      Failed symbols ARE retried on restart.

    ``sleep_fn`` is injectable so tests never actually wait.
    """
    sleep = sleep_fn or time.sleep
    path = Path(progress_path)
    state = _load_progress(path)
    completed: dict[str, str | None] = dict(state["completed"])

    failed: list[str] = []
    retries_used = 0
    ok_count = 0
    attempted = 0
    started = time.perf_counter()

    for symbol in symbols:
        if symbol in completed:
            continue
        if (
            reset_hook is not None
            and reconnect_every > 0
            and attempted > 0
            and attempted % reconnect_every == 0
        ):
            reset_hook()
        attempted += 1

        result: pd.DataFrame | None = None
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                result = fetch_fn(symbol)
            except Exception as exc:
                last_error = exc
                result = None
            if result is not None and not result.empty:
                break
            result = None
            if attempt < max_retries:
                delay = (
                    base_delays[attempt]
                    if attempt < len(base_delays)
                    else base_delays[-1]
                )
                retries_used += 1
                sleep(delay)

        if result is None:
            reason = f"error: {last_error}" if last_error else "no data returned"
            logger.warning(
                "ashare backfill symbol failed after %d retries: %s (%s)",
                max_retries,
                symbol,
                reason,
            )
            failed.append(symbol)
            continue

        completed[symbol] = store_fn(symbol, result)
        ok_count += 1
        state["completed"] = completed
        _save_progress(path, state)

    return BackfillReport(
        n_ok=ok_count,
        n_failed=len(failed),
        failed_symbols=failed,
        elapsed=time.perf_counter() - started,
        retries_used=retries_used,
    )


def fetch_symbol_with_retry(
    fetch_fn: Callable[[str], pd.DataFrame],
    symbol: str,
    *,
    retries: int = 1,
    base_delay: float = 1.0,
    sleep_fn: SleepFn | None = None,
) -> tuple[pd.DataFrame, Exception | None]:
    """Daily-sync hardening: one inline retry on error/empty per symbol.

    Returns ``(df, last_error)``. ``df`` is empty when every attempt came
    back empty; ``last_error`` is None unless every attempt raised. Callers
    must aggregate symbols that still failed and log the explicit list —
    silent skips are forbidden.
    """
    sleep = sleep_fn or time.sleep
    empty = pd.DataFrame()
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            candidate = fetch_fn(symbol)
        except Exception as exc:
            last_error = exc
            candidate = empty
        if candidate is not None and not candidate.empty:
            return candidate, None
        if attempt < retries:
            sleep(base_delay * (attempt + 1))
    return empty, last_error

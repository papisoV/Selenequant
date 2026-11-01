"""Strategy trust tracker — graduated trust levels per strategy.

Backs the Phase 4.2 trust escalation ladder:
  observe → shadow → confirm → limited → trusted

Each strategy has a persistent trust record. `record_strategy_outcome()`
is the only mutation path; on 30 consecutive daily successes in `limited`
mode, auto-upgrades to `trusted`. Failures are demotion-driven: after
`_DEMOTION_FAILURES` consecutive failures the strategy drops one level
(trusted→limited→confirm→shadow→observe) and enters a re-entry cooldown
during which auto-promotion and explicit upgrades are blocked. `observe`
is the floor — never demotes below it. Explicit downgrades via
`set_trust_level()` remain available at any time.

Storage: SQLite at ~/.selenespace/trust/trust_state.db (separate from
the main migrations DB and the live-state DB).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from selene_space_exchange.execution.mode_guard import _TRUST_MODE_MAP

logger = logging.getLogger(__name__)

_DEFAULT_TRUST_DIR = Path.home() / ".selenespace" / "trust"
_PROMOTION_STREAK = 30  # 30 consecutive daily successes → trusted
_DEMOTION_FAILURES = 5  # 5 consecutive failures → demote one level
_REENTRY_COOLDOWN_DAYS = 7  # cooldown after demotion before re-promotion is allowed
_REENTRY_COOLDOWN_SECONDS = _REENTRY_COOLDOWN_DAYS * 24 * 60 * 60

# Low → high; demotion walks one step down this ladder.
_TRUST_LADDER = ["observe", "shadow", "confirm", "limited", "trusted"]


@dataclass(frozen=True)
class TrustRecord:
    """Immutable snapshot of a strategy's trust state."""

    strategy_id: str
    level: str  # one of observe/shadow/confirm/limited/trusted
    consecutive_successes: int
    limited_run_days: int
    last_outcome_at: float | None
    updated_at: float
    consecutive_failures: int = 0
    demoted_at: float | None = None
    reentry_eligible_at: float | None = None  # cooldown end; None = no active cooldown


class StrategyTrustTracker:
    """SQLite-backed trust level store with auto-promotion logic."""

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            self._dir = _DEFAULT_TRUST_DIR
            self._db_path = self._dir / "trust_state.db"
        else:
            self._db_path = db_path
            self._dir = db_path.parent
        self._dir.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS trust_state (
                    strategy_id TEXT PRIMARY KEY,
                    level TEXT NOT NULL,
                    consecutive_successes INTEGER NOT NULL DEFAULT 0,
                    limited_run_days INTEGER NOT NULL DEFAULT 0,
                    last_outcome_at REAL,
                    updated_at REAL NOT NULL,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    demoted_at REAL,
                    reentry_eligible_at REAL
                )
                """
            )
            # Idempotent migration for DBs created before the demotion columns.
            self._ensure_column(con, "consecutive_failures", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(con, "demoted_at", "REAL")
            self._ensure_column(con, "reentry_eligible_at", "REAL")

    def _ensure_column(self, con: sqlite3.Connection, name: str, decl: str) -> None:
        existing = {row[1] for row in con.execute("PRAGMA table_info(trust_state)")}
        if name not in existing:
            con.execute(f"ALTER TABLE trust_state ADD COLUMN {name} {decl}")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))

    def get_trust_level(self, strategy_id: str) -> str:
        row = self._load(strategy_id)
        return row.level if row is not None else "observe"

    def get_record(self, strategy_id: str) -> TrustRecord:
        row = self._load(strategy_id)
        if row is not None:
            return row
        ts = time.time()
        return TrustRecord(
            strategy_id=strategy_id,
            level="observe",
            consecutive_successes=0,
            limited_run_days=0,
            last_outcome_at=None,
            updated_at=ts,
        )

    def record_strategy_outcome(
        self,
        strategy_id: str,
        success: bool,
        *,
        ts: float | None = None,
    ) -> TrustRecord:
        ts = ts if ts is not None else time.time()
        current = self.get_record(strategy_id)
        level = current.level
        demoted_at = current.demoted_at
        reentry_eligible_at = current.reentry_eligible_at
        in_cooldown = reentry_eligible_at is not None and ts < reentry_eligible_at

        if success:
            new_consecutive = current.consecutive_successes + 1
            new_failures = 0
            new_limited_days = current.limited_run_days + 1 if level == "limited" else current.limited_run_days
            if level == "limited" and new_consecutive >= _PROMOTION_STREAK and not in_cooldown:
                level = "trusted"
                logger.info(
                    "trust tracker: %s promoted limited→trusted after %d consecutive successes",
                    strategy_id,
                    new_consecutive,
                )
        else:
            new_consecutive = 0
            new_limited_days = current.limited_run_days
            new_failures = current.consecutive_failures + 1
            if new_failures >= _DEMOTION_FAILURES and level != "observe":
                level = _TRUST_LADDER[_TRUST_LADDER.index(level) - 1]
                demoted_at = ts
                reentry_eligible_at = ts + _REENTRY_COOLDOWN_SECONDS
                new_failures = 0
                logger.info(
                    "trust tracker: %s demoted to %s after %d consecutive failures; "
                    "re-entry eligible after %s",
                    strategy_id,
                    level,
                    _DEMOTION_FAILURES,
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(reentry_eligible_at)),
                )

        self._save(
            strategy_id=strategy_id,
            level=level,
            consecutive_successes=new_consecutive,
            consecutive_failures=new_failures,
            limited_run_days=new_limited_days,
            last_outcome_at=ts,
            updated_at=ts,
            demoted_at=demoted_at,
            reentry_eligible_at=reentry_eligible_at,
        )
        return TrustRecord(
            strategy_id=strategy_id,
            level=level,
            consecutive_successes=new_consecutive,
            consecutive_failures=new_failures,
            limited_run_days=new_limited_days,
            last_outcome_at=ts,
            updated_at=ts,
            demoted_at=demoted_at,
            reentry_eligible_at=reentry_eligible_at,
        )

    def set_trust_level(self, strategy_id: str, level: str) -> None:
        if level not in _TRUST_MODE_MAP:
            raise ValueError(
                f"Unknown trust level {level!r}; expected one of {sorted(_TRUST_MODE_MAP.keys())}"
            )
        current = self.get_record(strategy_id)
        ts = time.time()
        in_cooldown = current.reentry_eligible_at is not None and ts < current.reentry_eligible_at
        if in_cooldown and _TRUST_LADDER.index(level) > _TRUST_LADDER.index(current.level):
            raise ValueError(
                f"Cannot upgrade {strategy_id} {current.level}→{level}: "
                f"in re-entry cooldown until {current.reentry_eligible_at:.0f}"
            )
        level_changed = level != current.level
        self._save(
            strategy_id=strategy_id,
            level=level,
            consecutive_successes=0 if level_changed else current.consecutive_successes,
            consecutive_failures=0 if level_changed else current.consecutive_failures,
            limited_run_days=0 if level != "limited" else current.limited_run_days,
            last_outcome_at=current.last_outcome_at,
            updated_at=ts,
            # Explicit level change is a fresh operator decision: clear cooldown history.
            demoted_at=None if level_changed else current.demoted_at,
            reentry_eligible_at=None if level_changed else current.reentry_eligible_at,
        )

    def _load(self, strategy_id: str) -> TrustRecord | None:
        with self._connect() as con:
            cur = con.execute(
                "SELECT strategy_id, level, consecutive_successes, limited_run_days, "
                "last_outcome_at, updated_at, consecutive_failures, demoted_at, "
                "reentry_eligible_at FROM trust_state WHERE strategy_id = ?",
                (strategy_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return TrustRecord(
            strategy_id=row[0],
            level=row[1],
            consecutive_successes=row[2],
            limited_run_days=row[3],
            last_outcome_at=row[4],
            updated_at=row[5],
            consecutive_failures=row[6],
            demoted_at=row[7],
            reentry_eligible_at=row[8],
        )

    def _save(
        self,
        *,
        strategy_id: str,
        level: str,
        consecutive_successes: int,
        consecutive_failures: int,
        limited_run_days: int,
        last_outcome_at: float | None,
        updated_at: float,
        demoted_at: float | None,
        reentry_eligible_at: float | None,
    ) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO trust_state
                (strategy_id, level, consecutive_successes, consecutive_failures,
                 limited_run_days, last_outcome_at, updated_at, demoted_at, reentry_eligible_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_id,
                    level,
                    consecutive_successes,
                    consecutive_failures,
                    limited_run_days,
                    last_outcome_at,
                    updated_at,
                    demoted_at,
                    reentry_eligible_at,
                ),
            )

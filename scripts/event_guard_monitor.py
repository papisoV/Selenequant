"""EventGuard monitoring script.

Periodically checks guard multipliers and logs strategy signal activity.
Reports guard cost as (1 - avg_multiplier) when signals occur during events.

Usage:
    python scripts/event_guard_monitor.py
    python scripts/event_guard_monitor.py --interval 60
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))

from selene_space_alpha.event_guard import EventGuard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("guard_monitor")

SYMBOLS = ["BTC", "ETH", "SOL"]
DEFAULT_DB_PATH = PROJECT_ROOT / "services" / "local-api" / "data" / "macro_events.db"


class GuardMonitor:
    def __init__(self, db_path: str | Path, interval: int = 60) -> None:
        self._guard = EventGuard(macro_db_path=db_path)
        self._interval = interval
        self._history: list[dict] = []

    def snapshot(self) -> dict:
        now = datetime.now(timezone.utc)
        now_ts = int(now.timestamp() * 1000)
        result = {
            "time": now.isoformat(),
            "vix_regime": self._guard.get_vix_regime(now_ts),
            "symbols": {},
        }
        for sym in SYMBOLS:
            mult = self._guard.get_position_multiplier(sym, now_ts)
            alerts = self._guard.get_active_alerts(sym, now_ts)
            result["symbols"][sym] = {
                "multiplier": round(mult, 4),
                "alerts": [
                    {
                        "type": a.event_type,
                        "level": a.level.name,
                        "mult": a.multiplier,
                        "reason": a.reason,
                    }
                    for a in alerts
                ],
            }
        return result

    def run(self, rounds: int | None = None) -> None:
        logger.info("EventGuard Monitor started (interval=%ds)", self._interval)
        count = 0
        try:
            while rounds is None or count < rounds:
                snap = self.snapshot()
                self._history.append(snap)
                self._print_status(snap)
                count += 1
                if rounds is not None and count >= rounds:
                    break
                time.sleep(self._interval)
        except KeyboardInterrupt:
            pass
        self._print_summary()

    def _print_status(self, snap: dict) -> None:
        vix = snap["vix_regime"]
        parts = []
        for sym, info in snap["symbols"].items():
            mult = info["multiplier"]
            if mult < 1.0:
                alert_strs = [f"{a['type']}:{a['level']}" for a in info["alerts"]]
                parts.append(f"{sym}={mult:.2f}[{','.join(alert_strs)}]")
            else:
                parts.append(f"{sym}=1.00")
        logger.info("VIX=%s | %s", vix, " | ".join(parts))

    def _print_summary(self) -> None:
        if not self._history:
            return
        total_checks = len(self._history)
        guarded_checks = 0
        mult_sum = 0.0
        for snap in self._history:
            for sym, info in snap["symbols"].items():
                mult_sum += info["multiplier"]
                if info["multiplier"] < 1.0:
                    guarded_checks += 1

        total_entries = total_checks * len(SYMBOLS)
        avg_mult = mult_sum / total_entries if total_entries else 1.0
        guard_cost = 1.0 - avg_mult

        print("\n" + "=" * 50)
        print("EventGuard Monitor Summary")
        print("=" * 50)
        print(f"  Total checks: {total_checks}")
        print(f"  Total symbol-checks: {total_entries}")
        print(f"  Guarded (mult<1.0): {guarded_checks} ({guarded_checks/total_entries*100:.1f}%)")
        print(f"  Avg multiplier: {avg_mult:.4f}")
        print(f"  Guard cost: {guard_cost*100:.2f}%")
        print("  Expected guard cost: ~9.5%")
        print("=" * 50)


def main() -> None:
    parser = argparse.ArgumentParser(description="EventGuard monitoring")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--interval", type=int, default=60, help="Check interval in seconds")
    parser.add_argument("--rounds", type=int, default=None, help="Number of rounds (default: infinite)")
    args = parser.parse_args()

    monitor = GuardMonitor(db_path=args.db, interval=args.interval)
    monitor.run(rounds=args.rounds)


if __name__ == "__main__":
    main()

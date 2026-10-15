"""VIX regime source — uses VIX daily data to modulate position sizing.

Key finding (V10 validation, 3220 trades):
  - VIX越高策略表现越好 (Low<15 WR=65.4%, High 20-30 WR=69.9%, Crisis>30 WR=74.1%)
  - V5.2是短线动量策略，波动=机会
  - 简单VIX减仓只有负面效果（损失526 PnL）
  - 但High VIX + Macro Event叠加时WR降至66.8% (vs High VIX Normal 73.0%)
  - 因此VIX源只在High/Crisis VIX + Event叠加时额外降权

VIX regime → alert multiplier:
  Low VIX (<15): 1.0 (不做调整，虽然机会少但减仓更差)
  Mid VIX (15-20): 1.0 (正常)
  High VIX (20-30) + no event: 1.0 (高波动=高机会)
  High VIX (20-30) + event: 0.85 (叠加降权，WR 66.8% vs 73.0%)
  Crisis VIX (>30) + no event: 1.0 (极端波动=极端机会)
  Crisis VIX (>30) + event: 0.75 (最危险组合)
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import numpy as np

from .models import EventAlert

logger = logging.getLogger(__name__)

# VIX thresholds
VIX_LOW = 15.0
VIX_HIGH = 20.0
VIX_CRISIS = 30.0

# VIX regime → position multiplier (used when no event is active)
# All 1.0: V5.2 profits at all VIX levels, simple VIX reduction hurts (-526 PnL)
_VIX_REGIME_MULT = {
    "low": 1.0,
    "mid": 1.0,
    "high": 1.0,
    "crisis": 1.0,
}

# VIX regime + event overlap → extra reduction
# Only applied when High/Crisis VIX coincides with a macro event
# Data: High VIX+Event WR=66.8% vs High VIX Normal=73.0%
_VIX_EVENT_OVERLAP_MULT = {
    "high_event": 0.85,
    "crisis_event": 0.75,
}


class VixSource:
    """Generates VIX regime alerts from macro_events.db vix_daily table."""

    def __init__(self, db_path: str | Path = "data/macro_events.db") -> None:
        self._db_path = Path(db_path)
        self._cache_ts: int = 0
        self._vix_data: list[tuple[int, float]] = []  # (timestamp_ms, close)
        self._cache_ttl_ms = 3600_000  # Refresh every hour

    def _ensure_cache(self, now_ts: int) -> None:
        """Refresh VIX cache from DB if stale."""
        if now_ts - self._cache_ts < self._cache_ttl_ms and self._vix_data:
            return
        if not self._db_path.exists():
            logger.warning("macro_events.db not found at %s for VIX source", self._db_path)
            return
        try:
            conn = sqlite3.connect(str(self._db_path))
            cur = conn.execute(
                "SELECT timestamp, close FROM vix_daily ORDER BY timestamp ASC"
            )
            self._vix_data = [(int(r[0]), float(r[1])) for r in cur.fetchall()]
            conn.close()
            self._cache_ts = now_ts
            logger.debug("VixSource cache refreshed: %d days", len(self._vix_data))
        except Exception as e:
            logger.error("VixSource cache refresh failed: %s", e)

    def get_vix_at(self, now_ts: int) -> float | None:
        """Get the most recent VIX close for the given timestamp."""
        self._ensure_cache(now_ts)
        if not self._vix_data:
            return None

        # Binary search for most recent VIX <= now_ts
        ts_arr = np.array([d[0] for d in self._vix_data])
        idx = int(np.searchsorted(ts_arr, now_ts, side="right")) - 1
        if idx >= 0 and idx < len(self._vix_data):
            vix_ts = self._vix_data[idx][0]
            # Only use VIX from within last 3 days
            if now_ts - vix_ts < 3 * 86400000:
                return self._vix_data[idx][1]
        return None

    def get_regime(self, now_ts: int) -> str:
        """Classify current VIX regime: 'low', 'mid', 'high', 'crisis'."""
        vix = self.get_vix_at(now_ts)
        if vix is None:
            return "mid"
        if vix >= VIX_CRISIS:
            return "crisis"
        if vix >= VIX_HIGH:
            return "high"
        if vix >= VIX_LOW:
            return "mid"
        return "low"

    def get_regime_multiplier(self, now_ts: int, has_active_event: bool = False) -> float:
        """Get position multiplier based on VIX regime.

        Args:
            now_ts: Current timestamp in ms.
            has_active_event: Whether a macro event alert is currently active.
        """
        regime = self.get_regime(now_ts)

        if has_active_event:
            if regime == "crisis":
                return _VIX_EVENT_OVERLAP_MULT["crisis_event"]
            if regime == "high":
                return _VIX_EVENT_OVERLAP_MULT["high_event"]

        return _VIX_REGIME_MULT.get(regime, 1.0)

    def check(self, symbol: str, now_ts: int) -> list[EventAlert]:
        """Check VIX regime and return alert if position should be adjusted.

        Only generates alerts for VIX regimes where we'd adjust position.
        Currently: no standalone VIX adjustments (all 1.0).
        The VIX effect is applied via get_regime_multiplier in EventGuard
        when combined with macro event alerts.
        """
        return []

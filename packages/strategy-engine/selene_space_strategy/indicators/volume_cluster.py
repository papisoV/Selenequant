"""Volume Cluster indicator — identify volume density zones as S/R.

Research finding (laoyulaoyu 700-blog analysis): Volume cluster zones
achieve 61% win rate vs 42% for traditional support/resistance.

Algorithm:
1. Divide price range into equal bins over the lookback window.
2. Sum volume per bin.
3. Bins with volume > threshold × average are "clusters".
4. Clusters act as support (below price) or resistance (above price).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.base import Indicator


@dataclass
class VolumeClusterZone:
    """A volume density zone acting as support or resistance."""

    type: Literal["support", "resistance"]
    price_level: float
    volume: float
    strength: float
    bar_range: tuple[int, int]
    filled: bool = False


class VolumeCluster(Indicator):
    """Identify volume density zones as support/resistance.

    High-volume price zones indicate institutional accumulation/distribution
    and act as magnetic price levels. When price returns to these zones,
    it tends to bounce (support) or stall (resistance).
    """

    def __init__(
        self,
        period: int = 20,
        num_bins: int = 10,
        volume_threshold: float = 2.0,
    ) -> None:
        """Initialize Volume Cluster indicator.

        Args:
            period: Lookback window for cluster calculation.
            num_bins: Number of price bins to divide the range into.
            volume_threshold: Minimum volume multiple of average to qualify as cluster.
        """
        super().__init__(period)
        self._num_bins = num_bins
        self._volume_threshold = volume_threshold
        self._zones: list[VolumeClusterZone] = []

    @property
    def name(self) -> str:
        return "volume_cluster"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Detect volume clusters and return cluster count at each bar.

        Args:
            data: DataFrame with 'high', 'low', 'close', 'volume' columns.

        Returns:
            Series with cumulative cluster count (like FVG).
        """
        high = data["high"]
        low = data["low"]
        close = data["close"]
        volume = data.get("volume", pd.Series(1.0, index=data.index))

        result = pd.Series(0, index=data.index, dtype=int)
        self._zones = []

        for i in range(self._period, len(data)):
            window_high = high.iloc[i - self._period : i].max()
            window_low = low.iloc[i - self._period : i].min()
            window_vol = volume.iloc[i - self._period : i]

            if window_high == window_low or window_vol.sum() == 0:
                continue

            bin_size = (window_high - window_low) / self._num_bins
            avg_vol = window_vol.sum() / self._num_bins

            if avg_vol == 0:
                continue

            bin_volumes = np.zeros(self._num_bins)
            bin_vwap = np.zeros(self._num_bins)

            for j in range(i - self._period, i):
                bar_high = high.iloc[j]
                bar_low = low.iloc[j]
                bar_close = close.iloc[j]
                bar_vol = volume.iloc[j]

                bar_mid = (bar_high + bar_low + bar_close) / 3.0
                bin_idx = min(int((bar_mid - window_low) / bin_size), self._num_bins - 1)
                bin_idx = max(bin_idx, 0)

                bin_volumes[bin_idx] += bar_vol
                bin_vwap[bin_idx] += bar_mid * bar_vol

            current_price = close.iloc[i]

            new_zones_at_bar = 0
            for b in range(self._num_bins):
                if bin_volumes[b] < avg_vol * self._volume_threshold:
                    continue

                vwap = bin_vwap[b] / bin_volumes[b] if bin_volumes[b] > 0 else 0
                strength = bin_volumes[b] / window_vol.sum()

                zone_type: Literal["support", "resistance"] = (
                    "support" if vwap < current_price else "resistance"
                )

                zone = VolumeClusterZone(
                    type=zone_type,
                    price_level=vwap,
                    volume=bin_volumes[b],
                    strength=strength,
                    bar_range=(i - self._period, i),
                )
                self._zones.append(zone)
                new_zones_at_bar += 1

            # Incremental signal: 1 if any new zone detected at this bar, 0 otherwise.
            # Safe to pair with `operator: exists` (returns 0/1, not cumulative count).
            result.iloc[i] = 1 if new_zones_at_bar > 0 else 0

        return result

    def get_active_zones(
        self,
        current_price: float,
        max_age: int = 50,
        max_zones: int = 5,
    ) -> list[VolumeClusterZone]:
        """Get unfilled zones near current price, sorted by strength.

        Args:
            current_price: Current market price.
            max_age: Maximum number of bars since zone creation.
            max_zones: Maximum zones to return.

        Returns:
            List of active VolumeClusterZone, strongest first.
        """
        total = len(self._zones)
        active: list[VolumeClusterZone] = []

        for zone in self._zones:
            if zone.filled:
                continue
            if total - zone.bar_range[1] > max_age:
                continue
            if zone.type == "support" and current_price < zone.price_level * 0.95:
                zone.filled = True
                continue
            if zone.type == "resistance" and current_price > zone.price_level * 1.05:
                zone.filled = True
                continue
            active.append(zone)

        active.sort(key=lambda z: z.strength, reverse=True)
        return active[:max_zones]

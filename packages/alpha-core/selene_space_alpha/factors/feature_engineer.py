"""Feature engineering for formula-based strategies (torch-native)."""

import torch
import torch.nn as nn

from .meme_indicators import MemeIndicators
from ..vocab import FEATURE_NAMES


class RMSNormFactor(nn.Module):
    """RMSNorm for factor normalization."""

    def __init__(self, d_model: int, eps: float = 1e-6):
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(d_model))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        rms = torch.sqrt(torch.mean(x**2, dim=-1, keepdim=True) + self.eps)
        return (x / rms) * self.weight


class AdvancedFactorEngineer:
    """Advanced feature engineering producing 12-dimensional feature space."""

    def __init__(self):
        self.rms_norm = RMSNormFactor(1)

    @staticmethod
    def robust_norm(t: torch.Tensor) -> torch.Tensor:
        median = torch.nanmedian(t, dim=1, keepdim=True)[0]
        mad = torch.nanmedian(torch.abs(t - median), dim=1, keepdim=True)[0] + 1e-6
        norm = (t - median) / mad
        return torch.clamp(norm, -5.0, 5.0)

    def compute_advanced_features(self, raw_dict: dict[str, torch.Tensor]) -> torch.Tensor:
        """Compute 12-dimensional feature space with advanced factors.

        Args:
            raw_dict: Dict with keys close, open, high, low, volume, liquidity, fdv.
                Each value is a [tokens, timesteps] tensor.

        Returns:
            Tensor of shape [tokens, 12, timesteps].
        """
        c = raw_dict["close"]
        o = raw_dict["open"]
        h = raw_dict["high"]
        l = raw_dict["low"]
        v = raw_dict["volume"]
        liq = raw_dict["liquidity"]
        fdv = raw_dict["fdv"]

        # Basic factors
        ret = torch.log(c / (torch.roll(c, 1, dims=1) + 1e-9))
        liq_score = MemeIndicators.liquidity_health(liq, fdv)
        pressure = MemeIndicators.buy_sell_imbalance(c, o, h, l)
        fomo = MemeIndicators.fomo_acceleration(v)
        dev = MemeIndicators.pump_deviation(c)
        log_vol = torch.log1p(v)

        # Advanced factors
        vol_cluster = MemeIndicators.volatility_clustering(c)
        momentum_rev = MemeIndicators.momentum_reversal(c)
        rel_strength = MemeIndicators.relative_strength(c, h, l)
        hl_range = (h - l) / (c + 1e-9)
        close_pos = (c - l) / (h - l + 1e-9)
        vol_prev = torch.roll(v, 1, dims=1)
        vol_trend = (v - vol_prev) / (vol_prev + 1.0)

        features = torch.stack(
            [
                self.robust_norm(ret),
                liq_score,
                pressure,
                self.robust_norm(fomo),
                self.robust_norm(dev),
                self.robust_norm(log_vol),
                self.robust_norm(vol_cluster),
                momentum_rev,
                self.robust_norm(rel_strength),
                self.robust_norm(hl_range),
                close_pos,
                self.robust_norm(vol_trend),
            ],
            dim=1,
        )

        return features


class FeatureEngineer:
    """Basic feature engineering producing 6-dimensional feature space."""

    INPUT_DIM = len(FEATURE_NAMES)

    @staticmethod
    def compute_features(raw_dict: dict[str, torch.Tensor]) -> torch.Tensor:
        """Compute 6-dimensional feature space.

        Args:
            raw_dict: Dict with keys close, open, high, low, volume, liquidity, fdv.

        Returns:
            Tensor of shape [tokens, 6, timesteps].
        """
        c = raw_dict["close"]
        o = raw_dict["open"]
        h = raw_dict["high"]
        l = raw_dict["low"]
        v = raw_dict["volume"]
        liq = raw_dict["liquidity"]
        fdv = raw_dict["fdv"]

        ret = torch.log(c / (torch.roll(c, 1, dims=1) + 1e-9))
        liq_score = MemeIndicators.liquidity_health(liq, fdv)
        pressure = MemeIndicators.buy_sell_imbalance(c, o, h, l)
        fomo = MemeIndicators.fomo_acceleration(v)
        dev = MemeIndicators.pump_deviation(c)
        log_vol = torch.log1p(v)

        def robust_norm(t: torch.Tensor) -> torch.Tensor:
            median = torch.nanmedian(t, dim=1, keepdim=True)[0]
            mad = torch.nanmedian(torch.abs(t - median), dim=1, keepdim=True)[0] + 1e-6
            norm = (t - median) / mad
            return torch.clamp(norm, -5.0, 5.0)

        features = torch.stack(
            [
                robust_norm(ret),
                liq_score,
                pressure,
                robust_norm(fomo),
                robust_norm(dev),
                robust_norm(log_vol),
            ],
            dim=1,
        )

        return features

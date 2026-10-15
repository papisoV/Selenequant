"""Meme-specific technical indicators (torch-native)."""

import torch


class MemeIndicators:
    """Static indicator methods for meme token analysis."""

    @staticmethod
    def liquidity_health(liquidity: torch.Tensor, fdv: torch.Tensor) -> torch.Tensor:
        ratio = liquidity / (fdv + 1e-6)
        return torch.clamp(ratio * 4.0, 0.0, 1.0)

    @staticmethod
    def buy_sell_imbalance(
        close: torch.Tensor,
        open_: torch.Tensor,
        high: torch.Tensor,
        low: torch.Tensor,
    ) -> torch.Tensor:
        range_hl = high - low + 1e-9
        body = close - open_
        strength = body / range_hl
        return torch.tanh(strength * 3.0)

    @staticmethod
    def fomo_acceleration(volume: torch.Tensor, window: int = 5) -> torch.Tensor:
        vol_prev = torch.roll(volume, 1, dims=1)
        vol_chg = (volume - vol_prev) / (vol_prev + 1.0)
        acc = vol_chg - torch.roll(vol_chg, 1, dims=1)
        return torch.clamp(acc, -5.0, 5.0)

    @staticmethod
    def pump_deviation(close: torch.Tensor, window: int = 20) -> torch.Tensor:
        pad = torch.zeros((close.shape[0], window - 1), device=close.device)
        c_pad = torch.cat([pad, close], dim=1)
        ma = c_pad.unfold(1, window, 1).mean(dim=-1)
        dev = (close - ma) / (ma + 1e-9)
        return dev

    @staticmethod
    def volatility_clustering(close: torch.Tensor, window: int = 10) -> torch.Tensor:
        ret = torch.log(close / (torch.roll(close, 1, dims=1) + 1e-9))
        ret_sq = ret**2

        pad = torch.zeros((ret_sq.shape[0], window - 1), device=close.device)
        ret_sq_pad = torch.cat([pad, ret_sq], dim=1)
        vol_ma = ret_sq_pad.unfold(1, window, 1).mean(dim=-1)

        return torch.sqrt(vol_ma + 1e-9)

    @staticmethod
    def momentum_reversal(close: torch.Tensor, window: int = 5) -> torch.Tensor:
        ret = torch.log(close / (torch.roll(close, 1, dims=1) + 1e-9))

        pad = torch.zeros((ret.shape[0], window - 1), device=close.device)
        ret_pad = torch.cat([pad, ret], dim=1)
        mom = ret_pad.unfold(1, window, 1).sum(dim=-1)

        mom_prev = torch.roll(mom, 1, dims=1)
        reversal = (mom * mom_prev < 0).float()

        return reversal

    @staticmethod
    def relative_strength(
        close: torch.Tensor,
        high: torch.Tensor,
        low: torch.Tensor,
        window: int = 14,
    ) -> torch.Tensor:
        ret = close - torch.roll(close, 1, dims=1)

        gains = torch.relu(ret)
        losses = torch.relu(-ret)

        pad = torch.zeros((gains.shape[0], window - 1), device=close.device)
        gains_pad = torch.cat([pad, gains], dim=1)
        losses_pad = torch.cat([pad, losses], dim=1)

        avg_gain = gains_pad.unfold(1, window, 1).mean(dim=-1)
        avg_loss = losses_pad.unfold(1, window, 1).mean(dim=-1)

        rs = (avg_gain + 1e-9) / (avg_loss + 1e-9)
        rsi = 100 - (100 / (1 + rs))

        return (rsi - 50) / 50

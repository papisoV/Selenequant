"""Meme backtest evaluation (torch-native, used during training)."""

import torch


class MemeBacktest:
    """Backtest evaluation for meme token strategies.

    Computes a fitness score from factor signals applied to historical data.
    """

    def __init__(
        self,
        trade_size: float = 1000.0,
        min_liq: float = 1000.0,
        base_fee: float = 0.001,
    ):
        self.trade_size = trade_size
        self.min_liq = min_liq
        self.base_fee = base_fee

    def evaluate(
        self,
        factors: torch.Tensor,
        raw_data: dict[str, torch.Tensor],
        target_ret: torch.Tensor,
    ) -> tuple[torch.Tensor, float]:
        """Evaluate factor signals against historical returns.

        Args:
            factors: Signal tensor [tokens, timesteps].
            raw_data: Dict with 'liquidity' tensor [tokens, timesteps].
            target_ret: Log returns [tokens, timesteps].

        Returns:
            (fitness_score, mean_cumulative_return)
        """
        liquidity = raw_data["liquidity"]
        signal = torch.sigmoid(factors)
        is_safe = (liquidity > self.min_liq).float()
        position = (signal > 0.85).float() * is_safe

        impact_slippage = self.trade_size / (liquidity + 1e-9)
        impact_slippage = torch.clamp(impact_slippage, 0.0, 0.05)
        total_slippage_one_way = self.base_fee + impact_slippage

        prev_pos = torch.roll(position, 1, dims=1)
        prev_pos[:, 0] = 0
        turnover = torch.abs(position - prev_pos)
        tx_cost = turnover * total_slippage_one_way

        gross_pnl = position * target_ret
        net_pnl = gross_pnl - tx_cost
        cum_ret = net_pnl.sum(dim=1)

        big_drawdowns = (net_pnl < -0.05).float().sum(dim=1)
        score = cum_ret - (big_drawdowns * 2.0)

        activity = position.sum(dim=1)
        score = torch.where(
            activity < 5, torch.tensor(-10.0, device=score.device), score
        )

        final_fitness = torch.median(score)
        return final_fitness, cum_ret.mean().item()

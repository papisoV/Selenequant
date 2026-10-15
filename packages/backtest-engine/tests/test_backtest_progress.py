"""US-105 回测进度: progress_cb 每 N 根 bar 吐 (done, total),循环结束必吐 (total, total)."""

from __future__ import annotations

from selene_space_backtest.commissions import NoCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.slippage import NoSlippage
from selene_space_types.market import KlineEvent


def _flat_tape(n_bars: int) -> list[KlineEvent]:
    origin = 1711000000000
    bar_ms = 3_600_000
    return [
        KlineEvent(
            exchange="backtest",
            symbol="BTC/USDT",
            timestamp=origin + i * bar_ms,
            interval="1h",
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1.0,
            closed=True,
        )
        for i in range(n_bars)
    ]


class _Quiet:
    def on_kline(self, event: KlineEvent):
        return None

    def reset(self) -> None:
        return None


def _run(n_bars: int, cb) -> None:
    config = BacktestConfig(
        initial_cash=10_000.0,
        commission=NoCommission(),
        slippage=NoSlippage(),
        progress_cb=cb,
    )
    BacktestEngine(config=config).run(_Quiet(), _flat_tape(n_bars))


class TestProgressCallback:
    def test_callback_fires_with_done_total_pairs(self) -> None:
        seen: list[tuple[int, int]] = []
        _run(1000, lambda done, total: seen.append((done, total)))
        assert seen, "progress_cb 从未被调用"
        total = seen[-1][1]
        assert total == 1000
        # 递增且不超过 total
        dones = [d for d, _ in seen]
        assert dones == sorted(dones)
        assert all(0 < d <= total for d in dones)
        # 收尾必报满
        assert seen[-1] == (1000, 1000)

    def test_step_is_coarse_not_every_bar(self) -> None:
        # 1000 根按 5% 步长 ≈ 20 次左右回调,不应每根都调(≤40 容差)
        seen: list[tuple[int, int]] = []
        _run(1000, lambda done, total: seen.append((done, total)))
        assert len(seen) <= 40

    def test_small_tape_still_reports_completion(self) -> None:
        # 极短数据:至少收尾一次 (n, n)
        seen: list[tuple[int, int]] = []
        _run(3, lambda done, total: seen.append((done, total)))
        assert seen[-1] == (3, 3)

    def test_no_callback_is_default_and_runs(self) -> None:
        # 不传 progress_cb 零回归:照常跑完
        config = BacktestConfig(
            initial_cash=10_000.0,
            commission=NoCommission(),
            slippage=NoSlippage(),
        )
        result = BacktestEngine(config=config).run(_Quiet(), _flat_tape(50))
        assert result.trades_count == 0

    def test_callback_exception_does_not_kill_backtest(self) -> None:
        # 回调抛错只警告,不中断回测(进度是副作用,不能绑架主流程)
        def boom(done: int, total: int) -> None:
            raise RuntimeError("progress sink exploded")

        result = BacktestEngine(
            config=BacktestConfig(
                initial_cash=10_000.0,
                commission=NoCommission(),
                slippage=NoSlippage(),
                progress_cb=boom,
            )
        ).run(_Quiet(), _flat_tape(50))
        assert result.trades_count == 0

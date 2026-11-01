#!/usr/bin/env python3
"""Walk-forward validation for dca_grid @ SOL/USDT 4h."""
from __future__ import annotations

import json
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except (AttributeError, IOError):
        pass

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from selene_space_market.kline_db import KlineDB
from selene_space_strategy.optimizer import StrategyOptimizer
from selene_space_strategy.templates.presets.dca_grid import DCAGridStrategy
from selene_space_types.market import KlineEvent

DB_PATH = PROJECT_ROOT / "services" / "local-api" / "data" / "klines.db"
PARAM_SPACE = {
    "fast_ma": (5, 15),
    "slow_ma": (20, 50),
    "base_size": (1.0, 10.0),
    "max_trades": (5, 15),
    "grid_spacing": (0.002, 0.01),
    "grid_price_scale": (1.1, 1.5),
    "grid_volume_scale": (1.5, 2.5),
    "take_profit_pct": (0.01, 0.03),
    "stop_loss_pct": (0.03, 0.10),
}


def main() -> None:
    db = KlineDB(DB_PATH)
    df = db.get_klines(exchange="binance", market_type="futures", symbol="ETH/USDT", interval="4h")
    db.close()
    klines = [KlineEvent(
        exchange="binance", market_type="futures", symbol="ETH/USDT",
        timestamp=int(r["timestamp"]), interval="4h",
        open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
        close=float(r["close"]), volume=float(r["volume"]), closed=True,
    ) for _, r in df.iterrows()]
    print(f"ETH/USDT 4h bars: {len(klines)}")

    opt = StrategyOptimizer(
        strategy_class=DCAGridStrategy,
        initial_capital=100000.0,
        leverage=1,
        fee_rate=0.0005,
        long_only=True,
    )

    print("Running walk-forward (5 folds, 30 trials/fold)...")
    wf = opt.walk_forward_validate(
        data=klines,
        param_space=PARAM_SPACE,
        symbol="ETH/USDT",
        n_folds=5,
        train_ratio=0.7,
        n_trials_per_fold=30,
        verbose=True,
    )

    print("\n" + "=" * 60)
    print("Walk-Forward 结果 (DCA网格 ETH 4h)")
    print("=" * 60)
    print(f"平均 IS Sharpe:  {wf.avg_is_sharpe:.4f}")
    print(f"平均 OOS Sharpe: {wf.avg_oos_sharpe:.4f}")
    if wf.avg_is_sharpe != 0:
        print(f"IS/OOS 比率:     {wf.avg_oos_sharpe / wf.avg_is_sharpe:.2%}")
    print(f"平均 IS 收益:    {wf.avg_is_return:.4f}")
    print(f"平均 OOS 收益:   {wf.avg_oos_return:.4f}")

    full_sample_sharpe = 0.3587
    print(f"\n全样本 Sharpe:    {full_sample_sharpe:.4f}")
    if wf.avg_oos_sharpe >= full_sample_sharpe * 0.5:
        print("OOS >= 50% 全样本 → 通过(稳健)")
    elif wf.avg_oos_sharpe >= 0.2:
        print("OOS 在 0.2-0.5x 全样本 → 部分过拟合,小仓位可用")
    elif wf.avg_oos_sharpe >= 0:
        print("OOS 0-0.2 → 严重过拟合,实盘大概率不赚")
    else:
        print("OOS 负 → 完全过拟合,不能用")

    out = PROJECT_ROOT / "data" / "tuning_results" / "walk_forward_dca_eth_4h.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(wf.to_dict(), f, indent=2, ensure_ascii=False, default=str)
    print(f"\n保存到: {out}")


if __name__ == "__main__":
    main()

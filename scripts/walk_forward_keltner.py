#!/usr/bin/env python3
"""Walk-forward validation for keltner_v2 @ SOL/USDT 1d best params."""
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
from selene_space_strategy.templates.presets.keltner_v2 import KeltnerChannelV2Strategy

DB_PATH = PROJECT_ROOT / "services" / "local-api" / "data" / "klines.db"
PARAM_SPACE = {
    "ma_length": (10, 30),
    "atr_length": (10, 30),
    "mult": (1.5, 3.0),
    "adx_length": (7, 21),
    "adx_threshold": (15, 35),
    "stop_pct": (0.02, 0.08),
    "tp_pct": (0.05, 0.15),
}


def main() -> None:
    db = KlineDB(DB_PATH)
    df = db.get_klines(exchange="binance", market_type="futures", symbol="SOL/USDT", interval="1d")
    db.close()
    from selene_space_types.market import KlineEvent
    klines = [KlineEvent(
        exchange="binance", market_type="futures", symbol="SOL/USDT",
        timestamp=int(r["timestamp"]), interval="1d",
        open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
        close=float(r["close"]), volume=float(r["volume"]), closed=True,
    ) for _, r in df.iterrows()]
    print(f"SOL/USDT 1d bars: {len(klines)}")

    opt = StrategyOptimizer(
        strategy_class=KeltnerChannelV2Strategy,
        initial_capital=100000.0,
        leverage=1,
        fee_rate=0.0005,
        long_only=True,
    )

    print("Running walk-forward (5 folds, 20 trials/fold)...")
    wf = opt.walk_forward_validate(
        data=klines,
        param_space=PARAM_SPACE,
        symbol="SOL/USDT",
        n_folds=5,
        train_ratio=0.7,
        n_trials_per_fold=20,
        verbose=True,
    )

    print("\n" + "=" * 60)
    print("Walk-Forward 结果")
    print("=" * 60)
    print(f"平均 IS Sharpe:  {wf.avg_is_sharpe:.4f}")
    print(f"平均 OOS Sharpe: {wf.avg_oos_sharpe:.4f}")
    print(f"IS/OOS 比率:     {wf.avg_oos_sharpe / wf.avg_is_sharpe:.2%}" if wf.avg_is_sharpe != 0 else "N/A")
    print(f"平均 IS 收益:    {wf.avg_is_return:.4f}")
    print(f"平均 OOS 收益:   {wf.avg_oos_return:.4f}")

    out = PROJECT_ROOT / "data" / "tuning_results" / "walk_forward_keltner_sol_1d.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(wf.to_dict(), f, indent=2, ensure_ascii=False, default=str)
    print(f"\n保存到: {out}")


if __name__ == "__main__":
    main()

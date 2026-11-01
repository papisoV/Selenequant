#!/usr/bin/env python3
"""Run backtests with FIXED parameters from 推演 1 (no optimization).
Tests 5 推演 strategies mapped to existing templates on BTC/ETH/SOL.

Usage:
    python scripts/backtest_deduced.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Fix Windows console encoding (use reconfigure, not replace stdout)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except (AttributeError, IOError):
        pass

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from selene_space_market.kline_db import KlineDB
from selene_space_types.market import KlineEvent

from selene_space_strategy.templates.presets.rsi_ma_delta import RSIMADeltaStrategy
from selene_space_strategy.templates.presets.tema_channel import TEMAChannelStrategy
from selene_space_strategy.templates.presets.dca_grid import DCAGridStrategy
from selene_space_strategy.templates.presets.ema_triple_cross import EMATripleCrossStrategy
from selene_space_strategy.templates.presets.pyramid_ma import PyramidMAStrategy

DB_PATH = PROJECT_ROOT / "services" / "local-api" / "data" / "klines.db"

# 推演 5 策略映射 + 推演参数
DEDUCED_STRATEGIES: list[dict[str, Any]] = [
    {
        "id": "rsi_1h_oversold",
        "name": "RSI 1h 超卖反弹(日内 P0)",
        "strategy_class": RSIMADeltaStrategy,
        "interval": "1h",
        "leverage": 5,
        "params": {
            "rsi_len": 14,
            "ma_len1": 14,
            "ma_len2": 14,
            "tp_pct": 0.04,
            "sl_pct": 0.02,
        },
    },
    {
        "id": "5min_breakout",
        "name": "5min 突破前高(日内 P0,用 tema_channel 替代)",
        "strategy_class": TEMAChannelStrategy,
        "interval": "5m",
        "leverage": 8,
        "params": {
            "length": 20,
            "mult": 2.0,
            "tp_pct": 0.015,
            "sl_pct": 0.008,
        },
    },
    {
        "id": "1h_grid",
        "name": "1h 0.5% 网格(日内 P1)",
        "strategy_class": DCAGridStrategy,
        "interval": "1h",
        "leverage": 5,
        "params": {
            "fast_ma": 20,
            "slow_ma": 50,
            "base_size": 3.0,
            "max_trades": 10,
            "grid_spacing": 0.005,
            "grid_price_scale": 1.2,
            "grid_volume_scale": 2.0,
            "take_profit_pct": 0.02,
            "stop_loss_pct": 0.05,
        },
    },
    {
        "id": "ma_4h_cross",
        "name": "MA 4h 金叉死叉(短线 P1)",
        "strategy_class": EMATripleCrossStrategy,
        "interval": "4h",
        "leverage": 3,
        "params": {
            "short_period": 20,
            "mid_period": 50,
            "long_period": 100,
            "atr_period": 14,
            "stop_atr_mult": 2.0,
            "tp_pct": 0.06,
            "sl_pct": 0.02,
        },
    },
    {
        "id": "1d_breakout",
        "name": "1d 突破 20 日新高(短线 P2,用 pyramid_ma 替代)",
        "strategy_class": PyramidMAStrategy,
        "interval": "1d",
        "leverage": 3,
        "params": {
            "short_p": 20,
            "mid_short_p": 20,
            "mid_p": 50,
            "long_p": 100,
            "take_profit_pct": 0.15,
            "stop_loss_pct": 0.05,
            "max_positions": 3,
        },
    },
]

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
INITIAL_CAPITAL = 100000.0
FEE_RATE = 0.0005


def load_klines(symbol: str, interval: str) -> list[KlineEvent]:
    db = KlineDB(DB_PATH)
    df = db.get_klines(exchange="binance", market_type="futures", symbol=symbol, interval=interval)
    db.close()
    if df.empty:
        return []
    klines = []
    for _, row in df.iterrows():
        klines.append(KlineEvent(
            exchange="binance", market_type="futures", symbol=symbol,
            timestamp=int(row["timestamp"]), interval=interval,
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume=float(row["volume"]), closed=True,
        ))
    return klines


def run_backtest(strategy_class, params, klines, leverage) -> dict[str, Any] | None:
    try:
        strat = strategy_class(
            initial_capital=INITIAL_CAPITAL,
            leverage=leverage,
            fee_rate=FEE_RATE,
            long_only=True,
            **params,
        )
        result = strat.run_backtest(klines)
        return result.to_dict() if result else None
    except Exception as e:
        import traceback
        return {"error": str(e), "traceback": traceback.format_exc()[-500:]}


def main() -> None:
    print("=" * 80)
    print("推演 1 — 5 策略固定参数回测")
    print("=" * 80)
    print(f"本金: 100000 | 杠杆: 各策略不同 | 手续费: {FEE_RATE}")
    print(f"交易对: {SYMBOLS}")
    print("=" * 80)

    all_results = []

    for strat in DEDUCED_STRATEGIES:
        print(f"\n>>> {strat['name']} ({strat['interval']}, {strat['leverage']}x)")
        print(f"    params: {strat['params']}")

        for symbol in SYMBOLS:
            klines = load_klines(symbol, strat["interval"])
            if not klines:
                print(f"    {symbol}: 无数据")
                continue

            bt = run_backtest(strat["strategy_class"], strat["params"], klines, strat["leverage"])
            if bt is None or "error" in bt:
                err = bt.get("error", "unknown") if bt else "no result"
                print(f"    {symbol} ({len(klines)} bars): 失败 - {err[:100]}")
                all_results.append({
                    "strategy": strat["name"], "symbol": symbol,
                    "interval": strat["interval"], "leverage": strat["leverage"],
                    "error": err,
                })
                continue

            sharpe = bt.get("sharpe_ratio", 0)
            total_return = bt.get("total_return", 0)
            max_dd = bt.get("max_drawdown", 0)
            win_rate = bt.get("win_rate", 0)
            pf = bt.get("profit_factor", 0)
            trades = bt.get("total_trades", 0)

            verdict = "✅" if (sharpe > 1.5 and total_return > 0 and abs(max_dd) < 0.30) else "🟡" if sharpe > 0 else "❌"
            print(f"    {symbol} ({len(klines)} bars): {verdict} Sharpe={sharpe:.4f} 收益={total_return*100:+.2f}% 回撤={abs(max_dd)*100:.2f}% 胜率={win_rate*100:.1f}% 盈亏比={pf:.2f} 交易={trades}")

            all_results.append({
                "strategy": strat["name"], "symbol": symbol,
                "interval": strat["interval"], "leverage": strat["leverage"],
                "params": strat["params"],
                "sharpe": sharpe, "total_return": total_return,
                "max_drawdown": max_dd, "win_rate": win_rate,
                "profit_factor": pf, "total_trades": trades,
                "backtest_result": bt,
            })

    # Save
    out_file = PROJECT_ROOT / "data" / "tuning_results" / "backtest_deduced.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"results": all_results}, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n结果保存: {out_file}")

    # Summary
    print("\n" + "=" * 80)
    print("汇总(按 Sharpe 降序)")
    print("=" * 80)
    valid = [r for r in all_results if "sharpe" in r]
    valid.sort(key=lambda x: x["sharpe"], reverse=True)
    print(f"{'策略':<40} {'交易对':<10} {'周期':<4} {'杠杆':<4} {'Sharpe':>8} {'收益':>8} {'回撤':>6} {'胜率':>6} {'交易':>4}")
    print("-" * 100)
    for r in valid:
        print(f"{r['strategy'][:40]:<40} {r['symbol']:<10} {r['interval']:<4} {r['leverage']:>3}x {r['sharpe']:>8.4f} {r['total_return']*100:>+7.2f}% {abs(r['max_drawdown'])*100:>5.2f}% {r['win_rate']*100:>5.1f}% {r['total_trades']:>4}")


if __name__ == "__main__":
    main()

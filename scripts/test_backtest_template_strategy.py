"""Test backtest with created strategy from template."""
import sys
from pathlib import Path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, f"{PROJECT_ROOT}/services/local-api")
sys.path.insert(0, f"{PROJECT_ROOT}/packages/shared-types")
sys.path.insert(0, f"{PROJECT_ROOT}/packages/strategy-engine")
sys.path.insert(0, f"{PROJECT_ROOT}/packages/backtest-engine")
sys.path.insert(0, f"{PROJECT_ROOT}/packages/market-data")

from selene_space_strategy.factory import STRATEGY_TEMPLATES

# Check if parameters match bb_v3
config_params = {
    "bb_len": 7,
    "buy_len": 44,
    "buy_mult": 2.664,
    "sell_len": 20,
    "sell_mult": 2.54,
}

param_keys = set(config_params.keys())
print(f"param_keys: {param_keys}")

# Check detection logic
if {"bb_len", "buy_mult", "sell_mult"}.issubset(param_keys):
    print("DETECTED: bb_v3")
else:
    print("NOT DETECTED")

# Check STRATEGY_TEMPLATES
print(f"\nSTRATEGY_TEMPLATES keys: {list(STRATEGY_TEMPLATES.keys())[:10]}...")
print(f"bb_v3 in STRATEGY_TEMPLATES: {'bb_v3' in STRATEGY_TEMPLATES}")

# Now test actual backtest
from selene_space_backtest.engine import BacktestEngine, BacktestConfig
from selene_space_backtest.commissions import PercentageCommission
from selene_space_strategy.templates.presets.bb_v3 import BBV3Strategy
from selene_space_market.kline_db import KlineDB

# Load kline data
db = KlineDB(db_path=f"{PROJECT_ROOT}/services/local-api/data/klines.db")
df = db.get_klines("binance", "spot", "BTC/USDT", "1h")
print(f"\nLoaded {len(df)} klines")

# Create strategy with config params
strategy = BBV3Strategy(
    symbol="BTC/USDT",
    initial_capital=10000,
    bb_len=config_params["bb_len"],
    buy_len=config_params["buy_len"],
    buy_mult=config_params["buy_mult"],
    sell_len=config_params["sell_len"],
    sell_mult=config_params["sell_mult"],
    enable_long=True,
    enable_short=False,
)

# Run backtest
bt_config = BacktestConfig(
    initial_cash=10000,
    commission=PercentageCommission(rate=0.001),
    strategy_id="test-bb_v3",
    symbol="BTC/USDT",
    interval="1h",
)
engine = BacktestEngine(config=bt_config)
result = engine.run(strategy, df)

print("\nBacktest result:")
print(f"  trades_count: {result.trades_count}")
print(f"  total_return: {result.metrics.total_return:.4f}")
print(f"  sharpe_ratio: {result.metrics.sharpe_ratio:.4f}")

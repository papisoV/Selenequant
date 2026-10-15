"""Run factor mining on SeleneQuant kline data.

Usage:
    python scripts/run_alpha_mining.py
    python scripts/run_alpha_mining.py --symbol ETH/USDT --interval 4h --steps 500
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure project packages are importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))

from selene_space_alpha.config import AlphaCoreConfig
from selene_space_alpha.data_loader import CryptoDataLoader
from selene_space_alpha.mining.engine import AlphaEngine
from selene_space_alpha.strategy_bridge import AlphaFormulaStrategy
from selene_space_alpha.vocab import FORMULA_VOCAB


def main():
    parser = argparse.ArgumentParser(description="Factor Mining")
    parser.add_argument("--db", default="services/local-api/data/klines.db", help="Path to klines.db")
    parser.add_argument("--symbol", default="BTC/USDT", help="Trading pair")
    parser.add_argument("--exchange", default="binance", help="Exchange name")
    parser.add_argument("--interval", default="4h", help="Kline interval")
    parser.add_argument("--steps", type=int, default=500, help="Training steps")
    parser.add_argument("--batch-size", type=int, default=4096, help="Batch size per step")
    parser.add_argument("--max-formula-len", type=int, default=12, help="Max formula token length")
    parser.add_argument("--n-tokens", type=int, default=100, help="Synthetic token count")
    parser.add_argument("--limit-bars", type=int, default=500, help="Max bars to load")
    parser.add_argument("--output-dir", default="data/alpha_mining", help="Output directory")
    parser.add_argument("--no-lord", action="store_true", help="Disable LoRD regularization")
    args = parser.parse_args()

    db_path = PROJECT_ROOT / args.db
    output_dir = PROJECT_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Factor Mining")
    print("=" * 60)
    print(f"  Data:  {db_path}")
    print(f"  Symbol: {args.symbol} | Interval: {args.interval} | Exchange: {args.exchange}")
    print(f"  Steps: {args.steps} | Batch: {args.batch_size} | FormulaLen: {args.max_formula_len}")
    print(f"  Device: {'CUDA' if AlphaCoreConfig.DEVICE.type == 'cuda' else 'CPU'}")
    print(f"  LoRD:   {'OFF' if args.no_lord else 'ON'}")
    print(f"  Output: {output_dir}")
    print()

    # Config
    config = AlphaCoreConfig()
    config.TRAIN_STEPS = args.steps
    config.BATCH_SIZE = args.batch_size
    config.MAX_FORMULA_LEN = args.max_formula_len

    # Load data
    print("[1/3] Loading kline data...")
    loader = CryptoDataLoader(config)
    loader.load_from_sqlite(
        db_path=str(db_path),
        symbol=args.symbol,
        exchange=args.exchange,
        interval=args.interval,
        limit_bars=args.limit_bars,
        n_synthetic_tokens=args.n_tokens,
    )
    print(f"  feat_tensor shape: {loader.feat_tensor.shape}")
    print(f"  target_ret shape:  {loader.target_ret.shape}")
    print(f"  synthetic tokens:  {len(loader.addresses)}")
    print()

    # Run mining
    print("[2/3] Mining alpha formulas...")
    engine = AlphaEngine(
        data_loader=loader,
        config=config,
        use_lord_regularization=not args.no_lord,
        output_dir=str(output_dir),
    )
    engine.train()
    print()

    # Decode and validate best formula
    print("[3/3] Validating discovered formula...")
    if engine.best_formula is None:
        print("  No valid formula discovered. Try more steps or different data.")
        return

    formula_readable = FORMULA_VOCAB.decode(engine.best_formula)
    print(f"  Best formula tokens: {engine.best_formula}")
    print(f"  Best formula readable: {formula_readable}")
    print(f"  Best score: {engine.best_score:.4f}")

    # Create strategy and test signal
    strategy = AlphaFormulaStrategy(
        formula_tokens=engine.best_formula,
        name=f"alpha_{args.symbol.replace('/', '')}_{args.interval}",
    )
    print(f"  Strategy: {strategy}")
    print(f"  Buy threshold: {strategy.buy_threshold}")
    print(f"  Sell threshold: {strategy.sell_threshold}")

    # Save full result
    result = {
        "symbol": args.symbol,
        "exchange": args.exchange,
        "interval": args.interval,
        "best_formula_tokens": engine.best_formula,
        "best_formula_readable": formula_readable,
        "best_score": engine.best_score,
        "config": {
            "steps": args.steps,
            "batch_size": args.batch_size,
            "max_formula_len": args.max_formula_len,
            "n_synthetic_tokens": args.n_tokens,
            "limit_bars": args.limit_bars,
        },
        "strategy": strategy.to_dict(),
        "vocab": {
            "features": list(FORMULA_VOCAB.feature_names),
            "operators": list(FORMULA_VOCAB.operator_names),
        },
    }

    result_path = output_dir / "mining_result.json"
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print()
    print(f"Result saved to: {result_path}")
    print()
    print("Formula breakdown:")
    for i, token in enumerate(engine.best_formula):
        if token < FORMULA_VOCAB.feature_count:
            kind = "FEATURE"
        else:
            kind = "OPERATOR"
        name = FORMULA_VOCAB.token_names[token] if token < FORMULA_VOCAB.size else f"UNK({token})"
        print(f"  [{i}] token={token} -> {kind}: {name}")


if __name__ == "__main__":
    main()

# Cleanroom implementation: design intent derived from prior art; independent expression.
"""Deterministic genetic formula search — CLI runner over klines.db (T3).

US-03 (T3) entry point: load real kline slices from ``klines.db``, feed them
to :func:`genetic_search.run_search` under a ``SearchConfig`` (or the lowered
``smoke_config``), and persist the top-N formulas to ``top_formulas.json`` for
the T4 validation protocol.

Data-loading contract (the plan's hard requirement):

1. ``ORDER BY timestamp DESC LIMIT ?`` per symbol — fetch the newest ``bars``
   rows.
2. **Dedupe keep-last** — klines.db stores 34-50% duplicate 4h candles per
   symbol; iterating newest-first and dropping later duplicates keeps the
   newest row per timestamp (source order, ``keep='last'`` semantics).
3. Reverse to chronological — the retained rows are reversed back to ascending
   time so the VM sees an ordered series.

Torch isolation is a HARD constraint: this module imports only sqlite3 /
argparse / json / pathlib / pandas — no torch, no ``real_engine``
(``test_no_torch_in_search_import.py`` asserts the whole search chain).

``python -m`` usage (from ``packages/factor-research``):

    python -m selene_space_factor_research.mining.runner \
        --symbols BTC/USDT,ETH/USDT --interval 4h --budget 1500 --seed 42

The ``--budget`` and ``--bars`` flags bound wall-clock (per the US-02 perf
calibration, ~0.44 s per non-cached candidate on a ~150-bar slice).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# sys.path bootstrap: neither package is pip-installed; the CLI must run the
# same way pytest does (conftest inserts these dirs for the test suite).
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[4]
for _pkg_dir in (
    _REPO_ROOT / "packages" / "factor-research",
    _REPO_ROOT / "packages" / "core",
    _REPO_ROOT / "packages" / "alpha-core",
    # storage: ledger_bridge lives behind lazy imports — the sys.path entry
    # only makes the lazy import resolvable when --ledger-known is on.
    _REPO_ROOT / "packages" / "storage",
):
    if str(_pkg_dir) not in sys.path:
        sys.path[:0] = [str(_pkg_dir)]

import pandas as pd  # noqa: E402  (after the sys.path bootstrap)

from selene_space_alpha.vocab import FORMULA_VOCAB  # noqa: E402
from selene_space_factor_research.mining.genetic_search import (  # noqa: E402
    FormulaEntry,
    SearchResult,
    run_search,
)
from selene_space_factor_research.mining.search_config import (  # noqa: E402
    SearchConfig,
    smoke_config,
)

from selene_space_core.kline_paths import default_klines_db_path  # noqa: E402

__all__ = [
    "DEFAULT_DB_PATH",
    "DEFAULT_RESULTS_DIR",
    "load_klines",
    "build_returns",
    "main",
]

#: Default klines.db location, relative to the repo root.
DEFAULT_DB_PATH = default_klines_db_path()
#: Where ``top_formulas.json`` and the run log land.
DEFAULT_RESULTS_DIR = _REPO_ROOT / "实验" / "alpha-mining"
#: Max bars kept per symbol (US-02 calibration: keep slices <= ~300).
DEFAULT_BARS = 300
#: Kline exchange used by the calibration / baseline scripts.
_EXCHANGE = "binance"
#: Market partition loaded by default; perpetual futures are the mainstream
#: venue for factor research. klines.db stores spot and futures rows under the
#: same (symbol, interval), so filtering here prevents ~34% duplicate reads.
_MARKET_TYPE = "futures"


def load_klines(
    db_path: Path,
    symbols: list[str],
    interval: str,
    *,
    max_bars: int = DEFAULT_BARS,
    market_type: str = _MARKET_TYPE,
) -> dict[str, pd.DataFrame]:
    """Load deduplicated, time-ordered OHLCV slices from ``klines.db``.

    Args:
        db_path: SQLite kline database.
        symbols: e.g. ``["BTC/USDT", "ETH/USDT"]``.
        interval: candle interval, e.g. ``"4h"``.
        max_bars: maximum unique bars retained per symbol (<= 300 suggested).
        market_type: ``klines`` partition to read (default ``"futures"``).
            Spot and futures share (symbol, interval), so the SQL filter is
            required; the keep-last dedupe below remains as defense.

    Returns:
        ``{symbol: DataFrame}`` with a DatetimeIndex (UTC, ms timestamps) and
        open/high/low/close/volume columns, ascending by time.

    Raises:
        FileNotFoundError: ``db_path`` does not exist.
        ValueError: a requested symbol has no rows in the database.
    """
    if not Path(db_path).exists():
        raise FileNotFoundError(f"Kline DB not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    try:
        data_by_symbol: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            rows = conn.execute(
                "SELECT timestamp, open, high, low, close, volume FROM klines"
                " WHERE symbol = ? AND exchange = ? AND market_type = ?"
                " AND interval = ? ORDER BY timestamp DESC LIMIT ?",
                (symbol, _EXCHANGE, market_type, interval, max_bars),
            ).fetchall()
            # Dedupe keep-last: newest-first scan keeps the first (newest) row
            # per timestamp, dropping the older duplicates.
            seen: set[int] = set()
            unique: list[tuple] = []
            for r in rows:
                if r[0] in seen:
                    continue
                seen.add(r[0])
                unique.append(r)
            unique.reverse()  # back to chronological
            if not unique:
                raise ValueError(f"no klines for {symbol}/{interval} in {db_path}")
            df = pd.DataFrame(
                unique,
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.set_index("timestamp")
            for col in ("open", "high", "low", "close", "volume"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            data_by_symbol[symbol] = df
    finally:
        conn.close()
    return data_by_symbol


def build_returns(
    data_by_symbol: dict[str, pd.DataFrame],
) -> dict[str, pd.Series]:
    """Forward-return Series per symbol (``close[t+1] / close[t] - 1``).

    Matches ``fitness.score``'s ``ret_by_symbol`` contract — the last bar's
    forward return is NaN (no future bar), exactly like ``test_fitness_scoring``.
    """
    ret_by_symbol: dict[str, pd.Series] = {}
    for symbol, df in data_by_symbol.items():
        closes = df["close"]
        ret_by_symbol[symbol] = closes.shift(-1) / closes - 1.0
    return ret_by_symbol


def _formula_to_record(entry: FormulaEntry, rank: int) -> dict:
    """One JSON-safe row for ``top_formulas.json`` from a FormulaEntry."""
    f = entry.fitness
    return {
        "rank": rank,
        "readable": entry.readable,
        "tokens": list(entry.tokens),
        "canonical_hash": entry.canonical_hash,
        "valid": f.valid,
        "score": f.score,
        "rank_ic": f.rank_ic,
        "ls_net_sharpe": f.ls_net_sharpe,
        "ls_sortino": f.ls_sortino,
        "ls_calmar": f.ls_calmar,
        "symbol_agreement": f.symbol_agreement,
        "stability_bonus": f.stability_bonus,
        "turnover": f.turnover,
        "ic_stability": f.ic_stability,
        "subperiod_ic_consistency": f.subperiod_ic_consistency,
        "oos_rank_ic": f.oos_rank_ic,
        "oos_ls_net_sharpe": f.oos_ls_net_sharpe,
    }


def _make_progress(budget: int) -> Callable[[dict[str, Any]], None]:
    """Return a progress callback that prints every ~10% of the budget."""

    def _on_progress(info: dict) -> None:
        frac = info["evaluations_used"] / budget
        print(
            f"[genetic-search] gen={info['generation']:>4d} "
            f"evals={info['evaluations_used']:>5d}/{budget} ({frac * 100:4.1f}%) "
            f"pop={info['population_size']:>3d} "
            f"best={info['best_score'] if info['best_score'] is not None else -float('inf'):.5f}"
        )

    return _on_progress


def _build_config(args: argparse.Namespace) -> SearchConfig:
    base = smoke_config() if args.budget == 0 else SearchConfig()
    budget = args.budget if args.budget > 0 else base.eval_budget
    return SearchConfig(seed=args.seed, eval_budget=budget, n_jobs=args.n_jobs)


def _parse_symbols(raw: str) -> list[str]:
    symbols = [s.strip() for s in raw.split(",") if s.strip()]
    if not symbols:
        raise ValueError("--symbols must be a non-empty comma-separated list")
    return symbols


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic genetic formula search over klines.db (T3)."
    )
    parser.add_argument(
        "--symbols",
        default="BTC/USDT",
        help="Comma-separated symbols (default: BTC/USDT).",
    )
    parser.add_argument(
        "--interval", default="4h", help="Candle interval (default: 4h)."
    )
    parser.add_argument(
        "--market-type",
        default=_MARKET_TYPE,
        help=f"Klines market partition (default: {_MARKET_TYPE}).",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"Path to klines.db (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--bars",
        type=int,
        default=DEFAULT_BARS,
        help=f"Max unique bars per symbol (default: {DEFAULT_BARS}).",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=1500,
        help="Eval budget (non-cached score() calls). 0 uses SearchConfig's default.",
    )
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (default: 42).")
    parser.add_argument(
        "--n-jobs",
        type=int,
        default=1,
        help="Worker count: 0/1 serial, >1 joblib loky (default: 1).",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_RESULTS_DIR),
        help=f"Output directory (default: {DEFAULT_RESULTS_DIR}).",
    )
    parser.add_argument(
        "--ledger-known",
        action="store_true",
        help=(
            "Read known formulas from the research ledger and block them from "
            "the search (zero eval budget spent on known hashes). Default off."
        ),
    )
    parser.add_argument(
        "--ledger-db",
        default=None,
        help=(
            "Research-ledger SQLite path (default: "
            "~/.selenespace/data/selenespace.db). Only read when "
            "--ledger-known is set."
        ),
    )
    args = parser.parse_args(argv)

    symbols = _parse_symbols(args.symbols)
    config = _build_config(args)

    t0 = time.perf_counter()
    print(
        f"[runner] symbols={symbols} interval={args.interval} bars={args.bars} "
        f"budget={config.eval_budget} seed={config.seed} n_jobs={config.n_jobs}"
    )
    data_by_symbol = load_klines(
        Path(args.db),
        symbols,
        args.interval,
        max_bars=args.bars,
        market_type=args.market_type,
    )
    for sym, df in data_by_symbol.items():
        print(
            f"[runner] {sym}: {len(df)} unique bars, {df.index.min()} -> {df.index.max()}"
        )
    ret_by_symbol = build_returns(data_by_symbol)

    # Ledger blocking is opt-in and lazy: --ledger-known off => zero storage
    # import, exact current behavior.
    blocked_hashes: list[str] | None = None
    if args.ledger_known:
        from selene_space_factor_research.mining import ledger_bridge

        ledger_db = (
            Path(args.ledger_db).expanduser()
            if args.ledger_db
            else ledger_bridge.DEFAULT_LEDGER_DB
        )
        known = ledger_bridge.load_known_formulas(
            ledger_db, vocab_digest=FORMULA_VOCAB.vocab_digest
        )
        blocked_hashes = list(known.keys())
        print(
            f"[runner] ledger: {ledger_db} known_formulas={len(blocked_hashes)} "
            f"(blocked from search)"
        )

    result: SearchResult = run_search(
        data_by_symbol,
        ret_by_symbol,
        config,
        blocked_hashes=blocked_hashes,
        progress=_make_progress(config.eval_budget),
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    records = [
        _formula_to_record(entry, rank)
        for rank, entry in enumerate(result.top_formulas, start=1)
    ]
    payload = {
        "symbols": symbols,
        "interval": args.interval,
        "bars_per_symbol": int(min(len(df) for df in data_by_symbol.values())),
        "seed": result.seed,
        "eval_budget": config.eval_budget,
        "evaluations_used": result.evaluations_used,
        "generations": result.generations,
        "stopped_reason": result.stopped_reason,
        "vocab_digest": FORMULA_VOCAB.vocab_digest,
        "token_layout_digest": FORMULA_VOCAB.token_layout_digest,
        "top_formulas": records,
    }
    out_file = out_dir / "top_formulas.json"
    out_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    elapsed = time.perf_counter() - t0
    print(
        f"[runner] done in {elapsed:.2f}s | evals={result.evaluations_used}/"
        f"{config.eval_budget} | gens={result.generations} | "
        f"stopped={result.stopped_reason}"
    )
    print(f"[runner] wrote {out_file}")
    top = result.top_formulas[0] if result.top_formulas else None
    if top is not None:
        print(
            f"[runner] TOP-1: score={top.fitness.score:.5f} "
            f"rank_ic={top.fitness.rank_ic:.5f} sharpe={top.fitness.ls_net_sharpe:.5f} "
            f"formula={top.readable}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

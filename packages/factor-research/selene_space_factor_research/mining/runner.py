# Cleanroom implementation: design intent derived from prior art; independent expression.
"""Deterministic genetic formula search — CLI runner over klines.db (T3).

US-03 (T3) entry point: load real kline slices from ``klines.db``, feed them
to :func:`genetic_search.run_search` under a ``SearchConfig`` (or the lowered
``smoke_config``), and persist the top-N formulas to ``top_formulas.json`` for
the T4 validation protocol.

Data-loading contract (the plan's hard requirement):

1. ``ORDER BY timestamp DESC LIMIT ?`` per symbol — fetch the newest ``bars``
   rows. With an explicit date window (``--start`` / ``--end``) the window
   replaces the LIMIT: every bar inside ``[start, end]`` is loaded and
   ``--bars`` is ignored, so the caller controls the sample instead of the
   database's "newest N" heuristic.
2. **Dedupe keep-last** — klines.db stores 34-50% duplicate 4h candles per
   symbol; iterating newest-first and dropping later duplicates keeps the
   newest row per timestamp (source order, ``keep='last'`` semantics).
3. Reverse to chronological — the retained rows are reversed back to ascending
   time so the VM sees an ordered series.

A date window is **UTC** (klines timestamps are epoch-ms UTC), and ``--end``
given as a plain date (``2025-01-01``, no clock component) is inclusive of that
whole day — otherwise ``--end 2025-01-01`` would silently drop every bar after
midnight and the sample would end a day early.

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
#: Symbols searched when the caller names none. Five majors: enough names that
#: the cross-sectional Rank-IC has a real distribution (see
#: ``_COMFORTABLE_SYMBOLS``) and all present in klines.db at 1h/4h.
DEFAULT_SYMBOLS = "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT"
#: Hard floor. ``fitness.score``'s Rank-IC is cross-sectional: one symbol gives
#: every date a single row, ``_spearman_pair`` returns NaN for ``len(x) < 2``,
#: the daily IC series is empty and *every* formula becomes a ``-inf`` sentinel
#: — a whole search budget buys nothing. Two symbols technically scores, but
#: Spearman on two points is always ±1, so the IC term has zero expectation and
#: only the backtest term moves; that is a silent lie rather than a verdict.
_MIN_SYMBOLS = 2
#: Below this count the cross-sectional IC can still be computed but takes only
#: a handful of discrete values, so its dispersion (and therefore the search's
#: signal) is thin. Not an error — a warning the caller can read and judge.
_COMFORTABLE_SYMBOLS = 4
#: Kline exchange used by the calibration / baseline scripts.
_EXCHANGE = "binance"
#: Market partition loaded by default; perpetual futures are the mainstream
#: venue for factor research. klines.db stores spot and futures rows under the
#: same (symbol, interval), so filtering here prevents ~34% duplicate reads.
_MARKET_TYPE = "futures"
#: Mirrors ``supply_line._MIN_SPAN_DAYS`` so the runner can warn about a panel
#: the supply line would defer. Kept as a literal (not an import) because the
#: search chain must stay import-light; ``test_runner_window`` pins the two
#: values equal so the copy cannot drift.
_G6_MIN_SPAN_DAYS = 365.0


def _span_days(data_by_symbol: dict[str, pd.DataFrame]) -> float:
    """Calendar span of the *shortest* symbol's panel, in days.

    The minimum, not the mean: every symbol has to clear G6 for a
    multi-symbol candidate to be validated at all.
    """
    spans = [
        (df.index.max() - df.index.min()).total_seconds() / 86_400.0
        for df in data_by_symbol.values()
        if len(df) > 0
    ]
    return min(spans) if spans else 0.0


#: The search's train/OOS split. ``fitness.score`` defaults ``train_frac`` to
#: 0.7 and no caller overrides it (``reward_score_kwargs`` does not map it), so
#: this literal and the search's real split cannot disagree. Kept here so the
#: artifact states the geometry it was searched under.
SEARCH_TRAIN_FRAC = 0.7


def _search_split_time(
    data_by_symbol: dict[str, pd.DataFrame], train_frac: float
) -> tuple[pd.Timestamp | None, float]:
    """``(first OOS bar, train_frac)`` as ``fitness.score`` computes the split.

    ``fitness`` stacks every symbol into one panel and splits *that* panel's
    date union at ``round(len * train_frac)``. Panels loaded together share a
    date axis, so the union is the shortest symbol's index — but if a symbol is
    ever ragged, the union (not any single frame) is what the search used.
    """
    axes = [
        pd.Index(pd.to_datetime(pd.Index(df.index)))
        for df in data_by_symbol.values()
        if len(df) > 0
    ]
    if not axes:
        return None, float(train_frac)
    union = axes[0]
    for axis in axes[1:]:
        union = union.union(axis)
    union = union.sort_values()
    n_train = max(1, int(round(len(union) * train_frac)))
    if n_train >= len(union):
        return None, float(train_frac)
    return pd.Timestamp(union[n_train]), float(train_frac)


def _parse_window_bound(raw: str, *, is_end: bool) -> int:
    """Parse a CLI date bound into inclusive epoch milliseconds (UTC).

    Accepts ``YYYY-MM-DD`` and ISO-8601 datetimes (``T`` or space separated,
    optional trailing ``Z``). A date-only ``end`` is expanded to the last
    millisecond of that UTC day — the natural reading of "--end 2025-01-01" is
    "include 2025-01-01", and truncating at midnight would silently end the
    sample a day early.

    Raises:
        ValueError: the string is not a parseable timestamp.
    """
    text = raw.strip()
    try:
        stamp = pd.Timestamp(text)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"unparseable date bound {raw!r}: {exc}") from exc
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    # Date-only end ⇒ whole day inclusive: last millisecond before the next day.
    if is_end and len(text) == 10:
        stamp = stamp + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)
    return int(stamp.value // 1_000_000)


def load_klines(
    db_path: Path,
    symbols: list[str],
    interval: str,
    *,
    max_bars: int = DEFAULT_BARS,
    market_type: str = _MARKET_TYPE,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Load deduplicated, time-ordered OHLCV slices from ``klines.db``.

    Args:
        db_path: SQLite kline database.
        symbols: e.g. ``["BTC/USDT", "ETH/USDT"]``.
        interval: candle interval, e.g. ``"4h"``.
        max_bars: maximum unique bars retained per symbol (<= 300 suggested).
            **Ignored** when ``start``/``end`` is given — an explicit window
            selects bars by time, and re-applying a row cap on top would
            quietly truncate the caller's requested sample.
        market_type: ``klines`` partition to read (default ``"futures"``).
            Spot and futures share (symbol, interval), so the SQL filter is
            required; the keep-last dedupe below remains as defense.
        start: inclusive window start (UTC), e.g. ``"2024-01-01"``.
        end: inclusive window end (UTC). A date-only value covers that whole
            day.

    Returns:
        ``{symbol: DataFrame}`` with a DatetimeIndex (UTC, ms timestamps) and
        open/high/low/close/volume columns, ascending by time.

    Raises:
        FileNotFoundError: ``db_path`` does not exist.
        ValueError: a requested symbol has no rows in the database (or none
            inside the requested window — an empty window is an error, never a
            silent zero-row panel), or ``start`` is not before ``end``.
    """
    if not Path(db_path).exists():
        raise FileNotFoundError(f"Kline DB not found: {db_path}")

    where = "symbol = ? AND exchange = ? AND market_type = ? AND interval = ?"
    params: list[Any] = []
    sql_tail = " ORDER BY timestamp DESC LIMIT ?"
    windowed = start is not None or end is not None
    if windowed:
        lo = _parse_window_bound(start, is_end=False) if start is not None else None
        hi = _parse_window_bound(end, is_end=True) if end is not None else None
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"start ({start}) is after end ({end})")
        if lo is not None:
            where += " AND timestamp >= ?"
        if hi is not None:
            where += " AND timestamp <= ?"
        # No LIMIT: the window is the selection.
        sql_tail = " ORDER BY timestamp DESC"

    conn = sqlite3.connect(str(db_path))
    try:
        data_by_symbol: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            row_params: list[Any] = [symbol, _EXCHANGE, market_type, interval]
            if windowed:
                if lo is not None:
                    row_params.append(lo)
                if hi is not None:
                    row_params.append(hi)
            else:
                row_params.append(max_bars)
            rows = conn.execute(
                "SELECT timestamp, open, high, low, close, volume FROM klines"
                f" WHERE {where}{sql_tail}",
                tuple(row_params),
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
                span = f" in window [{start or '-'}, {end or '-'}]" if windowed else ""
                raise ValueError(
                    f"no klines for {symbol}/{interval}{span} in {db_path}"
                )
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


def _check_symbol_count(symbols: list[str]) -> str | None:
    """Return a warning for a thin cross-section, or raise for an impossible one.

    The search's fitness is a *cross-sectional* Rank-IC, so the panel needs a
    cross-section. A single symbol cannot produce one (``_daily_ic`` drops every
    date as unscoreable), and a two-symbol Spearman is always ±1 — it runs, but
    the IC term has zero expectation. Both cases are about to burn the whole
    ``--budget`` before anyone notices, so fail/flag them at the entrance.

    Raises:
        ValueError: fewer than ``_MIN_SYMBOLS`` symbols were requested.
    """
    if len(symbols) < _MIN_SYMBOLS:
        raise ValueError(
            f"factor mining needs at least {_MIN_SYMBOLS} symbols (got {len(symbols)}: "
            f"{', '.join(symbols) or '-'}). The fitness Rank-IC is cross-sectional: a "
            f"single-symbol panel yields no daily cross-section, so every formula "
            f"scores -inf and the whole budget is wasted. Pass e.g. "
            f"--symbols {DEFAULT_SYMBOLS}."
        )
    if len(symbols) < _COMFORTABLE_SYMBOLS:
        return (
            f"only {len(symbols)} symbols: a 2-symbol cross-sectional Spearman is "
            f"always +/-1, so the IC term averages to zero and the score is driven "
            f"by the backtest leg alone. Prefer >= {_COMFORTABLE_SYMBOLS} symbols."
        )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic genetic formula search over klines.db (T3)."
    )
    parser.add_argument(
        "--symbols",
        default=DEFAULT_SYMBOLS,
        help=(
            "Comma-separated symbols (default: %(default)s). At least "
            f"{_MIN_SYMBOLS} are required: the fitness Rank-IC is cross-sectional "
            "and a single-symbol panel scores -inf for every formula."
        ),
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
        help=(
            f"Max unique bars per symbol (default: {DEFAULT_BARS}). "
            "Ignored when --start/--end is given."
        ),
    )
    parser.add_argument(
        "--start",
        default=None,
        help=(
            "Inclusive sample start, UTC (YYYY-MM-DD or ISO-8601). Selecting a "
            "window bypasses --bars entirely."
        ),
    )
    parser.add_argument(
        "--end",
        default=None,
        help=(
            "Inclusive sample end, UTC. A date-only value covers that whole "
            "day. Defaults to the newest bar when --start is given."
        ),
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
    # Cross-section floor: refuse a run that cannot score before it spends the
    # budget (see ``_check_symbol_count``). parser.error exits 2 with no traceback
    # — a bad flag, not a crash.
    try:
        symbol_warning = _check_symbol_count(symbols)
    except ValueError as exc:
        parser.error(str(exc))
    config = _build_config(args)

    t0 = time.perf_counter()
    print(
        f"[runner] symbols={symbols} interval={args.interval} bars={args.bars} "
        f"window=[{args.start or '-'} -> {args.end or '-'}] "
        f"budget={config.eval_budget} seed={config.seed} n_jobs={config.n_jobs}"
    )
    data_by_symbol = load_klines(
        Path(args.db),
        symbols,
        args.interval,
        max_bars=args.bars,
        market_type=args.market_type,
        start=args.start,
        end=args.end,
    )
    for sym, df in data_by_symbol.items():
        print(
            f"[runner] {sym}: {len(df)} unique bars, {df.index.min()} -> {df.index.max()}"
        )

    # The supply line's G6 defers any panel shorter than a year, so a run that
    # silently loaded 50 days can never reach a verdict. Say so at mining time
    # instead of letting the user discover it three commands later.
    # ASCII only: a Windows console on a GBK codepage mangles non-ASCII output.
    span_days = _span_days(data_by_symbol)
    if span_days < _G6_MIN_SPAN_DAYS:
        print(
            f"[runner] WARNING: panel span {span_days:.0f}d < "
            f"{_G6_MIN_SPAN_DAYS:.0f}d - the supply line's G6 will DEFER every "
            f"candidate from this artifact (widening the window is the only fix)."
        )
    # Thin cross-section: scoreable, but the IC term is near-degenerate (see
    # ``_check_symbol_count``). ASCII only, same GBK-console constraint as above.
    if symbol_warning is not None:
        print(f"[runner] WARNING: {symbol_warning}")

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
    # Provenance: record the sample the search actually consumed, not the
    # sample it asked for. ``bars_per_symbol`` is the real minimum after dedupe,
    # and the window is echoed so a downstream run (T4 / supply line) reloads
    # the *same* slice rather than the newest N bars.
    first_idx_min = min(df.index.min() for df in data_by_symbol.values())
    first_idx_max = max(df.index.max() for df in data_by_symbol.values())
    # Where the search stopped fitting. ``fitness.score`` splits each formula's
    # stacked panel by date at ``train_frac`` (a module literal, not a config
    # field), so a validator that starts its OOS folds at bar 0 measures the
    # formula on its own training data. Recording the split here lets the
    # validator floor its folds at the first untouched bar.
    search_train_end, search_train_frac = _search_split_time(
        data_by_symbol, SEARCH_TRAIN_FRAC
    )
    payload = {
        "symbols": symbols,
        "interval": args.interval,
        "bars_per_symbol": int(min(len(df) for df in data_by_symbol.values())),
        "market_type": args.market_type,
        "window": {
            "requested_start": args.start,
            "requested_end": args.end,
            "start": first_idx_min.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": first_idx_max.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "span_days": round(_span_days(data_by_symbol), 1),
            "selects_window": args.start is not None or args.end is not None,
            "search_train_end": (
                search_train_end.strftime("%Y-%m-%dT%H:%M:%SZ")
                if search_train_end is not None
                else None
            ),
            "search_train_frac": search_train_frac,
        },
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

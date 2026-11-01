# Cleanroom implementation: design intent derived from prior art; independent expression.
"""T4 validation report — JSON schema + atomic persistence + CLI entry point.

US-04 (T4) entry point: read ``internal-archive/top_formulas.json`` (the
T3 runner's output), load the **same-config** datasets the search used
(symbols / interval / bars_per_symbol come from the JSON metadata), run
:func:`~selene_space_factor_research.mining.validation_protocol.validate_formulas`
on the top-N formulas, and atomically persist
``internal-archive/validation_report.json``.

Report schema (fields aligned with the ``CycleReport`` naming convention —
``mean_ic`` / ``rolling_ic`` / ``decision`` / ``reason``; plus the T4-specific
``verdict`` / ``dsr`` / ``pbo`` / ``multi_symbol_agreement``):

.. code-block:: json

    {
      "generated_at": "2026-08-16T...",
      "summary": {"VALID": 2, "SUSPICIOUS": 1, "INVALID": 1, "INSUFFICIENT_DATA": 0},
      "config": {
        "n_trials": 340,
        "fold_config": {"min_train": 70, "fold_size": 20, "n_folds": 5},
        "thresholds": {"oos_ic_min": 0.02, "subinterval_agreement_min": 0.7,
                       "multi_symbol_agreement_min": 0.6667, "dsr_threshold": 0.95},
        "data_source": "klines.db",
        "datasets": {"4h": {"n_symbols": 3, "symbols": ["BTC/USDT", ...]}}
      },
      "formulas": [
        {"readable": "...", "tokens": [...], "canonical_hash": "...",
         "verdict": "VALID", "decision": "accept", "reason": "...",
         "mean_ic": 0.359, "rolling_ic": [0.31, 0.22, ...],
         "train_mean_ic": 0.249, "subinterval_agreement": 1.0,
         "multi_symbol_agreement": 1.0, "dsr": 0.99, "pbo": null,
         "n_oos_obs": 119, "datasets": {...per-dataset detail...}}
      ]
    }

Atomic persistence: ``write_report`` writes to a temp file in the target
directory, ``fsync``es it, then ``os.replace``s it over the destination — the
reader never observes a half-written report.

Honest fallback: when ``top_formulas.json`` holds only ``-inf`` score
sentinels (the search produced no valid formula), or ``klines.db`` is
missing, the CLI validates the recorded formulas on deterministic synthetic
AR(1) panels and records ``config.data_source: "synthetic_fallback"`` with the
reason — the report never pretends it used real market data when it did not.

Torch isolation is a HARD constraint: this module imports only argparse / json /
os / sqlite-free stdlib plus the torch-free factor-research + alpha-core chain
(``selene_space_backtest`` is imported lazily by the protocol's overfit audit,
so ``test_no_torch_in_search_import`` stays green).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Sequence

import pandas as pd

# ---------------------------------------------------------------------------
# sys.path bootstrap: neither package is pip-installed; the CLI must run the
# same way pytest does (conftest inserts these dirs for the test suite).
# backtest-engine + shared-types are required for the protocol's *lazy*
# ``selene_space_backtest.overfit.report`` import (DSR audit).
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[4]
for _pkg_dir in (
    _REPO_ROOT / "packages" / "factor-research",
    _REPO_ROOT / "packages" / "core",
    _REPO_ROOT / "packages" / "alpha-core",
    _REPO_ROOT / "packages" / "backtest-engine",
    _REPO_ROOT / "packages" / "shared-types",
):
    if str(_pkg_dir) not in sys.path:
        sys.path[:0] = [str(_pkg_dir)]

from selene_space_alpha.vocab import FORMULA_VOCAB  # noqa: E402
from selene_space_factor_research.mining.runner import (  # noqa: E402
    build_returns,
    load_klines,
)
from selene_space_factor_research.mining.validation_protocol import (  # noqa: E402
    FoldConfig,
    ValidationReport,
    validate_formulas,
)

from selene_space_core.kline_paths import default_klines_db_path  # noqa: E402

__all__ = [
    "DEFAULT_RESULTS_DIR",
    "build_datasets",
    "fallback_synthetic_datasets",
    "fold_config_of",
    "load_top_formulas",
    "main",
    "window_of",
    "write_report",
]

#: Where ``top_formulas.json`` lives and ``validation_report.json`` lands.
DEFAULT_RESULTS_DIR = _REPO_ROOT / "实验" / "alpha-mining"
#: Default klines.db (the T3 runner's data source).
DEFAULT_DB_PATH = default_klines_db_path()


# ---------------------------------------------------------------------------
# Report persistence (atomic).
# ---------------------------------------------------------------------------
def _summarize(report: ValidationReport) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in report.formulas:
        counts[f.verdict] = counts.get(f.verdict, 0) + 1
    return counts


def write_report(
    report: ValidationReport,
    path: Path,
    *,
    generated_at: str | None = None,
) -> Path:
    """Atomically persist a :class:`ValidationReport` as JSON.

    Writes to a temp file in the destination directory, fsyncs, then
    ``os.replace``s it into place — readers never see a partial report.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    payload["generated_at"] = generated_at or datetime.now().astimezone().isoformat()
    payload["summary"] = _summarize(report)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f"{path.stem}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


# ---------------------------------------------------------------------------
# Data loading.
# ---------------------------------------------------------------------------
def load_top_formulas(
    path: Path, *, strict_vocab: bool = False, verify_vocab: bool = True
) -> dict:
    """Read the T3 runner's ``top_formulas.json`` (raises on malformed input).

    Validates the payload's ``vocab_digest`` against the current
    :data:`FORMULA_VOCAB` via :meth:`FormulaVocab.verify`. Artifacts that
    predate digest recording (no ``vocab_digest`` key) are tolerated unless
    ``strict_vocab`` is set.

    Set ``verify_vocab=False`` to hand the digest decision to the caller. The
    supply line does this so a forked vocab is reported as its G1 finding
    rather than as an unreadable artifact.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"top-formulas file not found: {path}")
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    if "top_formulas" not in payload:
        raise ValueError(
            f"{path} is not a top_formulas.json payload (no 'top_formulas')"
        )
    if verify_vocab:
        FORMULA_VOCAB.verify(payload.get("vocab_digest"), strict=strict_vocab)
    return dict(payload)


def _has_inf_sentinels(records: list) -> bool:
    """True when the search produced no usable formula (-inf / invalid)."""
    for r in records:
        if not isinstance(r, dict):
            continue
        score = r.get("score")
        if score is not None and not isinstance(score, (int, float)):
            continue
        if score is None or not math.isfinite(float(score)):
            return True
        if r.get("valid") is False:
            return True
    return False


def fallback_synthetic_datasets(
    *,
    n_dates: int = 200,
    n_symbols: int = 4,
    seed: int = 42,
) -> dict[str, tuple[dict, dict]]:
    """Deterministic AR(1) momentum panels in the ``load_klines`` output shape.

    Matches the ``test_genetic_search._syn`` contract (DatetimeIndex +
    open/high/low/close/volume; forward-return Series) so the fallback report
    is reproducible and gives the decision table meaningful signal.
    """
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start="2024-01-01", periods=n_dates)
    noise = rng.normal(0.0, 0.02, (n_dates, n_symbols))
    rets = np.zeros_like(noise)
    rets[0] = noise[0]
    for t in range(1, n_dates):
        rets[t] = 0.5 * rets[t - 1] + noise[t]
    close = 100.0 * np.cumprod(1.0 + rets, axis=0)
    open_ = close / (1.0 + rets)
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    volume = 1000.0 * (1.0 + 0.5 * rng.random((n_dates, n_symbols)))

    data_by_symbol: dict = {}
    ret_by_symbol: dict = {}
    for s in range(n_symbols):
        sym = f"SYN{s}"
        data_by_symbol[sym] = pd.DataFrame(
            {
                "open": open_[:, s],
                "high": high[:, s],
                "low": low[:, s],
                "close": close[:, s],
                "volume": volume[:, s],
            },
            index=dates,
        )
        closes = pd.Series(close[:, s], index=dates)
        ret_by_symbol[sym] = closes.shift(-1) / closes - 1.0
    return {"synthetic": (data_by_symbol, ret_by_symbol)}


def build_datasets(
    db_path: Path,
    symbols: list[str],
    interval: str,
    bars: int,
    *,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, tuple[dict, dict]]:
    """Load the same-config datasets the T3 search used (one per interval).

    When the artifact records a sample window, pass it here: ``load_klines``
    then selects by time and ignores ``bars``. Reloading "the newest N bars"
    instead would validate the formula on a sample the search never saw.
    """
    data_by_symbol = load_klines(
        db_path, symbols, interval, max_bars=bars, start=start, end=end
    )
    ret_by_symbol = build_returns(data_by_symbol)
    return {interval: (data_by_symbol, ret_by_symbol)}


def window_of(payload: dict) -> tuple[str | None, str | None]:
    """Sample window recorded in a ``top_formulas.json`` payload.

    Returns ``(start, end)`` as absolute ISO timestamps, or ``(None, None)``
    for artifacts written before windows were recorded — those fall back to the
    legacy "newest ``bars_per_symbol``" behaviour.
    """
    window = payload.get("window")
    if not isinstance(window, dict):
        return None, None
    if not window.get("selects_window"):
        # A bars-based run recorded its realised span for provenance only; the
        # legacy LIMIT path reproduces it, and pinning the window would make a
        # re-run pick up bars the original run never had.
        return None, None
    start = window.get("start")
    end = window.get("end")
    return (
        str(start) if start is not None else None,
        str(end) if end is not None else None,
    )


def _naive_utc(value: object) -> pd.Timestamp | None:
    """Parse a timestamp and drop tzinfo (UTC), matching klines' tz-naive index.

    Artifacts write ``...Z`` (tz-aware); ``load_klines`` builds a tz-naive UTC
    index. Comparing the two raises, and a swallowed exception would silently
    place every fold before the search split — the exact defect this builder
    exists to remove. Normalise both sides instead.
    """
    try:
        ts = pd.Timestamp(str(value))
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts


def _naive_utc_index(dates: Sequence[object]) -> pd.DatetimeIndex:
    """Sorted, de-duplicated date axis in tz-naive UTC.

    Callers pass every symbol's index concatenated; the search split the
    *union* of those dates, so duplicates must not inflate the bar count.
    """
    idx = pd.DatetimeIndex(pd.to_datetime(list(dates)))
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return idx.unique().sort_values()


def fold_config_of(
    payload: dict,
    *,
    dates: Sequence[object] | None = None,
    min_train: int = 70,
    fold_size: int = 20,
    n_folds: int = 5,
) -> FoldConfig:
    """Walk-forward geometry that keeps OOS out of the search's training window.

    The search fits on the first ``train_frac`` of the dates (``fitness.score``
    hardcodes ``train_frac=0.7``; it is not a config field). Folds anchored at
    bar 0 therefore validate the formula on the very bars it was fitted on —
    measured on the real artifact: 600 bars, 5 folds, OOS ``[70,170)``, 100%
    inside the search's train window, zero independent bars.

    This builder derives a geometry that cannot do that: folds packed against
    the newest bars (``anchor="end"``) and floored at the search's split point.

    Two sources for the floor, in order of precision:

    * ``dates`` — the caller's actual date axis. ``search_train_end`` (ISO, the
      first OOS date) is located in it, giving the exact bar index. Use this
      whenever the panel is already loaded.
    * ``search_train_frac`` — applied by the validator to the panel it holds.
      Since the search never overrides ``train_frac``, this reproduces the
      split without pinning an index to a panel the caller has not seen yet.

    Artifacts that predate both fields (``window`` absent) get the protocol
    default — byte-for-byte the old behaviour, so replaying a historical
    artifact without a recorded split never silently changes its verdict.

    Args:
        payload: a ``top_formulas.json`` mapping.
        dates: optional iterable of the loaded panel's trade dates.
        min_train, fold_size, n_folds: explicit overrides (CLI flags).
    """
    window = payload.get("window")
    if not isinstance(window, dict):
        return FoldConfig(min_train=min_train, fold_size=fold_size, n_folds=n_folds)

    frac_raw = window.get("search_train_frac")
    try:
        frac = float(frac_raw) if frac_raw is not None else None
    except (TypeError, ValueError):
        frac = None
    if frac is not None and not 0.0 < frac < 1.0:
        frac = None

    end_raw = window.get("search_train_end")
    min_test_start = 0
    if dates is not None and end_raw is not None:
        cutoff = _naive_utc(end_raw)
        ordered = _naive_utc_index(dates)
        if cutoff is not None and len(ordered) > 0:
            min_test_start = int((ordered < cutoff).sum())

    if min_test_start > 0:
        return FoldConfig(
            min_train=min_train,
            fold_size=fold_size,
            n_folds=n_folds,
            anchor="end",
            min_test_start=min_test_start,
        )
    if frac is None:
        # Neither a resolvable split bar nor a usable ratio: keep the
        # historical geometry rather than invent a split the search never used.
        return FoldConfig(min_train=min_train, fold_size=fold_size, n_folds=n_folds)
    return FoldConfig(
        min_train=min_train,
        fold_size=fold_size,
        n_folds=n_folds,
        anchor="end",
        skip_search_train=True,
        search_train_frac=frac,
    )


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="T4 walk-forward validation report over the T3 top-N formulas."
    )
    parser.add_argument(
        "--top-formulas",
        default=str(DEFAULT_RESULTS_DIR / "top_formulas.json"),
        help=f"Path to top_formulas.json (default: {DEFAULT_RESULTS_DIR / 'top_formulas.json'}).",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_RESULTS_DIR / "validation_report.json"),
        help=f"Output report path (default: {DEFAULT_RESULTS_DIR / 'validation_report.json'}).",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"Path to klines.db (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--min-train",
        type=int,
        default=70,
        help="Expanding-window min train bars (default: 70).",
    )
    parser.add_argument(
        "--fold-size", type=int, default=20, help="OOS fold size in bars (default: 20)."
    )
    parser.add_argument(
        "--n-folds",
        type=int,
        default=5,
        help="Requested fold count (auto-reduced, default: 5).",
    )
    parser.add_argument(
        "--n-trials",
        type=int,
        default=0,
        help="DSR multiple-testing count; 0 uses top_formulas.json's evaluations_used (default: 0).",
    )
    parser.add_argument(
        "--force-synthetic",
        action="store_true",
        help="Always validate on synthetic AR(1) panels and record data_source=synthetic_fallback.",
    )
    parser.add_argument(
        "--strict-vocab",
        action="store_true",
        help="Reject top_formulas.json artifacts that carry no vocab_digest "
        "(strict audit mode). Default tolerates pre-digest artifacts.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    top = load_top_formulas(Path(args.top_formulas), strict_vocab=args.strict_vocab)
    records: list = top.get("top_formulas", [])
    symbols: list = top.get("symbols", [])
    interval: str = str(top.get("interval", "4h"))
    bars: int = int(top.get("bars_per_symbol", 150))
    win_start, win_end = window_of(top)
    n_trials: int = args.n_trials or int(top.get("evaluations_used") or 20)

    fold_config = fold_config_of(
        top,
        min_train=max(1, args.min_train),
        fold_size=max(1, args.fold_size),
        n_folds=max(1, args.n_folds),
    )

    # --- data source: real klines, or the honest synthetic fallback ----------
    data_source = "klines.db"
    fallback_reason: str | None = None
    if args.force_synthetic:
        data_source = "synthetic_fallback"
        fallback_reason = "forced via --force-synthetic"
    elif _has_inf_sentinels(records):
        data_source = "synthetic_fallback"
        fallback_reason = "top_formulas.json holds -inf/invalid score sentinels"
    elif not Path(args.db).exists():
        data_source = "synthetic_fallback"
        fallback_reason = f"klines.db missing: {args.db}"

    if data_source == "synthetic_fallback":
        datasets = fallback_synthetic_datasets(n_dates=max(200, bars), seed=42)
    else:
        try:
            datasets = build_datasets(
                Path(args.db), symbols, interval, bars, start=win_start, end=win_end
            )
        except (FileNotFoundError, ValueError) as exc:
            data_source = "synthetic_fallback"
            fallback_reason = f"klines.db load failed ({exc})"
            datasets = fallback_synthetic_datasets(n_dates=max(200, bars), seed=42)

    report = validate_formulas(
        records,
        datasets,
        n_trials=max(1, n_trials),
        fold_config=fold_config,
    )
    report.config["data_source"] = data_source
    if fallback_reason is not None:
        report.config["fallback_reason"] = fallback_reason

    out_path = write_report(report, Path(args.out))

    # A synthetic fallback is not a validation of the search: the panels are
    # generated AR(1) data, unrelated to the market the formulas were mined on.
    # ``data_source`` carries this into the JSON and the validation pane shows a
    # banner for it, but the CLI operator sees neither — say it here too.
    if data_source == "synthetic_fallback":
        print(
            "[validation] WARNING: synthetic fallback - this report was computed "
            "on GENERATED AR(1) panels, not on real klines. The verdicts below "
            "say nothing about live performance."
        )
        if fallback_reason is not None:
            print(f"[validation] WARNING: reason: {fallback_reason}")

    print(
        f"[validation] source={data_source} n_formulas={len(report.formulas)} "
        f"n_trials={n_trials} fold_config={fold_config.min_train}/"
        f"{fold_config.fold_size}/{fold_config.n_folds}"
    )
    for f in report.formulas:
        ic = (
            "n/a"
            if f.aggregate.oos_rank_ic is None
            else f"{f.aggregate.oos_rank_ic:.4f}"
        )
        dsr = "n/a" if f.aggregate.dsr is None else f"{f.aggregate.dsr:.4f}"
        print(
            f"[validation] {f.verdict:<16} ic={ic:<9} dsr={dsr:<9} "
            f"agree={f.aggregate.multi_symbol_agreement}  {f.readable}"
        )
    print(f"[validation] wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

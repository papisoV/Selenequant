# Cleanroom implementation: design intent derived from prior art; independent expression.
"""T5 VALID→FactorLifecycle registration bridge (candidate-only by default).

US-05 (T5) closes the loop: take a formula VERDICTED VALID by the T4
validation protocol (``validation_report.json``) and produce a **registration
candidate** — preprocessing → WeightDriftBacktest → ICPanel evidence — that
*waits for human confirmation*. Only an explicit ``confirm()`` (or the CLI
``--confirm`` flag) registers the formula into :class:`FactorLifecycle`
(via ``cycle()``) and returns its :class:`CycleReport`.

Registration discipline (HARD requirement):

- **A VALID verdict must NOT auto-register.** Building candidates performs zero
  FactorLifecycle side effects: no lifecycle is constructed, no state is
  advanced. ``FactorLifecycle`` is imported lazily inside ``confirm()`` only.
- ``confirm()`` is idempotent — a second call returns the stored report and
  does not advance the lifecycle twice.
- Each confirmed candidate registers into a *fresh* :class:`FactorLifecycle`
  (a new factor starts ``ACTIVE``), configured from the pipeline's
  ``lifecycle_kwargs`` (e.g. ``rolling_window`` / ``max_lag`` /
  ``baseline_half_life``).

Data-volume guard (the n_dates pit): ``load_pipeline_klines`` passes
``max_bars = 2 × target_bars`` to ``load_klines`` because klines.db stores
~50% duplicate timestamps per symbol — fetching 2× the target unique-bar count
yields ~the target unique bars instead of silently halving the evidence (the
validation CLI's direct ``bars_per_symbol`` pass-through is the known pit this
loader fixes).

Torch isolation is a HARD constraint: this module imports only pandas,
``fitness`` / ``weight_drift_backtest`` / ``ic_panel`` / ``runner`` (all
torch-free) and ``selene_space_factor_research.preprocessing.pipeline``;
``factor_lifecycle`` is imported lazily inside ``confirm()`` so
``test_no_torch_in_search_import`` stays green.

``python -m`` usage (from ``packages/factor-research``):

    python -m selene_space_factor_research.mining.pipeline \\
        --validation-report internal-archive/validation_report.json \\
        --target-bars 150                # candidate-only (no registration)
    python -m selene_space_factor_research.mining.pipeline \\
        --validation-report internal-archive/validation_report.json \\
        --target-bars 150 --confirm      # register VALID candidates
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# sys.path bootstrap: neither package is pip-installed; the CLI must run the
# same way pytest does (conftest inserts these dirs for the test suite).
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[4]
for _pkg_dir in (
    _REPO_ROOT / "packages" / "factor-research",
    _REPO_ROOT / "packages" / "core",
    _REPO_ROOT / "packages" / "alpha-core",
    # storage: ledger_bridge (research-ledger write/read) lives behind lazy
    # imports — the sys.path entry only makes the lazy import resolvable.
    _REPO_ROOT / "packages" / "storage",
):
    if str(_pkg_dir) not in sys.path:
        sys.path[:0] = [str(_pkg_dir)]

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from selene_space_factor_research.mining.fitness import build_factor_panel  # noqa: E402
from selene_space_factor_research.mining.ic_panel import ICPanel  # noqa: E402
from selene_space_factor_research.mining.runner import (  # noqa: E402
    _MARKET_TYPE,
    build_returns,
    load_klines,
)
from selene_space_factor_research.mining.weight_drift_backtest import (  # noqa: E402
    WeightDriftBacktest,
    WeightDriftConfig,
)
from selene_space_factor_research.preprocessing.pipeline import (  # noqa: E402
    PreprocessingPipeline,
)

from selene_space_core.kline_paths import default_klines_db_path  # noqa: E402

__all__ = [
    "DEFAULT_DB_PATH",
    "DEFAULT_RESULTS_DIR",
    "RegistrationCandidate",
    "RegistrationPipeline",
    "load_pipeline_klines",
    "main",
    "write_candidates",
]

#: Where ``validation_report.json`` is read and candidates land.
DEFAULT_RESULTS_DIR = _REPO_ROOT / "实验" / "alpha-mining"
#: Default klines.db (the T3 runner's data source).
DEFAULT_DB_PATH = default_klines_db_path()


# ---------------------------------------------------------------------------
# Candidate container.
# ---------------------------------------------------------------------------
@dataclass
class RegistrationCandidate:
    """A VALID formula pending human confirmation before lifecycle registration.

    Produced by :meth:`RegistrationPipeline.build_candidates` with zero
    FactorLifecycle side effects. ``confirm()`` is the only path that registers:
    it lazily constructs a fresh :class:`FactorLifecycle` (ACTIVE), runs one
    full ``cycle()`` on the aligned panels, and stores the returned
    :class:`CycleReport`.
    """

    tokens: list[int]
    readable: str
    canonical_hash: str | None
    verdict: str
    factor_df: pd.DataFrame
    ret_df: pd.DataFrame
    cleaned_factor_df: pd.DataFrame
    trace_hash: str
    backtest: dict[str, float]
    ic_panel: dict[str, float]
    preprocessing_config: Any
    backtest_config: WeightDriftConfig
    lifecycle_kwargs: dict[str, Any]
    confirmed: bool = False
    cycle_report: Any = None
    _lifecycle: Any = None
    #: Episode id of the ledger entry this candidate duplicates (known-formula
    #: hit during build_candidates). ``None`` = new formula, nothing on record.
    duplicate_of: str | None = None

    def confirm(self) -> Any:
        """Register this formula into FactorLifecycle (idempotent).

        Lazy-imports :class:`FactorLifecycle` (keeps the top-level search chain
        torch-free), runs one full ``cycle()`` on the aligned panels, and stores
        the :class:`CycleReport`. Returns the report; a second call returns the
        stored report without advancing the lifecycle again.
        """
        if self.confirmed:
            return self.cycle_report
        from selene_space_alpha.factor_lifecycle import FactorLifecycle

        lc = FactorLifecycle(
            preprocessing_config=self.preprocessing_config,
            backtest_config=self.backtest_config,
            **self.lifecycle_kwargs,
        )
        report = lc.cycle(self.factor_df, self.ret_df)
        self._lifecycle = lc
        self.cycle_report = report
        self.confirmed = True
        return report

    def to_dict(self) -> dict:
        """JSON-safe candidate summary (backtest/IC evidence + confirm status)."""
        return {
            "readable": self.readable,
            "tokens": [int(t) for t in self.tokens],
            "canonical_hash": self.canonical_hash,
            "verdict": self.verdict,
            "confirmed": self.confirmed,
            "trace_hash": self.trace_hash,
            "duplicate_of": self.duplicate_of,
            "n_factor_rows": int(len(self.factor_df)),
            "backtest": {k: _jsonable(v) for k, v in self.backtest.items()},
            "ic_panel": {k: _jsonable(v) for k, v in self.ic_panel.items()},
        }


# ---------------------------------------------------------------------------
# Small helpers.
# ---------------------------------------------------------------------------
def _jsonable(x: Any) -> Any:
    """Convert a metric value to a JSON-safe scalar (NaN/±inf -> None)."""
    if isinstance(x, (np.floating, float)):
        v = float(x)
        return None if not math.isfinite(v) else v
    if isinstance(x, (np.integer, int)):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def _entry_to_dict(entry: Any) -> dict:
    """Normalize a formula record (dict or ``FormulaValidation``) to a dict."""
    if isinstance(entry, dict):
        return entry
    if hasattr(entry, "to_dict"):
        # to_dict() is untyped upstream (Any); it always returns a dict.
        record: dict = entry.to_dict()
        return record
    raise TypeError(f"cannot extract formula record from {type(entry).__name__}")


def _formula_records(report: Any) -> list[dict]:
    """Normalize a :class:`ValidationReport` (or its JSON payload) to records.

    Accepts the report object itself (``.formulas``) or the ``to_dict()`` JSON
    payload shape (``{"formulas": [...]}``).
    """
    if isinstance(report, dict):
        formulas = report.get("formulas")
        if not isinstance(formulas, list):
            raise ValueError("report payload missing a 'formulas' list")
        return [_entry_to_dict(f) for f in formulas]
    return [_entry_to_dict(f) for f in report.formulas]


def _align_panels(
    factor_df: pd.DataFrame,
    ret_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Align the factor/return panels to the usable (date, symbol) rows.

    ``build_factor_panel`` keeps the last bar per symbol in ``ret_df`` with a
    NaN forward return (no future bar). :meth:`FactorLifecycle.cycle` requires
    the two panels to have matching per-symbol lengths (its IC-decay monitor
    asserts equal signal/return lengths and its attribution ``lstsq`` cannot
    take NaN rows), so drop the NaN-return rows and the factor rows that lose
    their return. This mirrors what ``WeightDriftBacktest`` does internally
    (it drops NaN forward returns), so the backtest evidence is unchanged.
    """
    ret = ret_df.dropna(subset=["fut_ret_1"]).copy()
    keys = set(zip(pd.to_datetime(ret["trade_date"]), ret["symbol"]))
    fac = factor_df[
        [
            (pd.Timestamp(d), s) in keys
            for d, s in zip(factor_df["trade_date"], factor_df["symbol"])
        ]
    ].copy()
    return fac, ret


def _backtest_evidence(
    factor_df: pd.DataFrame,
    ret_df: pd.DataFrame,
    config: WeightDriftConfig,
) -> dict[str, float]:
    """WeightDriftBacktest LS stats + turnover as candidate review evidence.

    Best-effort: a degenerate panel (empty, single date, constant factor) is
    recorded as ``{}`` rather than raising — the candidate still carries the
    formula; the reviewer sees the missing evidence.
    """
    try:
        wd = WeightDriftBacktest().run(factor_df, ret_df, config)
        stats: dict[str, float] = {k: float(v) for k, v in wd.stats_ls.items()}
        stats["turnover_ls"] = float(wd.avg_turnover_ls)
        return stats
    except Exception:
        return {}


def _ic_evidence(factor_df: pd.DataFrame, ret_df: pd.DataFrame) -> dict[str, float]:
    """ICPanel diagnostics as candidate review evidence (best-effort)."""
    try:
        ic = ICPanel().compute(factor_df, ret_df)
        dist = ic.ic_distribution
        return {
            "mean_ic": float(dist.get("Mean IC", float("nan"))),
            "ic_std": float(dist.get("Std", float("nan"))),
            "ir": float(dist.get("IR", float("nan"))),
            "n_ic_obs": int(len(ic.rolling_ic.dropna())),
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Pipeline.
# ---------------------------------------------------------------------------
class RegistrationPipeline:
    """VALID → registration-candidate bridge (default: candidate-only)."""

    def __init__(
        self,
        *,
        preprocessing_config: Any = None,
        backtest_config: WeightDriftConfig | None = None,
        lifecycle_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.preprocessing_config = (
            preprocessing_config if preprocessing_config is not None else []
        )
        self.backtest_config = backtest_config or WeightDriftConfig()
        self.lifecycle_kwargs = dict(lifecycle_kwargs or {})

    def build_candidates(
        self,
        report: Any,
        data_by_symbol: dict[str, pd.DataFrame],
        ret_by_symbol: dict,
        *,
        only_valid: bool = True,
        known_formulas: dict[str, dict] | None = None,
    ) -> list[RegistrationCandidate]:
        """Build registration candidates for the VALID formulas in ``report``.

        Args:
            report: a :class:`ValidationReport` or its JSON payload.
            data_by_symbol: ``{symbol: DataFrame}`` (OHLCV + DatetimeIndex) —
                the same contract ``build_factor_panel`` expects.
            ret_by_symbol: ``{symbol: Series | DataFrame}`` forward returns.
            only_valid: when True (default), only ``verdict == "VALID"``
                formulas become candidates — the registration discipline.
            known_formulas: Optional ``{canonical_hash: entry}`` from the
                research ledger (``ledger_bridge.load_known_formulas``). A
                candidate whose ``canonical_hash`` is a key here gets
                ``duplicate_of`` = the entry's ``episode_id``. Formulas with
                ``canonical_hash=None`` (parse failure) skip the lookup.

        Returns:
            Pending candidates (``confirmed=False``). No FactorLifecycle object
            is constructed or advanced — registration happens only on
            :meth:`RegistrationCandidate.confirm`.
        """
        candidates: list[RegistrationCandidate] = []
        for entry in _formula_records(report):
            if only_valid and entry.get("verdict") != "VALID":
                continue
            tokens = [int(t) for t in entry["tokens"]]
            factor_df, ret_df = build_factor_panel(
                tokens, data_by_symbol, ret_by_symbol
            )
            if factor_df.empty:
                continue
            factor_df, ret_df = _align_panels(factor_df, ret_df)
            cleaned = PreprocessingPipeline.from_config(self.preprocessing_config).run(
                factor_df
            )
            entry_hash = entry.get("canonical_hash")
            duplicate_of = (
                known_formulas.get(entry_hash, {}).get("episode_id")
                if entry_hash is not None and known_formulas
                else None
            )
            candidates.append(
                RegistrationCandidate(
                    tokens=tokens,
                    readable=str(entry.get("readable", "<unknown>")),
                    canonical_hash=entry.get("canonical_hash"),
                    verdict=str(entry.get("verdict", "VALID")),
                    factor_df=factor_df,
                    ret_df=ret_df,
                    cleaned_factor_df=cleaned.df,
                    trace_hash=cleaned.trace_hash,
                    backtest=_backtest_evidence(
                        cleaned.df, ret_df, self.backtest_config
                    ),
                    ic_panel=_ic_evidence(cleaned.df, ret_df),
                    preprocessing_config=self.preprocessing_config,
                    backtest_config=self.backtest_config,
                    lifecycle_kwargs=dict(self.lifecycle_kwargs),
                    duplicate_of=duplicate_of,
                )
            )
        return candidates


# ---------------------------------------------------------------------------
# Klines loading (n_dates pit calibration).
# ---------------------------------------------------------------------------
def load_pipeline_klines(
    db_path: Path,
    symbols: list[str],
    interval: str,
    *,
    target_bars: int = 150,
    market_type: str = _MARKET_TYPE,
) -> dict[str, pd.DataFrame]:
    """Load klines calibrated to a *unique* bar count (the n_dates pit fix).

    The loader filters to a single ``market_type`` partition; the old ~50%
    duplicate-timestamp comment was caused by spot/futures rows sharing
    (symbol, interval). Fetching ``2 × target_bars`` rows from one partition
    yields ~``target_bars`` unique bars. The validation CLI's direct
    ``bars_per_symbol`` pass-through silently halves the evidence; this loader
    is the pipeline's honest equivalent.
    """
    return load_klines(
        db_path,
        symbols,
        interval,
        max_bars=max(1, 2 * max(1, int(target_bars))),
        market_type=market_type,
    )


# ---------------------------------------------------------------------------
# Candidate persistence (atomic).
# ---------------------------------------------------------------------------
def write_candidates(
    candidates: list[RegistrationCandidate],
    path: Path,
    *,
    generated_at: str | None = None,
) -> Path:
    """Atomically persist the pending/confirmed registration candidates."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": generated_at or datetime.now().astimezone().isoformat(),
        "n_candidates": int(len(candidates)),
        "n_confirmed": int(sum(1 for c in candidates if c.confirmed)),
        "candidates": [c.to_dict() for c in candidates],
    }
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
# CLI.
# ---------------------------------------------------------------------------
def _report_datasets(report_payload: dict) -> dict:
    cfg = report_payload.get("config") or {}
    datasets = cfg.get("datasets") or {}
    return datasets if isinstance(datasets, dict) else {}


def _symbols_from_report(report_payload: dict) -> list[str]:
    for ds in _report_datasets(report_payload).values():
        syms = ds.get("symbols")
        if syms:
            return [str(s) for s in syms]
    return ["BTC/USDT"]


def _interval_from_report(report_payload: dict) -> str:
    datasets = _report_datasets(report_payload)
    if datasets:
        return str(next(iter(datasets)))
    return "4h"


def _parse_symbols(raw: str) -> list[str]:
    symbols = [s.strip() for s in raw.split(",") if s.strip()]
    if not symbols:
        raise ValueError("--symbols must be a non-empty comma-separated list")
    return symbols


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "T5 VALID→registration bridge: build pending candidates from the "
            "T4 validation_report.json (no registration by default)."
        )
    )
    parser.add_argument(
        "--validation-report",
        default=str(DEFAULT_RESULTS_DIR / "validation_report.json"),
        help=(
            "Path to validation_report.json (default: "
            f"{DEFAULT_RESULTS_DIR / 'validation_report.json'})."
        ),
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"Path to klines.db (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--target-bars",
        type=int,
        default=150,
        help=(
            "Target *unique* bars per symbol; max_bars passed to load_klines is "
            "2× this because klines.db dedupes to ~50% (default: 150)."
        ),
    )
    parser.add_argument(
        "--interval",
        default=None,
        help="Kline interval (default: the report config's first dataset key).",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated symbols (default: from the report config).",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help=(
            "Confirm all VALID candidates: register each into FactorLifecycle "
            "and print its CycleReport. Without this flag the run is "
            "candidate-only (zero FactorLifecycle side effects)."
        ),
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_RESULTS_DIR / "registration_candidates.json"),
        help=(
            "Candidate JSON output path (default: "
            f"{DEFAULT_RESULTS_DIR / 'registration_candidates.json'})."
        ),
    )
    parser.add_argument(
        "--ledger-db",
        default=None,
        help=(
            "Research-ledger SQLite path for known-formula lookup and episode "
            "registration on --confirm. Defaults to the user-level ledger at "
            "~/.selenespace/data/selenespace.db; pass 'off' to disable "
            "all ledger access (candidate JSON marks nothing duplicate, "
            "confirm does not write episodes)."
        ),
    )
    parser.add_argument(
        "--vocab-digest",
        default=None,
        help=(
            "FORMULA_VOCAB.vocab_digest for ledger identity (W7 dual key). "
            "Defaults to the installed vocab's digest. Known-formula lookup "
            "only trusts entries minted under the same digest."
        ),
    )
    return parser


def _resolve_ledger_db(raw: str | None) -> Path | None:
    """CLI --ledger-db → Path. Default ON (plan v3): the user-level ledger
    db unless explicitly disabled with 'off' — write-path-first means a bare
    ``--confirm`` run records episodes, not silently does nothing."""
    if raw is not None and raw.strip().lower() == "off":
        return None
    if raw is None:
        from selene_space_factor_research.mining import ledger_bridge

        return ledger_bridge.DEFAULT_LEDGER_DB
    return Path(raw).expanduser()


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    report_path = Path(args.validation_report)
    if not report_path.exists():
        raise FileNotFoundError(f"validation report not found: {report_path}")
    report_payload = json.loads(report_path.read_text(encoding="utf-8"))

    symbols = (
        _symbols_from_report(report_payload)
        if args.symbols is None
        else _parse_symbols(args.symbols)
    )
    interval = (
        _interval_from_report(report_payload)
        if args.interval is None
        else args.interval
    )

    # Ledger access is lazy and opt-out-able: default db unless 'off'.
    from selene_space_factor_research.mining import ledger_bridge

    ledger_db = _resolve_ledger_db(args.ledger_db)
    if ledger_db is None:
        known_formulas: dict[str, dict] | None = None
        vocab_digest: str | None = None
    else:
        from selene_space_alpha.vocab import FORMULA_VOCAB

        vocab_digest = args.vocab_digest or FORMULA_VOCAB.vocab_digest
        known_formulas = ledger_bridge.load_known_formulas(
            ledger_db, vocab_digest=vocab_digest
        )
        print(
            f"[pipeline] ledger: {ledger_db} known_formulas="
            f"{len(known_formulas)} vocab_digest={vocab_digest}"
        )

    data_by_symbol = load_pipeline_klines(
        Path(args.db), symbols, interval, target_bars=args.target_bars
    )
    for sym, df in data_by_symbol.items():
        print(f"[pipeline] {sym}: {len(df)} unique bars")
    ret_by_symbol = build_returns(data_by_symbol)

    pipeline = RegistrationPipeline()
    candidates = pipeline.build_candidates(
        report_payload, data_by_symbol, ret_by_symbol, known_formulas=known_formulas
    )
    print(
        f"[pipeline] source={args.db} interval={interval} "
        f"target_bars={args.target_bars} n_valid_candidates={len(candidates)}"
    )
    for c in candidates:
        mean_ic = c.ic_panel.get("mean_ic")
        ic_str = "n/a" if mean_ic is None else f"{mean_ic:.4f}"
        sharpe = c.backtest.get("Ann. Sharpe")
        sharpe_str = "n/a" if sharpe is None else f"{sharpe:.4f}"
        dup = "" if c.duplicate_of is None else f" duplicate_of={c.duplicate_of}"
        print(
            f"[pipeline] candidate confirmed={c.confirmed} ic={ic_str} "
            f"sharpe={sharpe_str} hash={c.canonical_hash}{dup} {c.readable}"
        )

    if args.confirm:
        for c in candidates:
            report = c.confirm()
            print(
                f"[pipeline] CONFIRMED {c.canonical_hash}: "
                f"{report.state_before.value} -> {report.state_after.value} "
                f"decision={report.decision} reason={report.reason}"
            )
            if ledger_db is None or c.canonical_hash is None:
                continue
            # Module-attribute call (not from-import): monkeypatching
            # ledger_bridge.register_formula_episode must stay possible —
            # a bound reference would silently defeat that contract.
            try:
                result = ledger_bridge.register_formula_episode(
                    ledger_db,
                    canonical_hash=c.canonical_hash,
                    vocab_digest=vocab_digest,
                    formula_tokens=[int(t) for t in c.tokens],
                    readable=c.readable,
                    source="registration_pipeline",
                    verdict=c.verdict,
                    metrics={"mean_ic": c.ic_panel.get("mean_ic")},
                )
                print(
                    f"[pipeline] ledger episode {result['new_episode_id']} "
                    f"recorded for {c.canonical_hash}"
                )
            except Exception as exc:  # noqa: BLE001 — H5: registration failure
                # must not lose the confirm result; stderr leaves a manual
                # backfill key (canonical_hash).
                print(
                    f"[pipeline] ERROR: ledger registration failed for "
                    f"canonical_hash={c.canonical_hash}: {exc} — "
                    f"episode not recorded",
                    file=sys.stderr,
                )
        print(
            f"[pipeline] registered {sum(1 for c in candidates if c.confirmed)}/"
            f"{len(candidates)} candidates into FactorLifecycle"
        )

    out_path = write_candidates(candidates, Path(args.out))
    print(f"[pipeline] wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

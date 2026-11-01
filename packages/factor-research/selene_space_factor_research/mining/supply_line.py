# Cleanroom implementation: design intent derived from prior art; independent expression.
"""Supply line: recovery -> shape gates (G1-G6) -> T4 validation -> report.

One pass over each configured source, a T3 ``top_formulas.json`` artifact::

    recover   load_top_formulas()      top-N candidates + artifact provenance
    G1        vocab digest recompute   the artifact was searched on THIS vocab
    G5        timeframe/filename agree the file is what its name says it is
    G4        panel loadable           the data the search ran on still exists
    G2/G3/G6  per-candidate shape      fields present, tokens in-vocab + valid
                                       RPN, panel long enough to decide
    validate  validate_formulas()      T4 walk-forward, once per source
    report    write_report()           the artifact the deploy gate reads

The gate layer is deliberately *shape-only*: it decides whether a candidate may
be handed to the T4 contract at all, never how good it is. Quality is the T4
verdict's job, and a gate that guessed at it would be a second, worse judge.

Gate scope follows the nature of the check. G1/G4/G5 are source gates — a
failure means the whole artifact is unusable, so none of its candidates enter
the funnel. G2/G3/G6 are candidate gates. G6 *defers* rather than rejects: a
panel shorter than a year is not evidence against a formula, it is the absence
of evidence, so the candidate leaves the funnel undecided — the same
``INSUFFICIENT_DATA -> defer`` shape the protocol's own ``_ACTION_BY_VERDICT``
uses.

Validation runs once per source, each with that source's own search count. DSR's
multiple-testing correction is a function of how many candidates were searched;
pooling sources with different search counts into a single call would misstate
the correction for all of them.

Run as ``python -m selene_space_factor_research.mining.supply_line``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from selene_space_alpha.vocab import FORMULA_VOCAB
from selene_space_factor_research.mining.report import (
    DEFAULT_DB_PATH,
    DEFAULT_RESULTS_DIR,
    build_datasets,
    fold_config_of,
    load_top_formulas,
    window_of,
    write_report,
)
from selene_space_factor_research.mining.structure_validation import (
    validate as validate_structure,
)
from selene_space_factor_research.mining.validation_protocol import (
    FoldConfig,
    ValidationReport,
    validate_formulas,
)

__all__ = [
    "DEFAULT_REPORT_PATH",
    "DEFAULT_TOP_N",
    "GateFinding",
    "GateFire",
    "SourceOutcome",
    "SourceSpec",
    "SupplyLineResult",
    "gate_datasets",
    "gate_panel_span",
    "gate_required_fields",
    "gate_timeframe_consistency",
    "gate_token_bounds",
    "gate_vocab_digest",
    "interval_from_filename",
    "main",
    "min_panel_span_days",
    "run_supply_line",
]

#: Where the deploy gate expects to find the T4 report.
DEFAULT_REPORT_PATH = DEFAULT_RESULTS_DIR / "validation_report.json"
#: Candidates carried per source when the caller does not narrow it further.
DEFAULT_TOP_N = 20
#: DSR search-count fallback for artifacts that record no ``evaluations_used``.
_DEFAULT_N_TRIALS = 20
#: Panel defaults for artifacts that record no ``interval``/``bars_per_symbol``
#: — the same values ``report.main`` falls back to.
_DEFAULT_INTERVAL = "4h"
_DEFAULT_BARS = 150
#: T4 geometry defaults. ``--min-train/--fold-size/--n-folds`` share these;
#: passing them unchanged means "use the artifact's recorded split".
_DEFAULT_MIN_TRAIN = 70
_DEFAULT_FOLD_SIZE = 20
_DEFAULT_N_FOLDS = 5

#: A candidate whose shortest panel spans less than this is undecidable, not
#: wrong — see G6.
_MIN_SPAN_DAYS = 365.0

#: Record fields the T4 pass consumes. ``validate_formulas`` derives
#: ``readable`` and ``canonical_hash`` from the tokens itself, so ``tokens`` is
#: the entire contract; it is still worth naming because a record missing it
#: would otherwise land in the report as an anonymous ``<unparseable>`` row.
_REQUIRED_RECORD_FIELDS: tuple[str, ...] = ("tokens",)

#: Bar intervals recognised when reading a timeframe back out of a filename.
_KNOWN_INTERVALS: frozenset[str] = frozenset(
    {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"}
)

#: Gates whose failure defers a candidate instead of rejecting it.
_DEFERRAL_GATES: frozenset[str] = frozenset({"G6"})


# ---------------------------------------------------------------------------
# Gate vocabulary.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GateFire:
    """A gate that fired. ``gate`` is ``"G1"``..``"G6"``."""

    gate: str
    reason: str


@dataclass(frozen=True)
class GateFinding:
    """A fired gate pinned to the subject it fired on."""

    gate: str
    subject: str
    reason: str

    @classmethod
    def of(cls, fire: GateFire, subject: str) -> GateFinding:
        return cls(gate=fire.gate, subject=subject, reason=fire.reason)


# ---------------------------------------------------------------------------
# Run geometry.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SourceSpec:
    """One ``top_formulas.json`` artifact and the label it reports under."""

    path: Path
    label: str | None = None

    @property
    def resolved_label(self) -> str:
        return self.label or Path(self.path).stem


@dataclass(frozen=True)
class SourceOutcome:
    """One source's journey through the funnel."""

    label: str
    path: Path
    #: Candidates read from the artifact, after the top-N cut.
    recovered: int
    #: Candidates that cleared every gate and were handed to T4.
    gated_in: int
    #: Search count attributed to this source (the DSR multiple-testing input).
    n_trials: int
    interval: str | None
    rejected: tuple[GateFinding, ...] = ()
    deferred: tuple[GateFinding, ...] = ()


@dataclass(frozen=True)
class _SourcePass:
    """Internal: one source's outcome plus what the validate stage needs."""

    outcome: SourceOutcome
    records: list[dict[str, Any]]
    datasets: dict[str, tuple[dict[str, Any], dict[str, Any]]] | None
    #: Geometry resolved from *this* artifact's recorded search split. Sources
    #: are validated separately, so each one brings its own train window.
    fold_config: FoldConfig | None = None


@dataclass
class SupplyLineResult:
    """The T4 report plus the provenance of every source that fed it."""

    report: ValidationReport
    sources: list[SourceOutcome]
    report_path: Path | None = None

    @property
    def findings(self) -> list[GateFinding]:
        """Every rejected and deferred candidate, in source order."""
        out: list[GateFinding] = []
        for source in self.sources:
            out.extend(source.rejected)
            out.extend(source.deferred)
        return out

    @property
    def deferred(self) -> list[GateFinding]:
        return [f for source in self.sources for f in source.deferred]

    @property
    def rejected(self) -> list[GateFinding]:
        return [f for source in self.sources for f in source.rejected]


# ---------------------------------------------------------------------------
# G1 — vocab digest. The artifact's fingerprint must recompute to the current
# vocab's; a mismatch means its token ids index a different name table, so
# every id downstream would be read as the wrong feature.
#
# Two digests, because one is not enough. `vocab_digest` covers only the shared
# 77-feature prefix plus the operators (W-tree compatibility), so it cannot see
# a feature appended to the tail — and such an append renumbers every operator
# id without touching the shared segment. `token_layout_digest` covers the
# complete token list and therefore does move. An artifact missing the layout
# digest predates this check and is tolerated, matching FormulaVocab.verify.
# ---------------------------------------------------------------------------
def gate_vocab_digest(payload: dict[str, Any]) -> GateFire | None:
    recorded = payload.get("vocab_digest")
    if not recorded:
        return GateFire(
            "G1", "G1 vocab digest absent: cannot recompute the artifact's vocab"
        )
    recomputed = FORMULA_VOCAB.vocab_digest
    if str(recorded) != recomputed:
        return GateFire(
            "G1",
            f"G1 vocab digest mismatch: artifact={recorded} recomputed={recomputed}",
        )
    recorded_layout = payload.get("token_layout_digest")
    if recorded_layout:
        recomputed_layout = FORMULA_VOCAB.token_layout_digest
        if str(recorded_layout) != recomputed_layout:
            return GateFire(
                "G1",
                "G1 token layout digest mismatch: artifact="
                f"{recorded_layout} recomputed={recomputed_layout} — the "
                "feature/operator boundary moved, so every operator token would "
                "decode as a different name",
            )
    return None


# ---------------------------------------------------------------------------
# G5 — the filename's timeframe must agree with the payload's. A results file
# named for one interval while declaring another is the failure that puts a 1h
# search's numbers under a 4h label.
# ---------------------------------------------------------------------------
def interval_from_filename(path: Path) -> str | None:
    """Timeframe encoded in a results filename (``top_formulas_4h.json`` -> 4h).

    ``None`` when the name carries no recognisable interval, which is the
    normal case for the default ``top_formulas.json``.
    """
    for token in reversed(Path(path).stem.split("_")):
        if token in _KNOWN_INTERVALS:
            return token
    return None


def gate_timeframe_consistency(path: Path, payload: dict[str, Any]) -> GateFire | None:
    named = interval_from_filename(path)
    if named is None:
        return None
    declared = payload.get("interval")
    if declared is None:
        return GateFire(
            "G5", f"G5 payload declares no interval while the filename says {named}"
        )
    if str(declared) != named:
        return GateFire(
            "G5",
            f"G5 timeframe mismatch: filename says {named}, payload says {declared}",
        )
    return None


# ---------------------------------------------------------------------------
# G4 — the panel the search ran on must still load. Without it the candidate
# cannot be validated at all, so this is a source gate.
# ---------------------------------------------------------------------------
def gate_datasets(
    db_path: Path,
    symbols: Sequence[str],
    interval: str,
    bars: int,
    *,
    start: str | None = None,
    end: str | None = None,
) -> tuple[dict[str, tuple[dict[str, Any], dict[str, Any]]] | None, GateFire | None]:
    """Load the declared panel, or explain why it is unavailable.

    Returns ``(datasets, None)`` on success and ``(None, fire)`` otherwise —
    the datasets are the input G6 and the T4 pass both need, so they come back
    through the gate rather than being loaded a second time.

    ``start``/``end`` come from the artifact's recorded window: validating on
    "the newest N bars" while the search ran on a pinned window would judge the
    formula on a sample it never saw.
    """
    if not symbols:
        return None, GateFire("G4", "G4 artifact declares no symbols to validate on")
    try:
        datasets = build_datasets(
            Path(db_path), list(symbols), interval, bars, start=start, end=end
        )
    except (FileNotFoundError, ValueError) as exc:
        return None, GateFire("G4", f"G4 panel unavailable: {exc}")
    return datasets, None


# ---------------------------------------------------------------------------
# G2 — a record must at least be a record with tokens in it.
# ---------------------------------------------------------------------------
def gate_required_fields(record: Any) -> GateFire | None:
    if not isinstance(record, dict):
        return GateFire(
            "G2", f"G2 record is {type(record).__name__}, expected an object"
        )
    for name in _REQUIRED_RECORD_FIELDS:
        if name not in record:
            return GateFire("G2", f"G2 required field missing: {name}")
    tokens = record.get("tokens")
    if not isinstance(tokens, (list, tuple)) or not tokens:
        return GateFire("G2", "G2 required field empty or not a sequence: tokens")
    return None


# ---------------------------------------------------------------------------
# G3 — token bounds plus RPN structure. Bounds first: an out-of-range id is a
# vocab disagreement and deserves to be named as one rather than surfacing as a
# generic arity complaint.
# ---------------------------------------------------------------------------
def gate_token_bounds(tokens: Sequence[Any]) -> GateFire | None:
    size = FORMULA_VOCAB.size
    for index, token in enumerate(tokens):
        if isinstance(token, bool) or not isinstance(token, int):
            return GateFire(
                "G3", f"G3 token[{index}] is {type(token).__name__}, expected int"
            )
        if not 0 <= token < size:
            return GateFire(
                "G3", f"G3 token[{index}]={token} outside vocab range [0, {size})"
            )
    # Structural check via the existing advisory module. Its infection threshold
    # is raised above the token count so only the RPN reasons can fire — the
    # always-positive-run signal is advisory and belongs to the fitness layer.
    result = validate_structure([int(t) for t in tokens], threshold=max(1, len(tokens)))
    if not result.valid:
        return GateFire("G3", "G3 invalid RPN sequence: " + "; ".join(result.reasons))
    return None


# ---------------------------------------------------------------------------
# G6 — defer, don't kill, on too little history.
# ---------------------------------------------------------------------------
def min_panel_span_days(
    datasets: dict[str, tuple[dict[str, Any], dict[str, Any]]],
) -> float | None:
    """Shortest span across every symbol in every dataset, in days.

    ``None`` when no frame carries a usable time index. The minimum is the
    conservative reading: a candidate is only decidable once *every* market in
    its panel has the history to decide on.
    """
    spans: list[float] = []
    for data_by_symbol, _ret in datasets.values():
        for frame in data_by_symbol.values():
            index = getattr(frame, "index", None)
            if index is None or len(index) < 2:
                continue
            spans.append((index[-1] - index[0]).total_seconds() / 86400.0)
    return min(spans) if spans else None


def gate_panel_span(
    datasets: dict[str, tuple[dict[str, Any], dict[str, Any]]],
) -> GateFire | None:
    span = min_panel_span_days(datasets)
    if span is None or span >= _MIN_SPAN_DAYS:
        return None
    return GateFire(
        "G6",
        f"G6 panel span {span:.0f}d < {_MIN_SPAN_DAYS:.0f}d: deferred, not enough history to decide",
    )


# ---------------------------------------------------------------------------
# Per-source pass.
# ---------------------------------------------------------------------------
def _n_trials_of(payload: dict[str, Any]) -> int:
    raw = payload.get("evaluations_used")
    try:
        value = int(raw) if raw is not None else _DEFAULT_N_TRIALS
    except (TypeError, ValueError):
        value = _DEFAULT_N_TRIALS
    return max(1, value)


def _outcome(
    spec: SourceSpec,
    *,
    recovered: int,
    gated_in: int,
    n_trials: int,
    interval: str | None,
    rejected: list[GateFinding],
    deferred: list[GateFinding],
) -> SourceOutcome:
    return SourceOutcome(
        label=spec.resolved_label,
        path=Path(spec.path),
        recovered=recovered,
        gated_in=gated_in,
        n_trials=n_trials,
        interval=interval,
        rejected=tuple(rejected),
        deferred=tuple(deferred),
    )


def _pass_source(
    spec: SourceSpec,
    *,
    top_n: int,
    db_path: Path,
    fold_override: FoldConfig | None = None,
) -> _SourcePass:
    """Run one source through recover -> G1/G5/G4 -> G2/G3/G6."""
    label = spec.resolved_label
    rejected: list[GateFinding] = []
    deferred: list[GateFinding] = []

    def stop(fire: GateFire, *, recovered: int = 0, n_trials: int = 0) -> _SourcePass:
        rejected.append(GateFinding.of(fire, label))
        return _SourcePass(
            _outcome(
                spec,
                recovered=recovered,
                gated_in=0,
                n_trials=n_trials,
                interval=None,
                rejected=rejected,
                deferred=deferred,
            ),
            [],
            None,
        )

    try:
        # G1 owns the vocab decision, so the loader is asked not to pre-empt it
        # with a "recover failed" attribution for the same mismatch.
        payload = load_top_formulas(Path(spec.path), verify_vocab=False)
    except (OSError, ValueError, TypeError) as exc:
        return stop(GateFire("recover", f"recover failed: {exc}"))

    raw_records = payload.get("top_formulas")
    if not isinstance(raw_records, list):
        return stop(
            GateFire(
                "recover",
                f"recover failed: top_formulas is {type(raw_records).__name__}",
            )
        )

    records: list[Any] = list(raw_records)[: max(1, top_n)]
    n_trials = _n_trials_of(payload)
    interval = payload.get("interval")

    # -- source gates, cheapest first: no point loading a panel for an artifact
    #    that already disagrees with this vocab or with its own name.
    for fire in (
        gate_vocab_digest(payload),
        gate_timeframe_consistency(Path(spec.path), payload),
    ):
        if fire is not None:
            return stop(fire, recovered=len(records), n_trials=n_trials)

    win_start, win_end = window_of(payload)
    datasets, fire = gate_datasets(
        db_path,
        payload.get("symbols") or [],
        str(interval or _DEFAULT_INTERVAL),
        int(payload.get("bars_per_symbol") or _DEFAULT_BARS),
        start=win_start,
        end=win_end,
    )
    if fire is not None:
        return stop(fire, recovered=len(records), n_trials=n_trials)
    if datasets is None:
        return stop(
            GateFire("G4", "G4 panel load reported success but returned no datasets"),
            recovered=len(records),
            n_trials=n_trials,
        )

    # -- candidate gates.
    candidates: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        fire = gate_required_fields(record)
        if fire is None and isinstance(record, dict):
            fire = gate_token_bounds(record["tokens"])
        if fire is None:
            fire = gate_panel_span(datasets)
        if fire is None:
            candidates.append(record)
            continue
        finding = GateFinding.of(fire, _subject_of(record, label, index))
        if fire.gate in _DEFERRAL_GATES:
            deferred.append(finding)
        else:
            rejected.append(finding)

    return _SourcePass(
        _outcome(
            spec,
            recovered=len(records),
            gated_in=len(candidates),
            n_trials=n_trials,
            interval=str(interval) if interval is not None else None,
            rejected=rejected,
            deferred=deferred,
        ),
        candidates,
        datasets,
        _fold_config_for(payload, datasets, fold_override),
    )


def _fold_config_for(
    payload: dict[str, Any],
    datasets: dict[str, tuple[dict[str, Any], dict[str, Any]]] | None,
    override: FoldConfig | None,
) -> FoldConfig | None:
    """Resolve this artifact's walk-forward geometry.

    An explicit ``override`` (CLI flags, tests) wins outright. Otherwise the
    geometry comes from the artifact: its recorded ``search_train_end`` is
    matched against the loaded panel's date axis, so the folds start on the
    first bar the search never fitted. Artifacts with no recorded split keep
    the protocol default — their historical verdicts do not change.
    """
    if override is not None:
        return override
    if datasets is None:
        return None
    dates: list[Any] = []
    for data_by_symbol, _ in datasets.values():
        for frame in data_by_symbol.values():
            if len(frame) > 0:
                dates.extend(frame.index)
    if not dates:
        return None
    return fold_config_of(payload, dates=dates)


def _subject_of(record: Any, label: str, index: int) -> str:
    """Best available name for a gated candidate, falling back to its position."""
    if isinstance(record, dict):
        for key in ("readable", "canonical_hash"):
            value = record.get(key)
            if value:
                return str(value)
    return f"{label}#{index}"


# ---------------------------------------------------------------------------
# Validate + report.
# ---------------------------------------------------------------------------
def _validate_groups(
    passes: Sequence[_SourcePass],
    fold_config: FoldConfig | None,
) -> ValidationReport:
    """One T4 call per source, each carrying its own search count.

    The calls are kept separate on purpose: ``n_trials`` is a scalar DSR
    multiple-testing count, so candidates searched a different number of times
    must not share one. Each call's own config is kept under ``groups`` rather
    than flattened to the top level — ``datasets`` and ``n_trials`` differ per
    source, and a flattened copy would quietly report one source's geometry as
    the whole run's.
    """
    merged = ValidationReport(config={})
    groups: list[dict[str, Any]] = []
    for entry in passes:
        if not entry.records or entry.datasets is None:
            continue
        part = validate_formulas(
            entry.records,
            entry.datasets,
            n_trials=entry.outcome.n_trials,
            fold_config=fold_config or entry.fold_config,
        )
        merged.formulas.extend(part.formulas)
        groups.append(
            {
                "source": entry.outcome.label,
                "n_trials": entry.outcome.n_trials,
                "config": part.config,
            }
        )
    merged.config["n_trials_by_source"] = {g["source"]: g["n_trials"] for g in groups}
    merged.config["groups"] = groups
    return merged


def run_supply_line(
    sources: Sequence[SourceSpec | str | Path],
    *,
    top_n: int = DEFAULT_TOP_N,
    db_path: Path = DEFAULT_DB_PATH,
    report_path: Path | None = DEFAULT_REPORT_PATH,
    fold_config: FoldConfig | None = None,
) -> SupplyLineResult:
    """Run every source through the full funnel and write the T4 report.

    Args:
        sources: artifacts to recover candidates from.
        top_n: candidates carried per source.
        db_path: klines database the panels are loaded from.
        report_path: where the report lands; ``None`` skips the write.
        fold_config: T4 walk-forward geometry (protocol default when omitted).
    """
    specs = [s if isinstance(s, SourceSpec) else SourceSpec(Path(s)) for s in sources]
    passes = [
        _pass_source(
            spec, top_n=top_n, db_path=Path(db_path), fold_override=fold_config
        )
        for spec in specs
    ]

    report = _validate_groups(passes, fold_config)
    report.config["supply_line"] = {
        "top_n": top_n,
        "sources": [
            {
                "label": entry.outcome.label,
                "path": str(entry.outcome.path),
                "recovered": entry.outcome.recovered,
                "gated_in": entry.outcome.gated_in,
                "n_trials": entry.outcome.n_trials,
                "interval": entry.outcome.interval,
                "rejected": [f.gate for f in entry.outcome.rejected],
                "deferred": [f.gate for f in entry.outcome.deferred],
            }
            for entry in passes
        ],
    }

    written: Path | None = None
    if report_path is not None:
        written = write_report(report, Path(report_path))

    return SupplyLineResult(
        report=report,
        sources=[entry.outcome for entry in passes],
        report_path=written,
    )


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------
def _fold_override_of(args: Any) -> FoldConfig | None:
    """CLI flags an operator actually passed, or ``None`` to use the artifact.

    argparse cannot tell a passed flag from its default, so the defaults are
    encoded here: any deviation means the operator overrode the geometry.
    """
    if (
        args.min_train == _DEFAULT_MIN_TRAIN
        and args.fold_size == _DEFAULT_FOLD_SIZE
        and args.n_folds == _DEFAULT_N_FOLDS
    ):
        return None
    return FoldConfig(
        min_train=max(1, args.min_train),
        fold_size=max(1, args.fold_size),
        n_folds=max(1, args.n_folds),
    )


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Supply line: recover -> G1-G6 shape gates -> T4 validate -> report."
    )
    parser.add_argument(
        "sources",
        nargs="*",
        default=[str(DEFAULT_RESULTS_DIR / "top_formulas.json")],
        help="top_formulas.json artifacts (default: the alpha-mining results dir).",
    )
    parser.add_argument(
        "--top-n", type=int, default=DEFAULT_TOP_N, help=f"Default: {DEFAULT_TOP_N}."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="klines.db path.")
    parser.add_argument(
        "--out", default=str(DEFAULT_REPORT_PATH), help="Report output path."
    )
    parser.add_argument(
        "--min-train", type=int, default=_DEFAULT_MIN_TRAIN, help="T4 min train bars."
    )
    parser.add_argument(
        "--fold-size",
        type=int,
        default=_DEFAULT_FOLD_SIZE,
        help="T4 OOS fold size in bars.",
    )
    parser.add_argument(
        "--n-folds", type=int, default=_DEFAULT_N_FOLDS, help="T4 requested fold count."
    )
    args = parser.parse_args(argv)

    result = run_supply_line(
        [Path(p) for p in args.sources],
        top_n=args.top_n,
        db_path=Path(args.db),
        report_path=Path(args.out),
        # The CLI defaults are the protocol defaults; passing them explicitly
        # would mask each artifact's recorded geometry. Only an explicit flag
        # overrides the artifact.
        fold_config=_fold_override_of(args),
    )

    for source in result.sources:
        print(
            f"[supply] {source.label}: recovered={source.recovered} "
            f"gated_in={source.gated_in} n_trials={source.n_trials} "
            f"rejected={len(source.rejected)} deferred={len(source.deferred)}"
        )
        for finding in source.rejected:
            print(f"[supply]   REJECT {finding.reason}")
        for finding in source.deferred:
            print(f"[supply]   DEFER  {finding.reason}")

    counts: dict[str, int] = {}
    for formula in result.report.formulas:
        counts[formula.verdict] = counts.get(formula.verdict, 0) + 1
    print(f"[supply] validated={len(result.report.formulas)} verdicts={counts}")
    if result.report_path is not None:
        print(f"[supply] wrote {result.report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

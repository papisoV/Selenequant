"""US-05/T5: VALID→registration pipeline, FitnessProtocol, RL warm-start bridge.

Tests the closing-the-loop deliverables of the deterministic genetic formula
search MVP:

- :class:`~selene_space_factor_research.mining.pipeline.RegistrationPipeline`
  produces VALID registration candidates with **zero** FactorLifecycle side
  effects (candidate-only by default — a VALID verdict must NOT auto-register);
- :meth:`~selene_space_factor_research.mining.pipeline.RegistrationCandidate.confirm`
  registers into a fresh :class:`FactorLifecycle` and returns a
  :class:`CycleReport` (idempotent);
- the synthetic AR(1) momentum demo: ``MOM = [RET, TS_RANK]`` is VERDICTED
  VALID by :func:`validate_formulas`, flows through the pipeline, and confirms
  into a CycleReport;
- :class:`~selene_space_factor_research.mining.fitness.FitnessProtocol` is a
  callable structural contract that the RL engine's fitness implements;
- :func:`~selene_space_factor_research.mining.fitness.top_formulas_to_warm_start`
  / :func:`~selene_space_factor_research.mining.fitness.warm_start_engine`
  serialize the deterministic top-N formulas into ``RealFitnessAlphaEngine``'s
  ``warm_start_formulas`` input;
- :func:`~selene_space_factor_research.mining.pipeline.load_pipeline_klines`
  calibrates ``max_bars = 2 × target_bars`` so the ~50%-duplicate klines.db
  does not silently halve the evidence (the n_dates pit).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from selene_space_alpha.factor_lifecycle import CycleReport, FactorState
from selene_space_alpha.vocab import FORMULA_VOCAB
from selene_space_factor_research.mining.fitness import (
    FitnessProtocol,
    top_formulas_to_warm_start,
    warm_start_engine,
)
from selene_space_factor_research.mining.pipeline import (
    RegistrationCandidate,
    RegistrationPipeline,
    load_pipeline_klines,
)
from selene_space_factor_research.mining.runner import load_klines
from selene_space_factor_research.mining.validation_protocol import (
    FoldConfig,
    validate_formulas,
)


#: The momentum-following formula — a strong, known-valid baseline on AR(1) data.
def _tok(name: str) -> int:
    return list(FORMULA_VOCAB.token_names).index(name)


RET = _tok("RET")
TS_RANK = _tok("TS_RANK")
GOOD = [RET, TS_RANK]


def _syn(
    n_dates: int = 120,
    n_symbols: int = 5,
    seed: int = 42,
    ar: float = 0.6,
    sigma: float = 0.02,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    """Synthetic OHLCV panels: AR(1) per-symbol returns (momentum persistence).

    Matches ``fitness.score`` / ``build_factor_panel``'s contract (DatetimeIndex
    + open/high/low/close/volume; forward-return Series with a NaN last bar).
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start="2024-01-01", periods=n_dates)
    noise = rng.normal(0.0, sigma, (n_dates, n_symbols))
    rets = np.zeros_like(noise)
    rets[0] = noise[0]
    for t in range(1, n_dates):
        rets[t] = ar * rets[t - 1] + noise[t]
    close = 100.0 * np.cumprod(1.0 + rets, axis=0)
    open_ = close / (1.0 + rets)
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    volume = 1000.0 * (1.0 + 0.5 * rng.random((n_dates, n_symbols)))

    data_by_symbol: dict[str, pd.DataFrame] = {}
    ret_by_symbol: dict[str, pd.Series] = {}
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
    return data_by_symbol, ret_by_symbol


def _valid_payload(*records: dict) -> dict:
    """A validation_report.json-shaped payload holding the given formula dicts."""
    return {
        "config": {"n_trials": 5, "fold_config": {"min_train": 70}},
        "formulas": list(records),
    }


def _formula_record(tokens: list[int] | None = None, verdict: str = "VALID") -> dict:
    tokens = tokens or GOOD
    return {
        "readable": "RET TS_RANK",
        "tokens": list(tokens),
        "canonical_hash": "deadbeef",
        "verdict": verdict,
        "decision": "accept",
        "reason": "OOS Rank-IC, subinterval agreement, multi-symbol agreement and DSR all pass",
    }


# --------------------------------------------------------------------------
# Candidate-only discipline (a): no registration without confirm.
# --------------------------------------------------------------------------
def test_build_candidates_has_no_lifecycle_side_effect() -> None:
    data, ret = _syn(n_dates=120, n_symbols=5)
    payload = _valid_payload(_formula_record())
    pipeline = RegistrationPipeline()

    # Patch FactorLifecycle so any construction during candidate building is
    # visible — the lazy import inside confirm() would resolve to this mock.
    with mock.patch(
        "selene_space_alpha.factor_lifecycle.FactorLifecycle"
    ) as lifecycle_cls:
        candidates = pipeline.build_candidates(payload, data, ret)

        assert len(candidates) == 1
        cand = candidates[0]
        assert isinstance(cand, RegistrationCandidate)
        assert cand.verdict == "VALID"
        assert cand.tokens == GOOD
        assert cand.confirmed is False
        assert cand.cycle_report is None
        # A VALID verdict must NOT auto-register: no FactorLifecycle was built.
        lifecycle_cls.assert_not_called()


def test_build_candidates_skips_non_valid_formulas() -> None:
    data, ret = _syn(n_dates=120, n_symbols=5)
    payload = _valid_payload(
        _formula_record(verdict="VALID"),
        _formula_record(verdict="SUSPICIOUS"),
        _formula_record(verdict="INVALID"),
    )
    pipeline = RegistrationPipeline()
    candidates = pipeline.build_candidates(payload, data, ret)
    assert len(candidates) == 1
    assert candidates[0].verdict == "VALID"


def test_build_candidates_accepts_validation_report_object() -> None:
    data, ret = _syn(n_dates=120, n_symbols=5)
    report = validate_formulas(
        [GOOD],
        {"syn": (data, ret)},
        n_trials=5,
        fold_config=FoldConfig(min_train=30, fold_size=20, n_folds=2),
    )
    pipeline = RegistrationPipeline()
    # A small-fold report on this short panel may not be VALID; build with
    # only_valid=False and check the report-object path still yields candidates.
    candidates = pipeline.build_candidates(report, data, ret, only_valid=False)
    assert len(candidates) == 1
    assert candidates[0].tokens == GOOD


# --------------------------------------------------------------------------
# confirm() registration (b): cycle() -> CycleReport.
# --------------------------------------------------------------------------
def test_confirm_registers_and_returns_cycle_report() -> None:
    data, ret = _syn(n_dates=120, n_symbols=5)
    payload = _valid_payload(_formula_record())
    pipeline = RegistrationPipeline(
        lifecycle_kwargs={"rolling_window": 10, "max_lag": 10}
    )

    candidates = pipeline.build_candidates(payload, data, ret)
    cand = candidates[0]
    assert cand.confirmed is False

    report = cand.confirm()
    assert isinstance(report, CycleReport)
    assert cand.confirmed is True
    assert cand.cycle_report is report
    # A fresh lifecycle starts ACTIVE; the first cycle advances the machine.
    assert report.state_before == FactorState.ACTIVE
    assert report.state_after in {FactorState.ACTIVE, FactorState.WATCH}
    assert report.cycle == 1
    assert len(report.trace_hash) == 64


def test_confirm_is_idempotent() -> None:
    data, ret = _syn(n_dates=120, n_symbols=5)
    payload = _valid_payload(_formula_record())
    pipeline = RegistrationPipeline(
        lifecycle_kwargs={"rolling_window": 10, "max_lag": 10}
    )

    cand = pipeline.build_candidates(payload, data, ret)[0]
    first = cand.confirm()
    second = cand.confirm()
    assert second is first
    assert cand.confirmed is True
    assert cand._lifecycle.n_cycles == 1  # not advanced twice


def test_confirm_uses_fresh_lifecycle_per_candidate() -> None:
    data, ret = _syn(n_dates=120, n_symbols=5)
    payload = _valid_payload(_formula_record(), _formula_record())
    pipeline = RegistrationPipeline(
        lifecycle_kwargs={"rolling_window": 10, "max_lag": 10}
    )

    candidates = pipeline.build_candidates(payload, data, ret)
    r1 = candidates[0].confirm()
    r2 = candidates[1].confirm()
    # Two separate factors -> two independent lifecycles, each at cycle 1.
    assert r1 is not r2
    assert r1.state_before == FactorState.ACTIVE
    assert r2.state_before == FactorState.ACTIVE


# --------------------------------------------------------------------------
# Synthetic-VALID end-to-end demo (4): MOM -> VALID -> candidate -> confirm.
# --------------------------------------------------------------------------
def test_synthetic_valid_end_to_end_demo() -> None:
    # Clean AR(1) momentum (ar=0.6, seed=32: DSR passes and no solo entry lands
    # in the SUSPICIOUS propagation band) with a long-enough OOS pool: the
    # hand-picked MOM formula genuinely passes the full §10 decision table (DSR
    # gate included) — the real klines.db smoke honestly returned NO VALID
    # formula, so this synthetic demo is the honest way to exercise the
    # registration path.
    data, ret = _syn(n_dates=340, n_symbols=5, ar=0.6, seed=32)
    report = validate_formulas(
        [GOOD],
        {"syn": (data, ret)},
        n_trials=5,
        fold_config=FoldConfig(min_train=70, fold_size=50, n_folds=5),
    )
    validation = report.formulas[0]
    assert validation.verdict == "VALID", validation.reason
    oos_ic = validation.aggregate.oos_rank_ic
    assert oos_ic is not None and oos_ic > 0.02
    assert validation.aggregate.dsr is not None and validation.aggregate.dsr > 0.95

    pipeline = RegistrationPipeline(
        lifecycle_kwargs={"rolling_window": 10, "max_lag": 10}
    )
    candidates = pipeline.build_candidates(report, data, ret)
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.verdict == "VALID"
    assert cand.confirmed is False
    assert cand.canonical_hash == validation.canonical_hash
    assert cand.backtest  # WeightDriftBacktest evidence recorded
    assert "mean_ic" in cand.ic_panel  # ICPanel evidence recorded

    cycle_report = cand.confirm()
    assert isinstance(cycle_report, CycleReport)
    assert cycle_report.state_before == FactorState.ACTIVE
    assert cycle_report.mean_ic > 0.0


# --------------------------------------------------------------------------
# FitnessProtocol contract (c).
# --------------------------------------------------------------------------
def test_fitness_protocol_is_runtime_checkable_contract() -> None:
    class FakeRLFitness:
        def evaluate(self, formula_tokens: list[int], vocab: object) -> float:
            return 1.5

    class NoEvaluate:
        pass

    # @runtime_checkable only checks member presence (signatures are static);
    # a class exposing `evaluate` conforms, one without it does not.
    assert isinstance(FakeRLFitness(), FitnessProtocol)
    assert isinstance(NoEvaluate(), FitnessProtocol) is False
    assert isinstance(42, FitnessProtocol) is False
    # The protocol names a callable boundary the RL engine invokes.
    assert FakeRLFitness().evaluate([1, 2], object()) == 1.5


# --------------------------------------------------------------------------
# RL warm-start bridge (d): deterministic top-N -> warm_start_formulas.
# --------------------------------------------------------------------------
def _write_top_formulas(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "top_formulas.json"
    path.write_text(json.dumps({"top_formulas": records}), encoding="utf-8")
    return path


def _record(
    rank: int, tokens: list[int], valid: bool = True, score: float = 1.0
) -> dict:
    return {
        "rank": rank,
        "tokens": tokens,
        "valid": valid,
        "score": score,
        "readable": f"f{rank}",
    }


def test_top_formulas_to_warm_start_shape_and_filtering(tmp_path) -> None:
    path = _write_top_formulas(
        tmp_path,
        [
            _record(1, [RET, TS_RANK], valid=True, score=3.5),
            _record(2, [RET], valid=True, score=2.0),
            _record(3, [1, 2, 3], valid=False, score=float("-inf")),  # sentinel
            _record(4, [RET, TS_RANK, 5], valid=True, score=1.2),
            _record(5, [0], valid=True, score=float("-inf")),  # sentinel score
        ],
    )
    warm = top_formulas_to_warm_start(path, n=2)
    # Only the top-2 *scored-valid* records, in rank order.
    assert warm == [[RET, TS_RANK], [RET]]
    # n=0 -> empty (nothing to warm-start from).
    assert top_formulas_to_warm_start(path, n=0) == []


def test_top_formulas_to_warm_start_missing_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        top_formulas_to_warm_start(tmp_path / "nope.json")


def test_warm_start_engine_passes_deterministic_top_n(tmp_path) -> None:
    path = _write_top_formulas(
        tmp_path,
        [
            _record(1, [RET, TS_RANK], valid=True, score=3.5),
            _record(2, [RET], valid=True, score=2.0),
            _record(3, [1, 2], valid=False, score=float("-inf")),
        ],
    )
    engine = mock.MagicMock()
    result = warm_start_engine(
        engine,  # type: ignore[arg-type]  # MagicMock stands in for engine_cls
        vocab="VOCAB",
        fitness="FITNESS",  # type: ignore[arg-type]  # protocol is structural; mock records the kwarg
        top_formulas_path=path,
        n=2,
        output_dir="out",
    )
    assert result is engine.return_value
    engine.assert_called_once_with(
        vocab="VOCAB",
        fitness="FITNESS",
        warm_start_formulas=[[RET, TS_RANK], [RET]],
        output_dir="out",
    )


# --------------------------------------------------------------------------
# Ledger known-formula lookup (closed-loop cut): duplicate_of marking.
# --------------------------------------------------------------------------
def test_build_candidates_known_hit_sets_duplicate_of() -> None:
    data, ret = _syn(n_dates=120, n_symbols=5)
    payload = _valid_payload(_formula_record())  # canonical_hash="deadbeef"
    pipeline = RegistrationPipeline()

    known = {"deadbeef": {"episode_id": "ep-abc123", "canonical_hash": "deadbeef"}}
    candidates = pipeline.build_candidates(payload, data, ret, known_formulas=known)
    assert len(candidates) == 1
    assert candidates[0].duplicate_of == "ep-abc123"


def test_build_candidates_without_known_formulas_leaves_duplicate_of_none() -> None:
    data, ret = _syn(n_dates=120, n_symbols=5)
    payload = _valid_payload(_formula_record())
    pipeline = RegistrationPipeline()

    # Default (no known_formulas): exact same candidates as before the
    # closed-loop cut — zero disturbance on the default path.
    candidates = pipeline.build_candidates(payload, data, ret)
    assert len(candidates) == 1
    assert candidates[0].duplicate_of is None


def test_build_candidates_hash_none_skips_duplicate_lookup() -> None:
    data, ret = _syn(n_dates=120, n_symbols=5)
    record = _formula_record()
    record["canonical_hash"] = None  # parse failure upstream
    payload = _valid_payload(record)
    pipeline = RegistrationPipeline()

    # A None hash must not crash the lookup and must not match anything.
    known = {"deadbeef": {"episode_id": "ep-abc123"}}
    candidates = pipeline.build_candidates(payload, data, ret, known_formulas=known)
    assert len(candidates) == 1
    assert candidates[0].canonical_hash is None
    assert candidates[0].duplicate_of is None


# --------------------------------------------------------------------------
# n_dates pit fix (5): load_pipeline_klines calibrates max_bars = 2× target.
# --------------------------------------------------------------------------
def _write_duplicate_klines_db(path, *, n_rows: int, dup_ratio: float) -> None:
    """SQLite klines.db with ~``dup_ratio`` duplicate timestamps per symbol."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE klines ("
        " symbol TEXT, exchange TEXT, market_type TEXT, interval TEXT,"
        " timestamp INTEGER, open REAL, high REAL, low REAL, close REAL,"
        " volume REAL)"
    )
    n_unique = max(1, int(round(n_rows / (1.0 + dup_ratio))))
    n_symbols = 2
    rows = []
    for s in range(n_symbols):
        sym = f"SYM{s}"
        for u in range(n_unique):
            ts = 1_700_000_000_000 + u * 3_600_000
            for _dup in range(int(round(dup_ratio)) + 1):
                rows.append(
                    (
                        sym,
                        "binance",
                        "futures",
                        "4h",
                        ts,
                        100.0,
                        101.0,
                        99.0,
                        100.5,
                        1000.0,
                    )
                )
    conn.executemany("INSERT INTO klines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_load_pipeline_klines_does_not_halve_evidence(tmp_path) -> None:
    db = tmp_path / "klines.db"
    # 150 unique timestamps per symbol, each duplicated once (≈50% dup).
    _write_duplicate_klines_db(db, n_rows=300, dup_ratio=1.0)

    data = load_pipeline_klines(db, ["SYM0", "SYM1"], "4h", target_bars=150)
    for sym in ("SYM0", "SYM1"):
        assert sym in data
        # 2× target max_bars (300 rows) dedupes back to ~150 unique bars, NOT 75.
        assert len(data[sym]) >= 120
        assert len(data[sym]) <= 160
        # Deduped and time-ordered.
        assert data[sym].index.is_monotonic_increasing


def test_load_pipeline_klines_respects_target_bars(tmp_path) -> None:
    db = tmp_path / "klines.db"
    _write_duplicate_klines_db(db, n_rows=400, dup_ratio=1.0)
    data = load_pipeline_klines(db, ["SYM0"], "4h", target_bars=100)
    assert len(data["SYM0"]) <= 110


# --------------------------------------------------------------------------
# market_type partition filtering (defect A: spot/futures mixed reads).
# --------------------------------------------------------------------------
def test_load_pipeline_klines_filters_market_type(tmp_path) -> None:
    """Spot and futures rows share (symbol, interval); only futures are loaded."""
    db = tmp_path / "klines.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE klines ("
        " symbol TEXT, exchange TEXT, market_type TEXT, interval TEXT,"
        " timestamp INTEGER, open REAL, high REAL, low REAL, close REAL,"
        " volume REAL)"
    )
    rows = []
    for mt in ("futures", "spot"):
        for u in range(10):
            ts = 1_700_000_000_000 + u * 3_600_000
            rows.append(
                ("SYM0", "binance", mt, "4h", ts, 100.0, 101.0, 99.0, 100.5, 1000.0)
            )
    conn.executemany("INSERT INTO klines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()

    data = load_pipeline_klines(db, ["SYM0"], "4h", target_bars=50)
    assert len(data["SYM0"]) == 10
    assert data["SYM0"].index.is_monotonic_increasing


def test_load_klines_market_type_kwarg(tmp_path) -> None:
    """Explicit market_type selects the requested partition."""
    db = tmp_path / "klines.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE klines ("
        " symbol TEXT, exchange TEXT, market_type TEXT, interval TEXT,"
        " timestamp INTEGER, open REAL, high REAL, low REAL, close REAL,"
        " volume REAL)"
    )
    for mt in ("futures", "spot"):
        ts = 1_700_000_000_000
        conn.execute(
            "INSERT INTO klines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("SYM0", "binance", mt, "4h", ts, 100.0, 101.0, 99.0, 100.5, 1000.0),
        )
    conn.commit()
    conn.close()

    spot = load_klines(db, ["SYM0"], "4h", max_bars=10, market_type="spot")
    assert len(spot["SYM0"]) == 1


# --------------------------------------------------------------------------
# main() ledger wiring (closed-loop cut): --confirm writes episodes; failure
# leaves a stderr backfill key (H5). Uses an in-memory-ish temp ledger db.
# --------------------------------------------------------------------------
def _main_fixtures(tmp_path, monkeypatch):
    """Write a validation report + tiny klines db; silence klines loading.

    Returns (report_path, klines_db_path, ledger_db_path). The report carries
    one VALID formula whose canonical_hash is registered in the fixture vocab
    space; load_pipeline_klines is patched out because the synthetic panels
    come from _syn() (AR(1) momentum), not the sqlite fixture.
    """
    import importlib

    pipeline_mod = importlib.import_module(
        "selene_space_factor_research.mining.pipeline"
    )

    report = {
        "config": {
            "n_trials": 5,
            "fold_config": {"min_train": 70},
            "datasets": {"4h": {"symbols": ["SYM0", "SYM1"]}},
        },
        "formulas": [_formula_record()],
    }
    report_path = tmp_path / "validation_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    db_path = tmp_path / "klines.db"
    _write_duplicate_klines_db(db_path, n_rows=300, dup_ratio=1.0)

    ledger_db = tmp_path / "ledger.db"

    data, ret = _syn(n_dates=120, n_symbols=5)
    monkeypatch.setattr(pipeline_mod, "load_pipeline_klines", lambda *a, **k: data)
    monkeypatch.setattr(pipeline_mod, "build_returns", lambda data_by_symbol: ret)
    return report_path, db_path, ledger_db, pipeline_mod


def test_resolve_ledger_db_defaults_on_with_off_optout() -> None:
    import importlib

    pipeline_mod = importlib.import_module(
        "selene_space_factor_research.mining.pipeline"
    )
    from selene_space_factor_research.mining import ledger_bridge

    resolve = pipeline_mod._resolve_ledger_db
    # Default ON (plan v3): a bare --confirm run writes to the user ledger.
    assert resolve(None) == ledger_bridge.DEFAULT_LEDGER_DB
    # Explicit opt-out is the only way to disable.
    assert resolve("off") is None
    assert resolve("OFF") is None
    # Explicit path is honored as-is.
    assert resolve(str(Path("<local-path-redacted>"))) == Path("<local-path-redacted>")


def test_main_confirm_registers_ledger_episode(tmp_path, monkeypatch, capsys) -> None:
    report_path, db_path, ledger_db, pipeline_mod = _main_fixtures(
        tmp_path, monkeypatch
    )
    from selene_space_factor_research.mining import ledger_bridge

    registered: list[dict] = []

    def fake_register(db, **kwargs):
        registered.append({"db": db, **kwargs})
        return {
            "new_episode_id": "ep-fake1",
            "latest": {"canonical_hash": kwargs["canonical_hash"]},
        }

    monkeypatch.setattr(ledger_bridge, "register_formula_episode", fake_register)

    rc = pipeline_mod.main(
        [
            "--validation-report",
            str(report_path),
            "--db",
            str(db_path),
            "--confirm",
            "--ledger-db",
            str(ledger_db),
            # --out defaults to the real internal-archive/ dir; pin it to the
            # test tmp dir so the run can't clobber the checked-in candidates.
            "--out",
            str(tmp_path / "registration_candidates.json"),
        ]
    )
    assert rc == 0
    assert len(registered) == 1
    call = registered[0]
    assert call["db"] == ledger_db
    assert call["canonical_hash"] == "deadbeef"
    assert call["source"] == "registration_pipeline"
    assert call["verdict"] == "VALID"
    out = capsys.readouterr()
    assert "ledger episode ep-fake1" in out.out


def test_main_confirm_register_failure_writes_stderr_key(
    tmp_path, monkeypatch, capsys
) -> None:
    report_path, db_path, ledger_db, pipeline_mod = _main_fixtures(
        tmp_path, monkeypatch
    )
    from selene_space_factor_research.mining import ledger_bridge

    def exploding_register(db, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(ledger_bridge, "register_formula_episode", exploding_register)

    rc = pipeline_mod.main(
        [
            "--validation-report",
            str(report_path),
            "--db",
            str(db_path),
            "--confirm",
            "--ledger-db",
            str(ledger_db),
            # Pin the candidate output off the real internal-archive/ dir.
            "--out",
            str(tmp_path / "registration_candidates.json"),
        ]
    )
    # H5: confirm itself succeeded; the ledger failure must not crash the run.
    assert rc == 0
    out = capsys.readouterr()
    assert "episode not recorded" in out.err
    assert "deadbeef" in out.err  # manual backfill key
    assert "disk on fire" in out.err

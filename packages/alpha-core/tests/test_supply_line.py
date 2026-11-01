"""US-002: the supply line — recover -> G1-G6 shape gates -> T4 validate -> report.

Gate semantics are pinned against a stubbed panel loader, so these cases test
the funnel rather than the contents of ``klines.db``. The end-to-end case runs
the real T4 protocol on a small synthetic panel and reads the report back off
disk — that one is where gate and protocol have to actually agree.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from selene_space_alpha.factors import btc_bg_factors
from selene_space_alpha.vocab import FORMULA_VOCAB, FormulaVocab
from selene_space_factor_research.mining import supply_line
from selene_space_factor_research.mining.validation_protocol import FoldConfig

#: Small geometry — the walk-forward shape is the protocol's concern, and this
#: keeps the end-to-end cases to a handful of folds.
_FAST_FOLDS = FoldConfig(min_train=60, fold_size=20, n_folds=2)


@pytest.fixture(autouse=True)
def _stub_btc_background(monkeypatch):
    """Keep the BTC background off disk.

    ``vm_pandas._compute_features`` rebuilds the background once per BTC_BG_*
    feature, and every rebuild re-reads the 2.3 GB ``data/klines.db`` — whose
    ``klines`` table has no ``(symbol, interval, exchange)`` index, so each of
    ``load_close_matrix``'s 19 queries is a near-full scan. Measured at ~50 s
    per rebuild and 6-12 rebuilds per ``validate_formulas`` call, i.e. ~5
    minutes for a single call. The background is an external data input, like
    the price panel, not part of the gate/protocol contract under test.
    """
    axis = pd.date_range("2020-01-01", periods=2000, freq="D")
    values = np.sin(np.arange(len(axis)) / 30.0)
    monkeypatch.setattr(
        btc_bg_factors, "build_background", lambda **_kw: pd.Series(values, index=axis)
    )


def _tok(name: str) -> int:
    return list(FORMULA_VOCAB.token_names).index(name)


RET = _tok("RET")
TS_RANK = _tok("TS_RANK")
NEG = _tok("NEG")

#: Arity-clean, in-vocab momentum formula.
GOOD = [RET, TS_RANK]


def _panel(*, n_dates: int = 400, n_symbols: int = 2, seed: int = 7) -> dict:
    """AR(1) OHLCV panels in ``build_datasets`` shape (``{interval: (data, ret)}``).

    400 business days spans ~18 months, comfortably past G6's one-year floor;
    ``n_dates=100`` is the short-panel case.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start="2022-01-03", periods=n_dates)
    noise = rng.normal(0.0, 0.02, (n_dates, n_symbols))
    rets = np.zeros_like(noise)
    rets[0] = noise[0]
    for t in range(1, n_dates):
        rets[t] = 0.5 * rets[t - 1] + noise[t]
    close = 100.0 * np.cumprod(1.0 + rets, axis=0)
    open_ = close / (1.0 + rets)

    data_by_symbol: dict[str, pd.DataFrame] = {}
    ret_by_symbol: dict[str, pd.Series] = {}
    for s in range(n_symbols):
        sym = f"S{s}"
        data_by_symbol[sym] = pd.DataFrame(
            {
                "open": open_[:, s],
                "high": np.maximum(open_[:, s], close[:, s]) * 1.004,
                "low": np.minimum(open_[:, s], close[:, s]) * 0.996,
                "close": close[:, s],
                "volume": 1000.0 * (1.0 + 0.5 * rng.random(n_dates)),
            },
            index=dates,
        )
        closes = pd.Series(close[:, s], index=dates)
        ret_by_symbol[sym] = closes.shift(-1) / closes - 1.0
    return {"4h": (data_by_symbol, ret_by_symbol)}


def _payload(records: list, **overrides) -> dict:
    payload = {
        "symbols": ["S0", "S1"],
        "interval": "4h",
        "bars_per_symbol": 400,
        "evaluations_used": 64,
        "vocab_digest": FORMULA_VOCAB.vocab_digest,
        "top_formulas": records,
    }
    payload.update(overrides)
    return payload


def _record(tokens: list[int] | None = None, **overrides) -> dict:
    record = {
        "rank": 1,
        "readable": "TS_RANK(RET)",
        "tokens": GOOD if tokens is None else tokens,
    }
    record.update(overrides)
    return record


def _write(tmp_path: Path, payload: dict, name: str = "top_formulas_4h.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _stub_panel(
    monkeypatch, datasets: dict | None = None, *, raises: Exception | None = None
) -> None:
    """Replace G4's panel load — the gate is under test, not klines.db."""
    if raises is not None:

        def _boom(*_args, **_kwargs):
            raise raises

        monkeypatch.setattr(supply_line, "build_datasets", _boom)
        return
    panel = _panel() if datasets is None else datasets
    monkeypatch.setattr(supply_line, "build_datasets", lambda *_a, **_k: panel)


def _verdict_of(formula) -> dict:
    """The decision-relevant slice of a formula, for cross-run comparison."""
    return {
        "readable": formula.readable,
        "verdict": formula.verdict,
        "decision": formula.decision,
        "mean_ic": formula.aggregate.oos_rank_ic,
        "dsr": formula.aggregate.dsr,
        "n_oos_obs": formula.aggregate.n_oos_obs,
    }


# ---------------------------------------------------------------------------
# G1 — vocab digest recompute.
# ---------------------------------------------------------------------------
def test_g1_accepts_the_current_vocab_fingerprint() -> None:
    assert supply_line.gate_vocab_digest(_payload([])) is None


def test_g1_rejects_an_artifact_from_a_forked_vocab(tmp_path, monkeypatch) -> None:
    """Renaming one operator shifts every id from there on, so the artifact's
    tokens would decode to different features than it was searched under.

    Note the digest covers ``feature_names[:77] + operator_names`` only — the
    AW_* tail is deliberately outside it, so *appending* a feature would leave
    the fingerprint unchanged and prove nothing here.
    """
    forked = FormulaVocab(
        feature_names=FORMULA_VOCAB.feature_names,
        operator_names=("FORKED_SENTINEL",) + FORMULA_VOCAB.operator_names[1:],
    )
    assert forked.vocab_digest != FORMULA_VOCAB.vocab_digest

    _stub_panel(monkeypatch)
    path = _write(tmp_path, _payload([_record()], vocab_digest=forked.vocab_digest))
    result = supply_line.run_supply_line([path], report_path=None)

    source = result.sources[0]
    assert source.recovered == 1
    assert source.gated_in == 0
    assert [f.gate for f in source.rejected] == ["G1"]
    # Both fingerprints are quoted so the disagreement is diagnosable.
    assert forked.vocab_digest in source.rejected[0].reason
    assert FORMULA_VOCAB.vocab_digest in source.rejected[0].reason


def test_g1_rejects_an_artifact_without_a_fingerprint(tmp_path, monkeypatch) -> None:
    _stub_panel(monkeypatch)
    payload = _payload([_record()])
    del payload["vocab_digest"]
    result = supply_line.run_supply_line([_write(tmp_path, payload)], report_path=None)
    assert [f.gate for f in result.sources[0].rejected] == ["G1"]


# ---------------------------------------------------------------------------
# G2 / G3 — per-candidate shape.
# ---------------------------------------------------------------------------
def test_g2_missing_tokens_rejects_only_that_candidate(tmp_path, monkeypatch) -> None:
    _stub_panel(monkeypatch)
    records = [{"rank": 1, "readable": "no tokens here"}, _record()]
    path = _write(tmp_path, _payload(records))
    result = supply_line.run_supply_line(
        [path], report_path=None, fold_config=_FAST_FOLDS
    )

    source = result.sources[0]
    assert source.recovered == 2
    assert source.gated_in == 1
    assert [f.gate for f in source.rejected] == ["G2"]
    assert "tokens" in source.rejected[0].reason
    # The survivor still reached the T4 pass — one bad record is not a batch kill.
    assert len(result.report.formulas) == 1


def test_g2_empty_or_non_object_records_are_rejected(tmp_path, monkeypatch) -> None:
    _stub_panel(monkeypatch)
    path = _write(tmp_path, _payload([{"tokens": []}, "not-a-record"]))
    result = supply_line.run_supply_line([path], report_path=None)

    source = result.sources[0]
    assert source.gated_in == 0
    assert [f.gate for f in source.rejected] == ["G2", "G2"]


def test_g3_rejects_a_token_outside_the_vocab(tmp_path, monkeypatch) -> None:
    _stub_panel(monkeypatch)
    path = _write(tmp_path, _payload([_record([RET, TS_RANK, FORMULA_VOCAB.size + 5])]))
    result = supply_line.run_supply_line([path], report_path=None)

    source = result.sources[0]
    assert source.gated_in == 0
    assert [f.gate for f in source.rejected] == ["G3"]
    assert "outside vocab range" in source.rejected[0].reason


def test_g3_rejects_an_rpn_underflow(tmp_path, monkeypatch) -> None:
    """A bare unary operator pops from an empty stack — structurally malformed
    even though every token is in range."""
    _stub_panel(monkeypatch)
    path = _write(tmp_path, _payload([_record([TS_RANK])]))
    result = supply_line.run_supply_line([path], report_path=None)

    source = result.sources[0]
    assert source.gated_in == 0
    assert source.rejected[0].gate == "G3"
    assert "RPN" in source.rejected[0].reason


def test_g3_accepts_a_well_formed_sequence() -> None:
    assert supply_line.gate_token_bounds(GOOD) is None


# ---------------------------------------------------------------------------
# G4 — the panel must exist.
# ---------------------------------------------------------------------------
def test_g4_rejects_a_panel_that_cannot_be_loaded(tmp_path, monkeypatch) -> None:
    _stub_panel(monkeypatch, raises=ValueError("no rows for S0"))
    path = _write(tmp_path, _payload([_record()]))
    result = supply_line.run_supply_line([path], report_path=None)

    source = result.sources[0]
    assert source.gated_in == 0
    assert [f.gate for f in source.rejected] == ["G4"]
    assert "no rows for S0" in source.rejected[0].reason


def test_g4_rejects_an_artifact_declaring_no_symbols(tmp_path, monkeypatch) -> None:
    _stub_panel(monkeypatch)
    path = _write(tmp_path, _payload([_record()], symbols=[]))
    result = supply_line.run_supply_line([path], report_path=None)
    assert [f.gate for f in result.sources[0].rejected] == ["G4"]


# ---------------------------------------------------------------------------
# G5 — the file is what its name says it is.
# ---------------------------------------------------------------------------
def test_g5_rejects_a_filename_that_contradicts_the_payload(
    tmp_path, monkeypatch
) -> None:
    _stub_panel(monkeypatch)
    path = _write(
        tmp_path, _payload([_record()], interval="1h"), name="top_formulas_4h.json"
    )
    result = supply_line.run_supply_line([path], report_path=None)

    source = result.sources[0]
    assert source.gated_in == 0
    assert [f.gate for f in source.rejected] == ["G5"]
    reason = source.rejected[0].reason
    assert "4h" in reason
    assert "1h" in reason


def test_g5_leaves_a_timeless_filename_alone(tmp_path, monkeypatch) -> None:
    """``top_formulas.json`` encodes no interval, so there is nothing to
    contradict — the default artifact must not be rejected."""
    _stub_panel(monkeypatch)
    path = _write(tmp_path, _payload([]), name="top_formulas.json")
    result = supply_line.run_supply_line([path], report_path=None)
    assert not result.sources[0].rejected


def test_interval_from_filename() -> None:
    assert supply_line.interval_from_filename(Path("top_formulas_4h.json")) == "4h"
    assert supply_line.interval_from_filename(Path("top_formulas_1d.json")) == "1d"
    assert supply_line.interval_from_filename(Path("top_formulas.json")) is None
    assert supply_line.interval_from_filename(Path("top_formulas_batch2.json")) is None


# ---------------------------------------------------------------------------
# G6 — defer, don't kill.
# ---------------------------------------------------------------------------
def test_g6_short_panel_defers_instead_of_rejecting(tmp_path, monkeypatch) -> None:
    _stub_panel(monkeypatch, _panel(n_dates=100))
    path = _write(tmp_path, _payload([_record()]))
    result = supply_line.run_supply_line(
        [path], report_path=None, fold_config=_FAST_FOLDS
    )

    source = result.sources[0]
    assert source.gated_in == 0
    assert not source.rejected
    assert [f.gate for f in source.deferred] == ["G6"]
    assert result.deferred == list(source.deferred)
    # Deferred means undecided, so nothing was judged.
    assert result.report.formulas == []


def test_min_panel_span_days_uses_the_shortest_market() -> None:
    """A panel is only decidable once *every* market in it has the history."""
    data, ret = _panel()["4h"]
    trimmed = {**data, "S1": data["S1"].iloc[-100:]}

    span = supply_line.min_panel_span_days({"4h": (trimmed, ret)})
    assert span is not None
    assert 100 < span < supply_line._MIN_SPAN_DAYS


# ---------------------------------------------------------------------------
# State machine.
# ---------------------------------------------------------------------------
def test_full_pass_validates_and_writes_the_report(tmp_path, monkeypatch) -> None:
    _stub_panel(monkeypatch)
    out = tmp_path / "validation_report.json"
    path = _write(tmp_path, _payload([_record()]))

    result = supply_line.run_supply_line(
        [path], report_path=out, fold_config=_FAST_FOLDS
    )

    source = result.sources[0]
    assert (source.recovered, source.gated_in, source.n_trials) == (1, 1, 64)
    assert result.report_path == out

    formula = result.report.formulas[0]
    assert formula.tokens == GOOD  # the gate handed the right candidate through
    assert formula.readable  # rendered by the T4 pass, not echoed from the record
    assert formula.decision in {"accept", "hold", "reject", "defer"}

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["config"]["n_trials_by_source"] == {"top_formulas_4h": 64}
    assert payload["config"]["groups"][0]["config"]["fold_config"]["n_folds"] == 2
    assert payload["summary"]
    assert payload["formulas"][0]["readable"] == formula.readable


def test_sources_are_validated_in_separate_calls_with_their_own_search_count(
    tmp_path, monkeypatch
) -> None:
    """``n_trials`` is a scalar DSR count, so two sources searched a different
    number of times cannot share one call."""
    _stub_panel(monkeypatch)
    narrow = _write(tmp_path, _payload([_record()], evaluations_used=16))
    wide = _write(
        tmp_path,
        _payload([_record([RET, NEG])], evaluations_used=512),
        name="top_formulas_batch2.json",
    )

    result = supply_line.run_supply_line(
        [narrow, wide], report_path=None, fold_config=_FAST_FOLDS
    )

    assert result.report.config["n_trials_by_source"] == {
        "top_formulas_4h": 16,
        "top_formulas_batch2": 512,
    }
    assert [g["n_trials"] for g in result.report.config["groups"]] == [16, 512]
    assert len(result.report.formulas) == 2


def test_each_batch_keeps_its_standalone_verdict_when_run_together(
    tmp_path, monkeypatch
) -> None:
    """Two batches with different search counts must reach the verdicts they
    reach alone.

    Both sources carry the *same* tokens over the same panel, so every quantity
    a verdict is built from is identical between them — the only thing that can
    separate a merged verdict from a standalone one is a search count applied to
    the wrong batch. That is precisely what pooling the sources would do.
    """
    _stub_panel(monkeypatch)
    narrow = _write(tmp_path, _payload([_record()], evaluations_used=16))
    wide = _write(
        tmp_path,
        _payload([_record()], evaluations_used=512),
        name="top_formulas_batch2.json",
    )

    alone: dict[int, dict] = {}
    for path, n_trials in ((narrow, 16), (wide, 512)):
        single = supply_line.run_supply_line(
            [path], report_path=None, fold_config=_FAST_FOLDS
        )
        assert len(single.report.formulas) == 1
        alone[n_trials] = _verdict_of(single.report.formulas[0])

    merged = supply_line.run_supply_line(
        [narrow, wide], report_path=None, fold_config=_FAST_FOLDS
    )

    groups = merged.report.config["groups"]
    assert [g["n_trials"] for g in groups] == [16, 512]
    assert len(merged.report.formulas) == 2
    for group, formula in zip(groups, merged.report.formulas):
        assert _verdict_of(formula) == alone[group["n_trials"]]
    # ...and the count each verdict was reached under is recoverable from the
    # report itself, not just from this test's memory of the run.
    assert merged.report.config["n_trials_by_source"] == {
        "top_formulas_4h": 16,
        "top_formulas_batch2": 512,
    }


def test_a_larger_search_count_deflates_the_same_formula_harder(
    tmp_path, monkeypatch
) -> None:
    """``n_trials`` moves the DSR benchmark and nothing else.

    Same tokens, same panel, same OOS evidence — only the search space widens,
    so the multiple-testing benchmark rises and the gate tightens. Without this
    the cross-run comparison above would be satisfied by a run where the count
    never reached the DSR at all, i.e. by an implementation that passed
    ``n_trials`` around but never used it.
    """
    _stub_panel(monkeypatch)
    narrow = _write(tmp_path, _payload([_record()], evaluations_used=16))
    wide = _write(
        tmp_path,
        _payload([_record()], evaluations_used=512),
        name="top_formulas_batch2.json",
    )

    result = supply_line.run_supply_line(
        [narrow, wide], report_path=None, fold_config=_FAST_FOLDS
    )

    by_trials = {
        group["n_trials"]: formula
        for group, formula in zip(
            result.report.config["groups"], result.report.formulas
        )
    }
    small, large = by_trials[16].aggregate, by_trials[512].aggregate
    assert small.dsr is not None and large.dsr is not None
    assert large.dsr < small.dsr
    # The OOS evidence is untouched: it is the correction that moved.
    assert large.oos_rank_ic == small.oos_rank_ic


def test_an_unreadable_artifact_is_a_finding_not_an_exception(tmp_path) -> None:
    result = supply_line.run_supply_line([tmp_path / "missing.json"], report_path=None)

    source = result.sources[0]
    assert source.recovered == 0
    assert source.gated_in == 0
    assert [f.gate for f in source.rejected] == ["recover"]
    assert "missing.json" in source.rejected[0].reason


def test_top_n_bounds_what_is_recovered(tmp_path, monkeypatch) -> None:
    _stub_panel(monkeypatch)
    records = [_record() for _ in range(5)]
    path = _write(tmp_path, _payload(records))

    result = supply_line.run_supply_line(
        [path], top_n=2, report_path=None, fold_config=_FAST_FOLDS
    )

    assert result.sources[0].recovered == 2
    assert result.sources[0].gated_in == 2
    assert len(result.report.formulas) == 2


def test_a_source_with_no_surviving_candidate_skips_validation(
    tmp_path, monkeypatch
) -> None:
    """Everything gated out means there is nothing to validate — the report is
    still written, with an empty formula list rather than a crash."""
    _stub_panel(monkeypatch)
    out = tmp_path / "validation_report.json"
    path = _write(tmp_path, _payload([_record([FORMULA_VOCAB.size + 1])]))

    result = supply_line.run_supply_line([path], report_path=out)

    assert result.report.formulas == []
    assert result.report_path == out
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["config"]["n_trials_by_source"] == {}
    assert payload["summary"] == {}

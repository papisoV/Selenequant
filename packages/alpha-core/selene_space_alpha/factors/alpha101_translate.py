"""Translate a curated subset of Alpha101/191 formulas into FormulaVocab tokens.

Not all 101/191 formulas can be expressed in our vocabulary — some require
operations beyond the current token set.  IND_NEUTRALIZE is now available
as a DataFrame-level operator (see ops_pandas.ind_neutralize), but it is
not included in the FormulaVocab token sequence because it operates on
the full cross-section rather than individual Series.  This module provides
~20 curated formulas that map cleanly and serve as warm-start seeds for the
formula-policy network.

Each translation is a list of token indices referencing the extended
FormulaVocab (features 0..N-1, operators N..M-1).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..vocab import FormulaVocab, FORMULA_VOCAB


@dataclass(frozen=True)
class TranslatedFormula:
    alpha_id: str
    tokens: tuple[int, ...]
    readable: str
    source: str  # "alpha101" or "alpha191"


def _tok(vocab: FormulaVocab, name: str) -> int:
    """Look up a token index by name in the vocab."""
    names = vocab.token_names
    idx = names.index(name) if name in names else -1
    if idx < 0:
        raise ValueError(f"Token '{name}' not found in vocab")
    return idx


def translate_alpha101_subset(vocab: FormulaVocab | None = None) -> list[TranslatedFormula]:
    """Translate curated Alpha101 formulas into FormulaVocab tokens.

    Returns a list of TranslatedFormula instances that can be used as
    warm-start seeds for REINFORCE training.
    """
    v = vocab or FORMULA_VOCAB
    formulas: list[TranslatedFormula] = []

    # --- Alpha001: max(ts_rank(rank(RET^2), 5)) - 0.5
    # Simplified: RANK(MUL(RET, RET)) - uses RANK to normalize squared returns
    formulas.append(TranslatedFormula(
        alpha_id="alpha_001",
        tokens=(
            _tok(v, "RANK"), _tok(v, "MUL"), _tok(v, "RET"), _tok(v, "RET"),
        ),
        readable="RANK(MUL(RET, RET))",
        source="alpha101",
    ))

    # --- Alpha004: -1 * ts_rank(rank(DEV), 9)
    # Simplified: NEG(TS_RANK(DEV))
    formulas.append(TranslatedFormula(
        alpha_id="alpha_004",
        tokens=(
            _tok(v, "NEG"), _tok(v, "TS_RANK"), _tok(v, "DEV"),
        ),
        readable="NEG(TS_RANK(DEV))",
        source="alpha101",
    ))

    # --- Alpha005: rank(RET - ts_mean(LOG_VOL, 10)) * -1 * abs(rank(RET - LOG_VOL))
    # Simplified: MUL(RANK(SUB(RET, TS_MEAN(LOG_VOL))), NEG(ABS(RANK(SUB(RET, LOG_VOL)))))
    formulas.append(TranslatedFormula(
        alpha_id="alpha_005",
        tokens=(
            _tok(v, "MUL"),
            _tok(v, "RANK"), _tok(v, "SUB"), _tok(v, "RET"), _tok(v, "TS_MEAN"), _tok(v, "LOG_VOL"),
            _tok(v, "NEG"), _tok(v, "ABS"), _tok(v, "RANK"), _tok(v, "SUB"), _tok(v, "RET"), _tok(v, "LOG_VOL"),
        ),
        readable="MUL(RANK(SUB(RET,TS_MEAN(LOG_VOL))), NEG(ABS(RANK(SUB(RET,LOG_VOL)))))",
        source="alpha101",
    ))

    # --- Alpha012: sign(DEV - ts_mean(DEV)) * (RET - ts_mean(RET))
    formulas.append(TranslatedFormula(
        alpha_id="alpha_012",
        tokens=(
            _tok(v, "MUL"),
            _tok(v, "SIGN"), _tok(v, "TS_DELTA"), _tok(v, "DEV"),
            _tok(v, "TS_DELTA"), _tok(v, "RET"),
        ),
        readable="MUL(SIGN(TS_DELTA(DEV)), TS_DELTA(RET))",
        source="alpha101",
    ))

    # --- Alpha014: -1 * rank(RET * DEV)
    formulas.append(TranslatedFormula(
        alpha_id="alpha_014",
        tokens=(
            _tok(v, "NEG"), _tok(v, "RANK"), _tok(v, "MUL"), _tok(v, "RET"), _tok(v, "DEV"),
        ),
        readable="NEG(RANK(MUL(RET, DEV)))",
        source="alpha101",
    ))

    # --- Alpha018: -1 * ts_rank(ts_std(RET, 5), 10)
    formulas.append(TranslatedFormula(
        alpha_id="alpha_018",
        tokens=(
            _tok(v, "NEG"), _tok(v, "TS_RANK"), _tok(v, "TS_STD"), _tok(v, "RET"),
        ),
        readable="NEG(TS_RANK(TS_STD(RET)))",
        source="alpha101",
    ))

    # --- Alpha020: (ts_max(RET) - RET) / ts_max(RET)
    formulas.append(TranslatedFormula(
        alpha_id="alpha_020",
        tokens=(
            _tok(v, "DIV"),
            _tok(v, "SUB"), _tok(v, "TS_MAX"), _tok(v, "RET"), _tok(v, "RET"),
            _tok(v, "TS_MAX"), _tok(v, "RET"),
        ),
        readable="DIV(SUB(TS_MAX(RET), RET), TS_MAX(RET))",
        source="alpha101",
    ))

    # --- Alpha023: sign(RET * ts_mean(RET, 5))
    formulas.append(TranslatedFormula(
        alpha_id="alpha_023",
        tokens=(
            _tok(v, "SIGN"), _tok(v, "MUL"), _tok(v, "RET"), _tok(v, "TS_MEAN"), _tok(v, "RET"),
        ),
        readable="SIGN(MUL(RET, TS_MEAN(RET)))",
        source="alpha101",
    ))

    # --- Alpha026: -1 * ts_max(ts_rank(ts_corr(RET, LOG_VOL, 5), 5))
    formulas.append(TranslatedFormula(
        alpha_id="alpha_026",
        tokens=(
            _tok(v, "NEG"), _tok(v, "TS_MAX"), _tok(v, "TS_RANK"),
            _tok(v, "TS_CORR"), _tok(v, "RET"), _tok(v, "LOG_VOL"),
        ),
        readable="NEG(TS_MAX(TS_RANK(TS_CORR(RET, LOG_VOL))))",
        source="alpha101",
    ))

    # --- Alpha032: ts_delta(HL_RANGE) / ts_mean(HL_RANGE)
    formulas.append(TranslatedFormula(
        alpha_id="alpha_032",
        tokens=(
            _tok(v, "DIV"),
            _tok(v, "TS_DELTA"), _tok(v, "HL_RANGE"),
            _tok(v, "TS_MEAN"), _tok(v, "HL_RANGE"),
        ),
        readable="DIV(TS_DELTA(HL_RANGE), TS_MEAN(HL_RANGE))",
        source="alpha101",
    ))

    # --- Alpha041: rank(DEV) * rank(ts_delta(RET))
    formulas.append(TranslatedFormula(
        alpha_id="alpha_041",
        tokens=(
            _tok(v, "MUL"),
            _tok(v, "RANK"), _tok(v, "DEV"),
            _tok(v, "RANK"), _tok(v, "TS_DELTA"), _tok(v, "RET"),
        ),
        readable="MUL(RANK(DEV), RANK(TS_DELTA(RET)))",
        source="alpha101",
    ))

    # --- Alpha044: ts_rank(ts_corr(RET, PRESSURE), 5)
    formulas.append(TranslatedFormula(
        alpha_id="alpha_044",
        tokens=(
            _tok(v, "TS_RANK"), _tok(v, "TS_CORR"), _tok(v, "RET"), _tok(v, "PRESSURE"),
        ),
        readable="TS_RANK(TS_CORR(RET, PRESSURE))",
        source="alpha101",
    ))

    # --- Alpha050: -1 * ts_max(rank(ts_corr(rank(RET), rank(LOG_VOL), 5)))
    formulas.append(TranslatedFormula(
        alpha_id="alpha_050",
        tokens=(
            _tok(v, "NEG"), _tok(v, "TS_MAX"), _tok(v, "TS_RANK"),
            _tok(v, "TS_CORR"), _tok(v, "RANK"), _tok(v, "RET"),
            _tok(v, "RANK"), _tok(v, "LOG_VOL"),
        ),
        readable="NEG(TS_MAX(TS_RANK(TS_CORR(RANK(RET), RANK(LOG_VOL)))))",
        source="alpha101",
    ))

    # --- Alpha053: -1 * ts_delta(rank(DECAY(RET)))
    formulas.append(TranslatedFormula(
        alpha_id="alpha_053",
        tokens=(
            _tok(v, "NEG"), _tok(v, "TS_DELTA"), _tok(v, "RANK"), _tok(v, "DECAY"), _tok(v, "RET"),
        ),
        readable="NEG(TS_DELTA(RANK(DECAY(RET))))",
        source="alpha101",
    ))

    # --- Alpha056: rank(RET - ts_mean(RET)) * rank(ts_corr(RET, LOG_VOL))
    formulas.append(TranslatedFormula(
        alpha_id="alpha_056",
        tokens=(
            _tok(v, "MUL"),
            _tok(v, "RANK"), _tok(v, "SUB"), _tok(v, "RET"), _tok(v, "TS_MEAN"), _tok(v, "RET"),
            _tok(v, "RANK"), _tok(v, "TS_CORR"), _tok(v, "RET"), _tok(v, "LOG_VOL"),
        ),
        readable="MUL(RANK(SUB(RET,TS_MEAN(RET))), RANK(TS_CORR(RET,LOG_VOL)))",
        source="alpha101",
    ))

    # --- Alpha060: rank(ABS(TS_DELTA(RET))) * rank(ts_corr(RET, FOMO))
    formulas.append(TranslatedFormula(
        alpha_id="alpha_060",
        tokens=(
            _tok(v, "MUL"),
            _tok(v, "RANK"), _tok(v, "ABS"), _tok(v, "TS_DELTA"), _tok(v, "RET"),
            _tok(v, "RANK"), _tok(v, "TS_CORR"), _tok(v, "RET"), _tok(v, "FOMO"),
        ),
        readable="MUL(RANK(ABS(TS_DELTA(RET))), RANK(TS_CORR(RET, FOMO)))",
        source="alpha101",
    ))

    # --- Alpha077: rank(DECAY(TS_DELTA(DEV)))
    formulas.append(TranslatedFormula(
        alpha_id="alpha_077",
        tokens=(
            _tok(v, "RANK"), _tok(v, "DECAY"), _tok(v, "TS_DELTA"), _tok(v, "DEV"),
        ),
        readable="RANK(DECAY(TS_DELTA(DEV)))",
        source="alpha101",
    ))

    # --- Alpha083: -1 * rank(ts_corr(RET, VOL_CLUSTER))
    formulas.append(TranslatedFormula(
        alpha_id="alpha_083",
        tokens=(
            _tok(v, "NEG"), _tok(v, "RANK"), _tok(v, "TS_CORR"), _tok(v, "RET"), _tok(v, "VOL_CLUSTER"),
        ),
        readable="NEG(RANK(TS_CORR(RET, VOL_CLUSTER)))",
        source="alpha101",
    ))

    # --- Alpha094: rank(ts_corr(RET, CLOSE_POS))
    formulas.append(TranslatedFormula(
        alpha_id="alpha_094",
        tokens=(
            _tok(v, "RANK"), _tok(v, "TS_CORR"), _tok(v, "RET"), _tok(v, "CLOSE_POS"),
        ),
        readable="RANK(TS_CORR(RET, CLOSE_POS))",
        source="alpha101",
    ))

    # --- Alpha101: (CLOSE_POS - ts_min(CLOSE_POS)) / (ts_max(CLOSE_POS) - ts_min(CLOSE_POS))
    formulas.append(TranslatedFormula(
        alpha_id="alpha_101",
        tokens=(
            _tok(v, "DIV"),
            _tok(v, "SUB"), _tok(v, "CLOSE_POS"), _tok(v, "TS_MIN"), _tok(v, "CLOSE_POS"),
            _tok(v, "SUB"), _tok(v, "TS_MAX"), _tok(v, "CLOSE_POS"), _tok(v, "TS_MIN"), _tok(v, "CLOSE_POS"),
        ),
        readable="DIV(SUB(CLOSE_POS,TS_MIN(CLOSE_POS)), SUB(TS_MAX(CLOSE_POS),TS_MIN(CLOSE_POS)))",
        source="alpha101",
    ))

    return formulas

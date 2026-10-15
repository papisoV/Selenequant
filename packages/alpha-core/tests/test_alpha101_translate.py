"""Tests for Alpha101 formula translation and warm-start integration."""

from __future__ import annotations


from selene_space_alpha.factors.alpha101_translate import (
    TranslatedFormula,
    translate_alpha101_subset,
)
from selene_space_alpha.vocab import FORMULA_VOCAB


def test_translate_returns_formulas():
    """translate_alpha101_subset should return a non-empty list."""
    formulas = translate_alpha101_subset()
    assert len(formulas) > 0


def test_translate_formula_type():
    """Each formula should be a TranslatedFormula instance."""
    formulas = translate_alpha101_subset()
    for f in formulas:
        assert isinstance(f, TranslatedFormula)
        assert f.source == "alpha101"
        assert len(f.alpha_id) > 0
        assert len(f.tokens) > 0
        assert len(f.readable) > 0


def test_translate_tokens_valid():
    """All token indices should be within vocab bounds."""
    formulas = translate_alpha101_subset()
    vocab = FORMULA_VOCAB
    for f in formulas:
        for t in f.tokens:
            assert 0 <= t < vocab.size, (
                f"Token {t} out of range [0, {vocab.size}) in {f.alpha_id}"
            )


def test_translate_roundtrip():
    """Encode then decode should produce a readable formula."""
    formulas = translate_alpha101_subset()
    vocab = FORMULA_VOCAB
    for f in formulas[:5]:
        decoded = vocab.decode(list(f.tokens))
        assert len(decoded) > 0
        has_feature = any(fn in decoded for fn in vocab.feature_names)
        assert has_feature, f"No feature in decoded: {decoded}"


def test_extended_vocab_size():
    """Extended vocab should have more than the original 19 tokens."""
    vocab = FORMULA_VOCAB
    assert vocab.size > 19
    assert vocab.feature_count >= 6
    assert len(vocab.operator_names) >= 13


def test_vocab_contains_v4_operators():
    """Vocab should include V4 time-series operators."""
    op_names = FORMULA_VOCAB.operator_names
    assert "TS_MEAN" in op_names
    assert "TS_STD" in op_names
    assert "TS_RANK" in op_names
    assert "RANK" in op_names
    assert "TS_DELTA" in op_names


def test_vocab_contains_v4_features():
    """Vocab should include V4 features."""
    feat_names = FORMULA_VOCAB.feature_names
    assert "VOL_CLUSTER" in feat_names
    assert "VWAP_DEV" in feat_names
    assert "CLOSE_POS" in feat_names


def test_specific_formula_tokens():
    """alpha_001 should produce RANK(MUL(RET, RET)) tokens."""
    formulas = translate_alpha101_subset()
    a001 = next(f for f in formulas if f.alpha_id == "alpha_001")
    vocab = FORMULA_VOCAB
    decoded = vocab.decode(list(a001.tokens))
    assert "RANK" in decoded
    assert "MUL" in decoded
    assert "RET" in decoded


def test_warm_start_formulas_no_duplicates():
    """Each alpha_id should be unique."""
    formulas = translate_alpha101_subset()
    ids = [f.alpha_id for f in formulas]
    assert len(ids) == len(set(ids))


def test_all_20_formulas_present():
    """Should have ~20 curated formulas."""
    formulas = translate_alpha101_subset()
    assert len(formulas) >= 15

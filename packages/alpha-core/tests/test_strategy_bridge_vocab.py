"""resolve_artifact_vocab — which vocab an alpha artifact's tokens decode in.

Mined formulas are searched in FORMULA_VOCAB; CEX_FEATURE_NAMES_V4 is a
27-feature subset with no LIQ_SCORE, so decoding a mined token list with it
shifts every index past slot 1 and silently backtests a different formula.
These tests pin which side that coin lands on.
"""

from __future__ import annotations

from typing import Any

from selene_space_alpha.real_data_loader import CEX_FEATURE_NAMES_V4
from selene_space_alpha.strategy_bridge import resolve_artifact_vocab
from selene_space_alpha.vocab import FORMULA_VOCAB


def test_declared_vocab_wins() -> None:
    payload = {
        "vocab": {
            "feature_names": list(FORMULA_VOCAB.feature_names),
            "operator_names": list(FORMULA_VOCAB.operator_names),
        }
    }
    vocab = resolve_artifact_vocab(payload)
    assert vocab.feature_names == FORMULA_VOCAB.feature_names
    assert vocab.operator_names == FORMULA_VOCAB.operator_names
    assert vocab.vocab_digest == FORMULA_VOCAB.vocab_digest


def test_missing_or_empty_block_falls_back_to_v4() -> None:
    # Legacy alpha_v4/v5/v6 archives predate the block; a writer with nothing to
    # declare emits empty lists. Both inherit the V4 default.
    empty: list[dict[str, Any] | None] = [
        None,
        {},
        {"vocab": {}},
        {"vocab": {"feature_names": [], "operator_names": []}},
    ]
    for payload in empty:
        assert resolve_artifact_vocab(payload).feature_names == CEX_FEATURE_NAMES_V4


def test_wrong_vocab_is_not_a_no_op() -> None:
    # The whole reason the function exists: same tokens, two different formulas.
    mined = resolve_artifact_vocab(
        {"vocab": {"feature_names": list(FORMULA_VOCAB.feature_names)}}
    )
    legacy = resolve_artifact_vocab(None)
    assert mined.vocab_digest != legacy.vocab_digest


def test_string_vocab_block_is_rejected_not_char_split() -> None:
    # best_meme_csi300.json shipped prose in the vocab block:
    #   {"feature_names": "CEX_FEATURE_NAMES (11)", "operator_names": "OPS_CONFIG ..."}
    # tuple("CEX...") char-splits into ('C','E','X',...) — a 21-char "feature set"
    # with an empty operator map — and the backtest then 500s with
    # "Formula execution returned None". A non-sequence must be treated as absent.
    payload = {
        "vocab": {
            "feature_names": "CEX_FEATURE_NAMES (11)",
            "operator_names": "OPS_CONFIG torch order incl GATE (21)",
        }
    }
    vocab = resolve_artifact_vocab(payload)
    assert vocab.feature_names == CEX_FEATURE_NAMES_V4
    assert "C" not in vocab.feature_names
    # The operator map must be non-empty, or PandasStackVM.execute returns None.
    assert len(vocab.operator_names) > 1
    assert "GATE" not in vocab.operator_names


def test_mixed_block_keeps_the_valid_side() -> None:
    # Only the malformed field falls back; the good one is still honoured.
    payload = {
        "vocab": {
            "feature_names": list(FORMULA_VOCAB.feature_names),
            "operator_names": "OPS_CONFIG ...",  # prose, not a list
        }
    }
    vocab = resolve_artifact_vocab(payload)
    assert vocab.feature_names == FORMULA_VOCAB.feature_names
    assert len(vocab.operator_names) > 1


def test_mining_torch_kind_resolves_the_mining_vocab() -> None:
    # best_meme_csi300.json declares vocab.kind == "mining_torch" but ships prose
    # for the names. The mining run (scripts/research-archive/
    # run_alpha_mining_sharpe.py) builds its tokens against
    # (CEX_FEATURE_NAMES, OPS_CONFIG torch order incl GATE) — 11 features + 21
    # ops. Decoding with V4 (27 features) shifted every op index and the
    # backtest 500'd with "Formula execution returned None". The declared kind
    # must resolve to the matching vocab, not the V4 fallback.
    from selene_space_alpha.ops.ops import OPS_CONFIG
    from selene_space_alpha.real_data_loader import CEX_FEATURE_NAMES

    payload = {
        "vocab": {
            "kind": "mining_torch",
            "feature_names": "CEX_FEATURE_NAMES (11)",
            "operator_names": "OPS_CONFIG torch order incl GATE (21)",
        }
    }
    vocab = resolve_artifact_vocab(payload)
    assert vocab.feature_names == tuple(CEX_FEATURE_NAMES)
    assert vocab.operator_names == tuple(cfg[0] for cfg in OPS_CONFIG)
    assert "GATE" in vocab.operator_names


def test_mining_torch_vocab_decodes_its_own_tokens() -> None:
    # The end-to-end assertion the 500 was missing: the artifact's tokens must
    # actually parse as a valid formula under the resolved vocab.
    from selene_space_alpha.ops.ops import OPS_CONFIG
    from selene_space_alpha.real_data_loader import CEX_FEATURE_NAMES

    tokens = [1, 25, 15, 25, 28, 7, 31, 12]  # best_meme_csi300.best_formula.tokens
    vocab = resolve_artifact_vocab(
        {"vocab": {"kind": "mining_torch", "feature_names": "x", "operator_names": "y"}}
    )
    assert vocab.feature_names == tuple(CEX_FEATURE_NAMES)
    assert vocab.operator_names == tuple(cfg[0] for cfg in OPS_CONFIG)
    # Every token must land on a feature or a known operator (the char-split bug
    # left op_map empty, so tokens >= feature_count silently miss).
    offset = len(vocab.feature_names)
    known = set(range(offset)) | {offset + i for i in range(len(vocab.operator_names))}
    assert all(t in known for t in tokens), [t for t in tokens if t not in known]

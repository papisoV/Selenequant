"""FormulaVocab deterministic versioning — vocab_digest + verify.

Verifies the sha256 fingerprint over token names is deterministic, order- and
content-sensitive, and that :meth:`FormulaVocab.verify` rejects mismatches
while tolerating pre-digest artifacts unless ``strict=True``. Also asserts the
vocab module stays torch-free (the search import chain depends on it).
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from selene_space_alpha.ops.registry import OPERATOR_DEFS
from selene_space_alpha.vocab import (
    FEATURE_NAMES,
    FORMULA_VOCAB,
    FORMULA_VOCAB_DIGEST,
    FORMULA_VOCAB_TOKEN_LAYOUT_DIGEST,
    FormulaVocab,
)
from selene_space_factor_research.mining.report import load_top_formulas
from selene_space_factor_research.mining.supply_line import gate_vocab_digest

_PKG_DIRS = [
    str(pathlib.Path(__file__).resolve().parents[3] / "packages" / "factor-research"),
    str(pathlib.Path(__file__).resolve().parents[3] / "packages" / "alpha-core"),
    str(pathlib.Path(__file__).resolve().parents[3] / "packages" / "core"),
]


def test_digest_is_deterministic() -> None:
    """Same vocab queried twice yields the bit-identical digest."""
    d1 = FORMULA_VOCAB.vocab_digest
    d2 = FORMULA_VOCAB.vocab_digest
    assert d1 == d2
    assert d1 == FORMULA_VOCAB_DIGEST


def test_digest_format() -> None:
    """Selene-specific prefix + 16 hex chars (independent of AlphaMaster format)."""
    d = FORMULA_VOCAB.vocab_digest
    assert d.startswith("selene-vocab-")
    assert len(d) == len("selene-vocab-") + 16
    assert all(c in "0123456789abcdef" for c in d[len("selene-vocab-") :])


def test_equivalent_vocab_same_digest() -> None:
    """A vocab built from the same names yields the same digest as FORMULA_VOCAB."""
    twin = FormulaVocab(
        feature_names=FEATURE_NAMES,
        operator_names=tuple(name for name, _ in OPERATOR_DEFS),
    )
    assert twin.vocab_digest == FORMULA_VOCAB.vocab_digest


def test_feature_reorder_changes_digest() -> None:
    """Reordering features changes the digest (order is a structural contract)."""
    reordered = list(FEATURE_NAMES)
    reordered[0], reordered[1] = reordered[1], reordered[0]
    vocab = FormulaVocab(
        feature_names=tuple(reordered),
        operator_names=tuple(name for name, _ in OPERATOR_DEFS),
    )
    assert vocab.vocab_digest != FORMULA_VOCAB.vocab_digest


def test_operator_set_change_changes_digest() -> None:
    """Adding/removing an operator changes the digest."""
    trimmed = tuple(name for name, _ in OPERATOR_DEFS if name != "ABS")
    vocab = FormulaVocab(
        feature_names=FEATURE_NAMES,
        operator_names=trimmed,
    )
    assert vocab.vocab_digest != FORMULA_VOCAB.vocab_digest


def test_verify_match_is_silent() -> None:
    """A matching digest does not raise."""
    FORMULA_VOCAB.verify(FORMULA_VOCAB.vocab_digest)


def test_verify_mismatch_raises() -> None:
    """A non-matching digest raises ValueError naming both digests."""
    with pytest.raises(ValueError, match="mismatch") as exc:
        FORMULA_VOCAB.verify("selene-vocab-deadbeefdeadbeef")
    assert "deadbeefdeadbeef" in str(exc.value)
    assert FORMULA_VOCAB.vocab_digest in str(exc.value)


def test_verify_absent_non_strict_is_silent() -> None:
    """A None digest (pre-digest artifact) is tolerated by default."""
    FORMULA_VOCAB.verify(None)
    FORMULA_VOCAB.verify(None, strict=False)


def test_verify_absent_strict_raises() -> None:
    """A None digest under strict=True raises (audit mode)."""
    with pytest.raises(ValueError, match="missing"):
        FORMULA_VOCAB.verify(None, strict=True)


def test_vocab_import_is_torch_free() -> None:
    """Importing the vocab module in a fresh interpreter pulls no torch."""
    code = (
        "import sys\n"
        f"sys.path[:0] = {_PKG_DIRS!r}\n"
        "import selene_space_alpha.vocab\n"
        "print('TORCH_PRESENT' if 'torch' in sys.modules else 'TORCH_FREE')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "TORCH_FREE" in proc.stdout


# --- runner payload contract (top_formulas.json vocab_digest) -------------


def _write_top_formulas(tmp_path: pathlib.Path, *, with_digest: bool) -> pathlib.Path:
    """Write a minimal top_formulas.json payload (the T3 runner's output shape)."""
    payload: dict = {
        "symbols": ["BTC/USDT", "ETH/USDT"],
        "interval": "4h",
        "bars_per_symbol": 150,
        "seed": 42,
        "eval_budget": 400,
        "evaluations_used": 340,
        "generations": 15,
        "stopped_reason": "stagnation",
        "top_formulas": [],
    }
    if with_digest:
        payload["vocab_digest"] = FORMULA_VOCAB.vocab_digest
    path = tmp_path / "top_formulas.json"
    path.write_text(__import__("json").dumps(payload), encoding="utf-8")
    return path


def test_runner_payload_digest_loads(tmp_path: pathlib.Path) -> None:
    """A post-digest top_formulas.json (with matching vocab_digest) loads OK."""
    path = _write_top_formulas(tmp_path, with_digest=True)
    payload = load_top_formulas(path)
    assert payload["vocab_digest"] == FORMULA_VOCAB.vocab_digest


def test_pre_digest_payload_tolerated_non_strict(tmp_path: pathlib.Path) -> None:
    """A pre-digest top_formulas.json (no vocab_digest) loads under default mode."""
    path = _write_top_formulas(tmp_path, with_digest=False)
    payload = load_top_formulas(path)
    assert "vocab_digest" not in payload


def test_pre_digest_payload_rejected_strict(tmp_path: pathlib.Path) -> None:
    """A pre-digest top_formulas.json is rejected under strict audit mode."""
    path = _write_top_formulas(tmp_path, with_digest=False)
    with pytest.raises(ValueError, match="missing"):
        load_top_formulas(path, strict_vocab=True)


# --- token layout digest: a feature append must not be silent -------------


def test_token_layout_digest_format_and_export() -> None:
    """Selene-specific prefix + 16 hex chars, exported as a module constant."""
    d = FORMULA_VOCAB.token_layout_digest
    assert d == FORMULA_VOCAB_TOKEN_LAYOUT_DIGEST
    assert d.startswith("selene-tokenlayout-")
    assert len(d) == len("selene-tokenlayout-") + 16
    assert all(c in "0123456789abcdef" for c in d[len("selene-tokenlayout-") :])


def test_appending_a_feature_moves_only_the_layout_digest() -> None:
    """The hole this digest closes.

    The shared segment is ``feature_names[:77] + operators``, so a feature
    appended to the tail is invisible to ``vocab_digest`` — yet it pushes every
    operator id up by one, and a previously-minted operator token then decodes
    as the new feature. Only the layout digest notices.
    """
    grown = FormulaVocab(
        feature_names=FEATURE_NAMES + ("PONGPONG",),
        operator_names=tuple(name for name, _ in OPERATOR_DEFS),
    )
    assert grown.vocab_digest == FORMULA_VOCAB.vocab_digest
    assert grown.token_layout_digest != FORMULA_VOCAB.token_layout_digest
    assert grown.operator_offset == FORMULA_VOCAB.operator_offset + 1


def test_appending_an_operator_moves_both_digests() -> None:
    """Operators sit inside the shared segment, so that append is already loud."""
    grown = FormulaVocab(
        feature_names=FEATURE_NAMES,
        operator_names=tuple(name for name, _ in OPERATOR_DEFS) + ("PONGPONG",),
    )
    assert grown.vocab_digest != FORMULA_VOCAB.vocab_digest
    assert grown.token_layout_digest != FORMULA_VOCAB.token_layout_digest


def test_equivalent_vocab_same_layout_digest() -> None:
    """Same names in the same order yield the same layout digest."""
    twin = FormulaVocab(
        feature_names=FEATURE_NAMES,
        operator_names=tuple(name for name, _ in OPERATOR_DEFS),
    )
    assert twin.token_layout_digest == FORMULA_VOCAB.token_layout_digest


def test_verify_token_layout_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="token layout digest mismatch"):
        FORMULA_VOCAB.verify_token_layout("selene-tokenlayout-deadbeefdeadbeef")


def test_verify_token_layout_absent_tolerated_unless_strict() -> None:
    FORMULA_VOCAB.verify_token_layout(None)
    with pytest.raises(ValueError, match="missing"):
        FORMULA_VOCAB.verify_token_layout(None, strict=True)


# --- G1 carries the layout check to the mining gate -----------------------


def test_g1_fires_on_layout_mismatch_alone() -> None:
    """Stale layout digest rejects the artifact even when vocab_digest matches."""
    fire = gate_vocab_digest(
        {
            "vocab_digest": FORMULA_VOCAB.vocab_digest,
            "token_layout_digest": "selene-tokenlayout-deadbeefdeadbeef",
        }
    )
    assert fire is not None
    assert fire.gate == "G1"
    assert "token layout digest mismatch" in fire.reason


def test_g1_passes_when_both_digests_match() -> None:
    assert (
        gate_vocab_digest(
            {
                "vocab_digest": FORMULA_VOCAB.vocab_digest,
                "token_layout_digest": FORMULA_VOCAB.token_layout_digest,
            }
        )
        is None
    )


def test_g1_tolerates_artifact_predating_the_layout_digest() -> None:
    """Legacy artifacts carry only vocab_digest and must still gate in."""
    assert gate_vocab_digest({"vocab_digest": FORMULA_VOCAB.vocab_digest}) is None

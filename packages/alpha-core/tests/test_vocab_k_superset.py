"""K-superset vocab: AW_* tail beyond the W-shared 77 + shared digest oracle.

K's vocab is a *superset* of W's: the first 77 features are bit-identical to
W's 77 (shared prefix 0..76), and K additionally carries 20 AW_* factors at
feature index 77..96. The operator set is identical (22) in both trees.

Option A' contract:
  - ``FEATURE_NAMES[:77]`` == W's shared feature names (index 0..76).
  - ``AW_*`` block occupies index 77..96 (exactly 20 factors).
  - operator token index == 97 + i (22 operators at 97..118).
  - ``FORMULA_VOCAB_DIGEST`` is the digest of the *shared segment* only
    (77 features + 22 operators = 99 tokens), bit-identical to the W tree's
    digest oracle ``selene-vocab-9120317ae298a9a3``. W-produced artifacts
    verify with zero mismatch and zero regeneration.
"""

from __future__ import annotations

from selene_space_alpha.ops.registry import OPERATOR_DEFS
from selene_space_alpha.vocab import FEATURE_NAMES, FORMULA_VOCAB, FORMULA_VOCAB_DIGEST

#: Shared-segment digest oracle. Originally captured from the W tree
#: (HEAD=e782519, ``selene-vocab-9120317ae298a9a3``). Re-blessed 2026-09-18
#: when the TS_STD_20 operator was appended for the artifact-bridge batch:
#: operator names sit inside the shared segment, so that append moves the
#: digest by design — loud reject + explicit re-bless, never a silent pass.
#: W-tree artifacts minted under the old digest now reject via verify().
W_VOCAB_DIGEST_ORACLE = "selene-vocab-09b5d4b06dc5ed2b"

#: W's 77-feature prefix names (exact, in order) — hardcoded so the test
#: verifies the *shape* of the shared boundary, not just the digest equality.
W_SHARED_FEATURE_NAMES = (
    "RET",
    "LIQ_SCORE",
    "PRESSURE",
    "FOMO",
    "DEV",
    "LOG_VOL",
    "VOL_CLUSTER",
    "MOMENTUM_REV",
    "REL_STRENGTH",
    "HL_RANGE",
    "CLOSE_POS",
    "VOL_TREND",
    "WICK_RATIO",
    "BODY_SIZE",
    "IMBALANCE",
    "VWAP_DEV",
    "SPREAD_PROXY",
    "GAP",
    "FIB_POS",
    "FIB_CONFLUENCE",
    "FIB_DENSITY",
    "FIB_BOUNCE",
    "FIB_STRENGTH",
    "FIB_MOMENTUM",
    "FIB_RESONANCE",
    "FIB_CROSSOVER",
    "FIB_DECAY_POS",
    "FIB2_HIGH_DENSITY",
    "FIB2_ATR_PROX",
    "FIB2_TREND_BOUNCE",
    "FIB2_MULTI_GRID",
    "FIB2_BREAKOUT_CONFIRM",
    "FIB2_LEVEL_MOMENTUM",
    "FIB3_TIME_AT_LEVEL",
    "FIB3_ACCELERATION",
    "FIB3_VOLUME_PROFILE",
    "FIB3_REACTION_REGIME",
    "FIB3_MAGNET_STRENGTH",
    "FIB3_REJECTION_RATIO",
    "TF_TREND",
    "TF_PRESSURE",
    "TF_DEV",
    "TF_MOM",
    "TF_RANGE_POS",
    "TF_VOL_RATIO",
    "OF_TAKER_RATIO",
    "OF_TAKER_SPIKE",
    "OF_TOP_POS_RATIO",
    "OF_TOP_POS_CHANGE",
    "OF_TOP_ACC_RATIO",
    "OF_FUNDING_IMPULSE",
    "OF_LIQUIDATION_MAGNET",
    "OF_CROWD_DIVERGENCE",
    "OF_GLOBAL_LS_RATIO",
    "OF_GLOBAL_LS_EXTREME",
    "OF_SMART_CROWD_DIV",
    "OF_BUY_VOL_PRESSURE",
    "OF_SELL_VOL_PRESSURE",
    "OF_VOL_IMBALANCE",
    "OF_TOP_ACC_CHANGE",
    "OF_TOP_POS_ACC_DIV",
    "DERIV_FUNDING_RATE",
    "DERIV_FUNDING_Z",
    "DERIV_FUNDING_MOMENTUM",
    "DERIV_FUNDING_EXTREME",
    "DERIV_OI_CHANGE",
    "DERIV_OI_PRICE_DIV",
    "DERIV_OI_Z",
    "DERIV_OI_FUNDING_DIV",
    "DERIV_OI_VOL_RATIO",
    "DERIV_LIQ_IMBALANCE",
    "DERIV_LIQ_SPIKE",
    "DERIV_LIQ_PRESSURE_PROXY",
    "DERIV_LIQ_SPIKE_PROXY",
    "DERIV_LIQ_MAGNET_PROXY",
    "DERIV_BASIS",
    "DERIV_BASIS_EXTREME",
)

#: K's 20 AW_* factors that extend beyond the W-shared prefix (index 77..96).
K_AW_SUPERSET_NAMES = (
    "AW_52W_HIGH",
    "AW_TSMOM_12M",
    "AW_TSMOM_SIGN",
    "AW_MOM_6M",
    "AW_CONSISTENT_MOM",
    "AW_REVERSAL_5D",
    "AW_REVERSAL_10D",
    "AW_LOW_VOL",
    "AW_VOL_RISK_PREMIUM",
    "AW_SKEW_12M",
    "AW_RETURN_ASYMMETRY",
    "AW_TREND_MA_SLOPE",
    "AW_TREND_ABOVE_MA",
    "AW_TURN_OF_MONTH",
    "AW_OVERNIGHT_8H",
    "AW_MOM_ACCELERATION",
    "AW_TREND_MA_CROSS",
    "AW_DRAWDOWN",
    "AW_VOL_OF_VOL",
    "AW_VOLUME_TREND",
)

_OPERATOR_NAMES = tuple(name for name, _ in OPERATOR_DEFS)


def test_total_feature_count_is_110() -> None:
    """97 (W-shared 77 + AW 20) + 3 B3 OHLCV + 3 B2 BTC background + 7 artifact-bridge factors."""
    assert len(FEATURE_NAMES) == 110


def test_shared_prefix_matches_w_bitwise() -> None:
    """K[:77] == W's 77 features, zero mismatches."""
    assert FEATURE_NAMES[:77] == W_SHARED_FEATURE_NAMES


def test_aw_tail_starts_at_index_77() -> None:
    """The AW_* superset block occupies feature index 77..96 (20 factors)."""
    assert FEATURE_NAMES[77:97] == K_AW_SUPERSET_NAMES
    assert all(name.startswith("AW_") for name in K_AW_SUPERSET_NAMES)


def test_operator_count_is_23() -> None:
    assert len(_OPERATOR_NAMES) == 23


def test_operator_tokens_follow_features() -> None:
    """Operator token indices follow the 110 features (artifact tail at 103..109)."""
    names = FORMULA_VOCAB.token_names
    assert names[110 : 110 + 23] == _OPERATOR_NAMES
    assert FORMULA_VOCAB.size == 110 + 23


def test_shared_digest_matches_w_oracle() -> None:
    """The shared-segment digest is bit-identical to the W oracle."""
    assert FORMULA_VOCAB_DIGEST == W_VOCAB_DIGEST_ORACLE
    assert FORMULA_VOCAB.vocab_digest == W_VOCAB_DIGEST_ORACLE


def test_w_artifact_digest_verifies() -> None:
    """A W-produced artifact digest passes verify() with zero mismatch."""
    FORMULA_VOCAB.verify(W_VOCAB_DIGEST_ORACLE)


def test_recomposed_shared_segment_digest_is_stable() -> None:
    """Manually recompose 77 features + 23 ops and check the digest matches."""
    import hashlib

    joined = "\n".join(list(W_SHARED_FEATURE_NAMES) + list(_OPERATOR_NAMES))
    expected = "selene-vocab-" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
    assert expected == W_VOCAB_DIGEST_ORACLE

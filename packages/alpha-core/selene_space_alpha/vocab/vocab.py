"""Formula vocabulary - feature and operator token mapping.

Deterministic vocab versioning
------------------------------
:class:`FormulaVocab.vocab_digest` is a sha256 fingerprint over an ordered
token name list (features then operators). It depends only on the token names
and their order — never on time, file paths, or a hand-maintained version
string — so two vocabs built from the same names are bit-identical.

:func:`FormulaVocab.verify` compares an artifact-recorded digest against the
currently-derived one and raises on mismatch, so a formula serialized under one
vocab cannot be silently re-scored under another. Absent digests (older
artifacts) are tolerated by default and rejected only under ``strict=True``.

The digest format (prefix + width) is a Selene-specific choice; it is an
independent implementation of the AlphaMaster ``compute_vocab_version`` /
``verify`` *design intent* (deterministic vocab fingerprint + reject on
mismatch), not a copy of its identifiers or format.

Shared-segment digest (Option A')
---------------------------------
K keeps all 97 features (the W tree's 77 features are the exact shared prefix
0..76; the AW_* block occupies feature index 77..96). The *digest* is computed
over the shared segment only — ``feature_names[:_SHARED_FEATURE_COUNT] +
operator_names`` (77 features + 22 operators, explicitly re-joined) — so it is
bit-identical to the W tree's digest. W-produced artifacts therefore verify with
zero mismatch and zero regeneration. The AW_* tail does not participate in the
digest.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..ops.registry import OPERATOR_DEFS


FEATURE_NAMES: tuple[str, ...] = (
    "RET",
    "LIQ_SCORE",
    "PRESSURE",
    "FOMO",
    "DEV",
    "LOG_VOL",
    # V4 features
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
    # FIB factors (9) — merged from selenespace-research
    "FIB_POS",
    "FIB_CONFLUENCE",
    "FIB_DENSITY",
    "FIB_BOUNCE",
    "FIB_STRENGTH",
    "FIB_MOMENTUM",
    "FIB_RESONANCE",
    "FIB_CROSSOVER",
    "FIB_DECAY_POS",
    # FIB2 factors (6)
    "FIB2_HIGH_DENSITY",
    "FIB2_ATR_PROX",
    "FIB2_TREND_BOUNCE",
    "FIB2_MULTI_GRID",
    "FIB2_BREAKOUT_CONFIRM",
    "FIB2_LEVEL_MOMENTUM",
    # FIB3 factors (6)
    "FIB3_TIME_AT_LEVEL",
    "FIB3_ACCELERATION",
    "FIB3_VOLUME_PROFILE",
    "FIB3_REACTION_REGIME",
    "FIB3_MAGNET_STRENGTH",
    "FIB3_REJECTION_RATIO",
    # TF factors (6) — multi-timeframe
    "TF_TREND",
    "TF_PRESSURE",
    "TF_DEV",
    "TF_MOM",
    "TF_RANGE_POS",
    "TF_VOL_RATIO",
    # OF factors (15) — orderflow v13
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
    # DERIV factors (14) — derivatives v13/v14/v15
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
    # Awesome-systematic-trading factors (20) — single-asset, OHLCV-only
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
    # QD-absorption B3 factors (3) — OHLCV-only technical indicators
    "CHOPPINESS_14",
    "ELDER_BULL_13",
    "ELDER_BEAR_13",
    # QD-absorption B2 factors (3) — BTC relative-strength background
    "BTC_BG_RS_LEVEL",
    "BTC_BG_RS_SLOPE",
    "BTC_BG_RS_EXTREME",
    # Artifact-bridge factors (7) — AlphaMaster *_autogen_factor.py feature ports
    "ICHIMOKU_KIJUN_DEV",
    "GK_VOL",
    "RET_ENTROPY_20",
    "RVOL",
    "MACD_HIST",
    "TRIX_15",
    "HMA_DEV_20",
)

#: Number of features that form the shared prefix with the W tree (feature
#: index 0..76). The digest is computed over this slice explicitly re-joined
#: with the operator names so it stays bit-identical to the W tree's digest.
#: Do NOT slice ``token_names[:99]`` — that depends on K's layout (AW_* at
#: 77..96) and would drift from W.
_SHARED_FEATURE_COUNT = 77


@dataclass(frozen=True)
class FormulaVocab:
    """Token vocabulary for formula sequences.

    Token indices 0..feature_count-1 map to features.
    Token indices feature_count..size-1 map to operators.
    """

    feature_names: tuple[str, ...]
    operator_names: tuple[str, ...]

    @property
    def feature_count(self) -> int:
        return len(self.feature_names)

    @property
    def operator_offset(self) -> int:
        return self.feature_count

    @property
    def token_names(self) -> tuple[str, ...]:
        return self.feature_names + self.operator_names

    @property
    def size(self) -> int:
        return len(self.token_names)

    @property
    def vocab_digest(self) -> str:
        """Deterministic sha256 fingerprint over the *shared-segment* tokens.

        Computed over ``feature_names[:_SHARED_FEATURE_COUNT] + operator_names``
        (the exact shared prefix with the W tree, explicitly re-joined) so it is
        bit-identical to the W tree's digest. AW_* tail features do not
        participate — a formula produced under the W vocab verifies with zero
        mismatch.

        Stable across processes and time: the same names yield the same digest.
        Order-sensitive (reordering changes the digest), so it doubles as a
        structural contract identifier for serialized formulas.
        """
        joined = "\n".join(
            list(self.feature_names[:_SHARED_FEATURE_COUNT]) + list(self.operator_names)
        )
        return "selene-vocab-" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]

    @property
    def token_layout_digest(self) -> str:
        """sha256 fingerprint over the *complete* token list, features then operators.

        ``vocab_digest`` covers only the shared segment, so the W tree's
        artifacts keep verifying. That leaves a hole: appending a *feature*
        leaves the shared segment untouched but moves every operator's id up by
        one. An artifact minted against the old layout would still pass the
        digest gate and then decode its operator ids as the newly appended
        feature names — wrong numbers, reported confidently.

        Covering every name a token id can resolve to closes that hole: any
        append, feature or operator, changes this digest, so the artifact is
        rejected by name instead of being silently misread.
        """
        joined = "\n".join(self.token_names)
        return (
            "selene-tokenlayout-"
            + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
        )

    def verify(self, artifact_digest: str | None, *, strict: bool = False) -> None:
        """Validate an artifact-recorded digest against the current vocab.

        Args:
            artifact_digest: digest persisted alongside a formula artifact
                (e.g. ``top_formulas.json["vocab_digest"]``), or ``None`` when
                the artifact predates digest recording.
            strict: when ``True`` an absent digest is rejected; when ``False``
                (default) it is tolerated for backward compatibility.

        Raises:
            ValueError: on mismatch, or on absent digest under ``strict=True``.
        """
        self._verify_digest(
            artifact_digest, self.vocab_digest, label="vocab digest", strict=strict
        )

    def verify_token_layout(
        self, artifact_digest: str | None, *, strict: bool = False
    ) -> None:
        """Validate an artifact-recorded *token layout* digest.

        Same contract as :meth:`verify`, against
        :attr:`token_layout_digest` instead of :attr:`vocab_digest`. Callers
        that re-score a persisted token chain want this one: it is the digest
        that moves when the feature/operator boundary moves.
        """
        self._verify_digest(
            artifact_digest,
            self.token_layout_digest,
            label="token layout digest",
            strict=strict,
        )

    def _verify_digest(
        self,
        artifact_digest: str | None,
        current: str,
        *,
        label: str,
        strict: bool,
    ) -> None:
        if artifact_digest is None:
            if strict:
                raise ValueError(
                    f"artifact is missing a {label} (strict mode requires one)"
                )
            return
        if artifact_digest != current:
            raise ValueError(
                f"{label} mismatch: artifact {artifact_digest!r} != "
                f"current {current!r}"
            )

    def decode(self, tokens: list[int]) -> str:
        """Decode a token sequence into a human-readable formula string."""
        parts: list[str] = []
        for t in tokens:
            if t < self.size:
                parts.append(self.token_names[t])
            else:
                parts.append(f"UNK({t})")
        return " ".join(parts)


FORMULA_VOCAB = FormulaVocab(
    feature_names=FEATURE_NAMES,
    operator_names=tuple(name for name, _ in OPERATOR_DEFS),
)

#: Deterministic digest of the *shared segment* (``feature_names[:77]`` +
#: operator names) — bit-identical to the W tree's digest, so W-produced
#: artifacts verify without mismatch. Record it alongside serialized formula
#: artifacts so :meth:`FormulaVocab.verify` can reject artifacts produced
#: under a different token vocabulary.
FORMULA_VOCAB_DIGEST: str = FORMULA_VOCAB.vocab_digest

#: Deterministic digest of the *complete* token list. Record it alongside every
#: serialized formula artifact in addition to the shared-segment digest above:
#: the shared digest alone cannot see a feature appended to the tail, and such
#: an append silently renumbers every operator id.
FORMULA_VOCAB_TOKEN_LAYOUT_DIGEST: str = FORMULA_VOCAB.token_layout_digest

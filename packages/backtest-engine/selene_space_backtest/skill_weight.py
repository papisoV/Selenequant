# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); upstream read its statistics through
# ``SkillOpinionPerformanceService`` (which owns a SQLAlchemy repository) and
# folded the policy into ``SkillOpinionWeightService.compute_weights``. Here the
# policy is a free pure function over plain bucket dicts, so the maths is
# replayable offline and unit-testable in isolation — the same seam as the
# sibling :mod:`selene_space_backtest.signal_outcome`.
"""Bounded per-Skill performance factors from attributable opinion outcomes.

A Skill that has been right about "up" calls for a while is worth listening to
a little more than one that has been wrong; a Skill whose calls keep coming
back *unjudgeable* is worth a little less. This module turns that intuition
into one number per Skill, and does it conservatively:

  - **Prior-dominated.** The hit rate is a Beta posterior with a symmetric
    15/15 prior, so a Skill needs a real sample before its score moves much.
    Below ``MIN_SKILL_OUTCOME_SAMPLE_SIZE`` it does not move *at all*.
  - **Bounded.** The factor lives in ``[1/1.2, 1.2]``; no Skill can dominate a
    blend, and none can be silenced by a bad stretch.
  - **Fail-neutral.** Anything missing, malformed, ambiguous or not-yet-sampled
    yields exactly ``1.0``. A weight that cannot be justified is not applied —
    the same honest-refusal discipline as :mod:`signal_outcome`, expressed as
    "leave the Skill alone" rather than "record a verdict".

The maths, per (skill, horizon) bucket::

    posterior   = (hit + 15) / (evaluated + 30)
    direction   = 2 * posterior - 1                     # -1 .. +1
    unable_rate = unable / (evaluated + observational + unable)
    bucket      = clamp(-1, +1, direction - 0.25 * unable_rate)
    evidence    = evaluated / (evaluated + 30)          # 0 .. 1
    combined    = Σ(bucket * evidence) / Σ(evidence)    # across horizons
    factor      = clamp(1/1.2, 1.2, 1.2 ** combined)

The four admission gates are deliberate and each one is load-bearing: a bucket
whose ``engine_version`` is stale, whose horizon is unsupported, which does not
declare a sufficient sample, or whose counts do not add up is **skipped, not
scaled**. A half-understood statistic is worse than no statistic.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from selene_space_backtest.signal_outcome import SIGNAL_OUTCOME_HORIZONS

# Symmetric Beta prior: 15 hits + 15 misses. Chosen so the first real
# observations move the posterior slowly — a Skill earns its weight.
BETA_PRIOR_HITS = 15
BETA_PRIOR_MISSES = 15
BETA_PRIOR_SIZE = BETA_PRIOR_HITS + BETA_PRIOR_MISSES

# Each *terminal* unjudgeable outcome shaves the score. 0.25 means a bucket
# that is 100% unable loses a quarter of its directional signal — a nudge, not
# a veto: "I could not judge this" is not evidence of being wrong.
UNABLE_PENALTY = 0.25

MAX_SKILL_WEIGHT_FACTOR = 1.2
MIN_SKILL_WEIGHT_FACTOR = 1.0 / MAX_SKILL_WEIGHT_FACTOR

#: The factor of a Skill with no usable evidence. Exactly 1.0 — never a guess.
NEUTRAL_SKILL_WEIGHT_FACTOR = 1.0

#: Fewer directional judgments than this and the Skill keeps its neutral weight.
MIN_SKILL_OUTCOME_SAMPLE_SIZE = 30

#: Horizons a bucket may claim. Re-exported from the sibling outcome module so
#: the two can never drift apart about what a "5d" window means.
SUPPORTED_SKILL_WEIGHT_HORIZONS = frozenset(SIGNAL_OUTCOME_HORIZONS)


def compute_skill_weight_factors(
    *,
    skill_ids: Sequence[str],
    buckets: Sequence[Mapping[str, Any]],
    engine_version: str,
) -> dict[str, float]:
    """Return one bounded performance factor per requested Skill.

    ``buckets`` are per (skill, horizon) aggregates carrying ``skill_id``,
    ``horizon``, ``engine_version``, ``evaluated``, ``hit``, ``miss``,
    ``observational``, ``unable`` and ``sample_sufficient`` — the shape
    :meth:`DecisionSignalRepository.skill_weight_rows` emits and the same shape
    DSA's performance service emitted. Every requested Skill appears in the
    result; those without usable evidence keep ``1.0``.
    """
    requested = _normalize_skill_ids(skill_ids)
    neutral = {skill_id: NEUTRAL_SKILL_WEIGHT_FACTOR for skill_id in requested}
    version = str(engine_version or "").strip()
    if not requested or not version:
        return neutral

    requested_set = set(requested)
    scored: dict[str, list[tuple[float, float]]] = {
        skill_id: [] for skill_id in requested
    }
    invalid_skills: set[str] = set()
    seen_buckets: set[tuple[str, str]] = set()

    for bucket in buckets:
        skill_id = _bucket_skill_id(bucket)
        if skill_id not in requested_set:
            continue
        if not _bucket_is_current(bucket, engine_version=version):
            continue

        horizon = str(bucket.get("horizon") or "").strip()
        bucket_key = (skill_id, horizon)
        # Two buckets for one (skill, horizon) means the caller is confused
        # about identity — that Skill's evidence is unusable, not additive.
        if bucket_key in seen_buckets:
            invalid_skills.add(skill_id)
            continue
        seen_buckets.add(bucket_key)

        if not _declares_sufficient_sample(bucket):
            continue

        score = score_bucket(bucket)
        if score is None:
            invalid_skills.add(skill_id)
            continue
        scored[skill_id].append(score)

    for skill_id, bucket_scores in scored.items():
        if skill_id in invalid_skills or not bucket_scores:
            continue
        factor = _combine(bucket_scores)
        if factor is None:
            continue
        neutral[skill_id] = factor

    return neutral


def score_bucket(bucket: Mapping[str, Any]) -> tuple[float, float] | None:
    """Score one (skill, horizon) bucket as ``(bucket_score, evidence_strength)``.

    ``None`` means the bucket is unusable: a count is missing, negative, a
    bool, or the arithmetic does not add up (``hit + miss != evaluated``).
    Returning ``None`` rather than a guess is what keeps a malformed bucket
    from quietly reweighting every Skill that has one.
    """
    evaluated = _count(bucket.get("evaluated"))
    hit = _count(bucket.get("hit"))
    miss = _count(bucket.get("miss"))
    observational = _count(bucket.get("observational"))
    unable = _count(bucket.get("unable"))
    if (
        evaluated is None
        or hit is None
        or miss is None
        or observational is None
        or unable is None
    ):
        return None
    # A directional judgment is one that produced a verdict; the counts must
    # reconcile, or the caller is mixing rows from different populations.
    if hit + miss != evaluated:
        return None
    if evaluated < MIN_SKILL_OUTCOME_SAMPLE_SIZE:
        return None

    posterior_hit_rate = (hit + BETA_PRIOR_HITS) / (evaluated + BETA_PRIOR_SIZE)
    direction_score = 2.0 * posterior_hit_rate - 1.0

    terminal_count = evaluated + observational + unable
    if terminal_count <= 0:
        return None
    unable_rate = unable / terminal_count
    bucket_score = min(1.0, max(-1.0, direction_score - UNABLE_PENALTY * unable_rate))

    evidence_strength = evaluated / (evaluated + BETA_PRIOR_SIZE)
    if not all(math.isfinite(v) for v in (bucket_score, evidence_strength)):
        return None
    return bucket_score, evidence_strength


def _combine(bucket_scores: Sequence[tuple[float, float]]) -> float | None:
    """Evidence-weight the per-horizon scores into one bounded factor."""
    evidence_total = sum(strength for _, strength in bucket_scores)
    if not math.isfinite(evidence_total) or evidence_total <= 0:
        return None
    combined = (
        sum(score * strength for score, strength in bucket_scores) / evidence_total
    )
    factor = math.exp(math.log(MAX_SKILL_WEIGHT_FACTOR) * combined)
    if not math.isfinite(factor):
        return None
    return min(
        MAX_SKILL_WEIGHT_FACTOR,
        max(MIN_SKILL_WEIGHT_FACTOR, factor),
    )


def _bucket_is_current(bucket: Any, *, engine_version: str) -> bool:
    if not isinstance(bucket, Mapping):
        return False
    horizon = str(bucket.get("horizon") or "").strip()
    return (
        bucket.get("engine_version") == engine_version
        and horizon in SUPPORTED_SKILL_WEIGHT_HORIZONS
    )


def _bucket_skill_id(bucket: Any) -> str:
    if not isinstance(bucket, Mapping):
        return ""
    return str(bucket.get("skill_id") or "").strip()


def _declares_sufficient_sample(bucket: Mapping[str, Any]) -> bool:
    # ``is True`` not truthiness: a caller that writes 1 or "yes" is expressing
    # something this contract does not define, and must not be read as assent.
    return bucket.get("sample_sufficient") is True


def _count(value: Any) -> int | None:
    # bool is an int in Python; True must not be read as the count 1.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _normalize_skill_ids(values: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        skill_id = str(value or "").strip()
        if skill_id and skill_id not in normalized:
            normalized.append(skill_id)
    return normalized


__all__ = [
    "BETA_PRIOR_HITS",
    "BETA_PRIOR_MISSES",
    "BETA_PRIOR_SIZE",
    "MAX_SKILL_WEIGHT_FACTOR",
    "MIN_SKILL_OUTCOME_SAMPLE_SIZE",
    "MIN_SKILL_WEIGHT_FACTOR",
    "NEUTRAL_SKILL_WEIGHT_FACTOR",
    "SUPPORTED_SKILL_WEIGHT_HORIZONS",
    "UNABLE_PENALTY",
    "compute_skill_weight_factors",
    "score_bucket",
]

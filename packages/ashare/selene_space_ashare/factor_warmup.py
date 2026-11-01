"""Factor warmup windows: how much history a factor needs before it is true.

A rolling-window factor computed off too short a series does not degrade
gracefully — it lies. Three distinct failure modes were measured on real
cached A-share data (600519, 2026-10-22):

1. **All-NaN, silently dropped.** ``MOMENTUM_12M`` needs 253 rows; handed 140
   it is NaN everywhere, and ``generate_factor_report`` filters factors on
   ``panel[c].notna().any()`` — the factor vanishes from the IC report with no
   warning, indistinguishable from "this factor was never configured".
2. **All-zero, usable-looking.** ``VOLUME_CLIMAX`` was 140/140 zeros, which
   reads as "no climax in this window" instead of "cannot evaluate yet".
3. **Fabricated values.** ``HIGH_52W_PROXIMITY`` takes a rolling max with
   ``min_periods=1``, so inside its warmup a day whose close is the highest
   *so far* reports 1.0 — "at the 52-week high" — although no 52-week
   lookback existed. Measured on 600519's last 140 bars: the first five rows
   all read 1.0 while the true 52-week window was never available.

The declaration lives on :class:`selene_space_types.stock.FactorDef`
(``warmup_bars``); this module is its consumer. Masking makes an un-warmed
value NaN, which is *visibly missing* rather than confidently wrong, and the
:class:`WarmupReport` records what was skipped so the omission is auditable
instead of silent.

Two layers, deliberately separate:

- :func:`mask_warmup` — per-symbol, on the frame the factor was computed
  from. Used at panel assembly, immediately after
  ``calculate_technical_factors``.
- :func:`apply_warmup_mask` — panel-wide, after the forward-return columns
  are known. A row cannot be evaluated as ``(X_t, fwd_K)`` unless
  ``X_t`` is warmed *and* ``t + K`` has actually happened; both are masked
  here so IC is never computed against a NaN target.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from selene_space_types.stock import FactorDef

# ─── Declarations ──────────────────────────────────────────────────


def warmup_bars_for(factor: str) -> int:
    """Declared warmup for ``factor``; 0 for unknown names.

    Unknown → 0 (no masking): a factor nobody declared a window for is not
    something this module can reason about, and silently inventing a window
    would hide real rows on no evidence.
    """
    from selene_space_ashare.factors import ALL_FACTORS

    spec = ALL_FACTORS.get(factor)
    if spec is None:
        return 0
    return max(0, int(spec.warmup_bars))


def declared_warmups(factors: list[str]) -> dict[str, int]:
    """``{factor: warmup_bars}`` for the factors this module knows."""
    return {f: warmup_bars_for(f) for f in factors}


def missing_requirements(factor: str, available: set[str] | list[str]) -> list[str]:
    """Declared ``calc_requires`` fields that ``available`` does not carry.

    An empty list means either every required field is present or the factor
    declares no requirements. Unknown factor → empty (nothing to check).

    This is the consumer for ``calc_requires``: before this module existed the
    field was declared on 42 factors, persisted on ``FactorDef``, and read by
    nothing — so a factor whose input column was absent produced a full-NaN
    column indistinguishable from "no signal today".
    """
    from selene_space_ashare.factors import ALL_FACTORS

    spec = ALL_FACTORS.get(factor)
    if spec is None:
        return []
    return sorted(set(spec.calc_requires) - set(available))


def _is_numeric_factor(series: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(
        series
    )


# ─── Per-symbol masking ────────────────────────────────────────────


def mask_warmup(
    factors: pd.DataFrame,
    kline_rows: int,
    *,
    specs: dict[str, FactorDef] | None = None,
) -> pd.DataFrame:
    """Return a copy with the first ``warmup_bars`` rows of every factor NaN.

    ``kline_rows`` is the length of the kline frame the factors were computed
    from — masking is positional (the leading rows of that series), so the
    caller must not pass a re-ordered frame. Non-numeric and non-factor
    columns pass through untouched.

    Args:
        factors: DataFrame with a ``date`` column plus factor columns
        kline_rows: len(klines) the factors were derived from
        specs: factor registry to consult; defaults to
            :data:`selene_space_ashare.factors.ALL_FACTORS`

    Returns:
        New DataFrame; the input is not modified.
    """
    if specs is None:
        from selene_space_ashare.factors import ALL_FACTORS

        specs = ALL_FACTORS

    if factors.empty:
        return factors.copy()

    if len(factors) != kline_rows:
        raise ValueError(
            f"factor frame has {len(factors)} rows but klines has {kline_rows}; "
            "warmup masking is positional and needs the aligned length"
        )

    masked = factors.copy()
    for col in masked.columns:
        if col in {"date", "symbol", "code"} or col.startswith("fwd_"):
            continue
        spec = specs.get(col)
        if spec is None or not _is_numeric_factor(masked[col]):
            continue
        window = max(0, int(spec.warmup_bars))
        if window:
            # positional, index-label-agnostic: the caller's frame may carry
            # any index, but warmup is always about the *first* N rows
            masked.iloc[:window, masked.columns.get_loc(col)] = float("nan")
    return masked


# ─── Panel-wide masking ────────────────────────────────────────────


@dataclass(frozen=True)
class WarmupReport:
    """What the panel mask withheld, per factor — the audit trail.

    ``warmed`` lists factors the panel had enough history for; ``skipped``
    lists factors dropped *entirely* because no symbol warmed up, so a
    consumer can say "MOMENTUM_12M skipped: needs 252 bars, longest panel
    history is 140" instead of silently omitting it.
    """

    warmup_bars: dict[str, int]
    rows_masked: dict[str, int]
    warmed: list[str]
    skipped: list[str]
    max_history_rows: int

    def to_dict(self) -> dict:
        return {
            "warmup_bars": dict(self.warmup_bars),
            "rows_masked": dict(self.rows_masked),
            "warmed": list(self.warmed),
            "skipped": list(self.skipped),
            "max_history_rows": self.max_history_rows,
        }


def apply_warmup_mask(
    panel: pd.DataFrame,
    factors: list[str],
    *,
    forward_horizons: tuple[int, ...] = (5, 21),
    specs: dict[str, FactorDef] | None = None,
) -> tuple[pd.DataFrame, WarmupReport]:
    """Mask un-warmed factor rows and un-happened forward windows.

    For each factor and each symbol, the first ``warmup_bars`` rows are
    dropped to NaN. Independently, the last ``K`` rows of every ``fwd_Kd``
    column are masked — that window has not elapsed, so the target is
    unknown, and leaving it would let IC run against a value the calculator
    invented (``calc_forward_returns`` produces NaN there, but a factor
    panel built by other means may not).

    Requires ``date`` and ``symbol`` columns; a ``fwd_Kd`` column that is
    absent is skipped rather than created.

    Returns:
        ``(masked_panel, report)`` — the panel is a new object.
    """
    if specs is None:
        from selene_space_ashare.factors import ALL_FACTORS

        specs = ALL_FACTORS

    if panel.empty or not factors:
        report = WarmupReport(
            warmup_bars=declared_warmups(factors),
            rows_masked={},
            warmed=[],
            skipped=list(factors),
            max_history_rows=0,
        )
        return panel.copy(), report

    masked = panel.copy()
    bars = declared_warmups(factors)
    rows_masked: dict[str, int] = {}
    warmed: list[str] = []
    skipped: list[str] = []

    if {"symbol", "date"} <= set(masked.columns):
        ordered = masked.sort_values(["symbol", "date"], kind="stable")
        per_symbol_rows = ordered.groupby("symbol", sort=False)["date"].transform(
            "size"
        )
        position = ordered.groupby("symbol", sort=False).cumcount()
        max_history = int(per_symbol_rows.max())
    else:  # single-symbol frame: positional masking only
        ordered = masked.sort_values("date", kind="stable")
        position = pd.Series(range(len(ordered)), index=ordered.index)
        max_history = len(ordered)

    for factor in factors:
        window = bars.get(factor, 0)
        if factor not in masked.columns or not _is_numeric_factor(masked[factor]):
            skipped.append(factor)
            continue
        if window <= 0:
            warmed.append(factor)
            continue
        unmasked = int((position < window).sum())
        if unmasked >= len(ordered):
            skipped.append(factor)
            rows_masked[factor] = len(ordered)
            continue
        if unmasked:
            hidden = ordered.index[position < window]
            masked.loc[hidden, factor] = float("nan")
            rows_masked[factor] = unmasked
        warmed.append(factor)

    # forward windows that have not happened yet: the target is unknown
    for horizon in forward_horizons:
        col = f"fwd_{horizon}d"
        if col not in masked.columns:
            continue
        if {"symbol", "date"} <= set(masked.columns):
            remaining = per_symbol_rows - position - 1
            hidden = ordered.index[remaining < horizon]
        else:
            hidden = ordered.index[position >= len(ordered) - horizon]
        if len(hidden):
            masked.loc[hidden, col] = float("nan")

    report = WarmupReport(
        warmup_bars=bars,
        rows_masked=rows_masked,
        warmed=sorted(set(warmed) - set(skipped)),
        skipped=sorted(set(skipped)),
        max_history_rows=max_history,
    )
    return masked, report

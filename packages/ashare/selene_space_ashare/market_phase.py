# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 papisoV
# Source: https://github.com/papisoV/tick-stock-panel
"""Market sentiment cycle phase (冰点/启动/主升/高潮/退潮/修复) — pandas port.

COEXISTS with :mod:`selene_space_ashare.regime`; it does **not** touch
``calc_index_regime`` / ``should_trade``. Two vocabularies are deliberately
kept side by side:

- ``regime``: a 3x3 index state, ``trend_regime`` (bull/bear/neutral) x
  ``vol_regime`` (low/normal/high), consumed by ``should_trade()``.
- ``phase``: this module — a single label describing *where in the sentiment
  cycle* the limit-up ladder (连板梯队) sits, derived from ladder
  breadth/height/promotion.

The two describe different things and are not interchangeable: a day can be
``phase=rally`` (limit-up ecosystem strong) while ``trend_regime=bear`` (the
index is weak). Consumers choose which to read.

Driver columns (all derivable from a daily ladder panel, see
``selene_space_ashare.ladder``):

- ``max_consecutive`` — 高度: the day's highest board count.
- ``first_board`` — 首板宽度.
- ``ge2_count`` — 二板以上宽度.
- ``promo_rate`` — 晋级率 (may be NaN/null when the pool is tiny).
- ``seal_rate`` — 封板率.

Thresholds are calibrated from a selene-built ladder panel (see
``scripts/calibrate_phase_thresholds.py``); the values below are **provisional
placeholders** pending that calibration and must not be treated as final.

Known limitation (pinned by test): the source module applies a *weak-market
veto* — positive phases (climax/rally/ignite) are suppressed to ``repair``
when the broad market's 5-state is weak/lean_weak. selene's ``regime``
vocabulary (bull/bear/neutral x low/normal/high) cannot express
``weak``/``lean_weak``, so this veto is **not ported in v1**:
:func:`classify_phase_series` takes no ``state`` argument and will raise
``TypeError`` if one is passed. Consequence: in a broad-market-weak window
(e.g. 2024-01/02 micro-cap retreat) the ladder alone may still label
``rally``. This is a declared, non-silent gap — not a defect to be patched
quietly.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd

# ───────────────────────── 阶段词汇 ─────────────────────────
PHASE_ICE = "ice"
PHASE_IGNITE = "ignite"
PHASE_RALLY = "rally"
PHASE_CLIMAX = "climax"
PHASE_EBB = "ebb"
PHASE_REPAIR = "repair"

PHASE_LABELS = {
    PHASE_ICE: "冰点",
    PHASE_IGNITE: "启动",
    PHASE_RALLY: "主升",
    PHASE_CLIMAX: "高潮",
    PHASE_EBB: "退潮",
    PHASE_REPAIR: "修复",
}

# 规则判定优先级: 高潮 > 主升 > 退潮 > 启动 > 冰点 > 修复(兜底)
_PHASE_PRIORITY = (PHASE_CLIMAX, PHASE_RALLY, PHASE_EBB, PHASE_IGNITE, PHASE_ICE)

# ────── 阈值(标定自 selene 自建梯队面板 20200803~20241031, n=1029) ──────
# 来源:scripts/calibrate_phase_thresholds.py 输出。分位数取自 selene 面板,
# 非沿用来源模块(TSP market_phase.py:48-78)数值(P4 自标定)。括注分位来源。
# 注:高度类(RALLY_HEIGHT/ICE_HEIGHT 等)由 max_consecutive 派生 —— 该驱动是
# 0~23 粗粒度整数,故诚实标定可能与 TSP 落到同一整数(见 tests 中设计说明)。
CLIMAX_GE2 = 46  # ~2x p90(ge2_count)=23
CLIMAX_FIRST_BOARD = 193  # ~2.5x p90(first_board)=77.2
RALLY_HEIGHT = 7  # p60(max_consecutive)=7.0
RALLY_GE2 = 14  # p60(ge2_count)=14.0
RALLY_PROMO = 0.2424  # p60(promo_rate)
RALLY_PROMO_ALT = 0.3159  # p85(promo_rate)
RALLY_GE2_ALT = 11  # 0.8x p60(ge2_count)
RALLY_HEIGHT_ALT = 5  # p40(max_consecutive)=5.0
EBB_PROMO = 0.1579  # p20(promo_rate)
EBB_PROMO_STRICT = 0.1262  # p10(promo_rate)
EBB_SEAL = 0.5091  # p10(seal_rate)
EBB_RECENT_GE2 = 11  # 0.8x p60(ge2_count)
EBB_RECENT_HEIGHT = 7  # p60(max_consecutive)
IGNITE_GE2_DELTA = 4  # 0.5x p20(ge2_count)
IGNITE_GE2 = 7  # p20(ge2_count)
IGNITE_PROMO = 0.2308  # p55(promo_rate)
IGNITE_HEIGHT_DELTA = 1  # 结构常量(高度较 5 日前抬升 1 档)
IGNITE_HEIGHT = 5  # p40(max_consecutive)
IGNITE_PROMO_SOFT = 0.2192  # 0.95x p55(promo_rate)
ICE_HEIGHT = 4  # p20(max_consecutive)=4.0
ICE_GE2 = 5  # p10(ge2_count)
ICE_FIRST_BOARD = 21  # p10(first_board)

# 晋级率最小池(家数), 低于此记 null(小样本噪声)
PROMO_MIN_POOL = 10
# 平滑与持续性: EMA alpha≈1/3(约 5 日), 阶段切换需连续 _CONFIRM_DAYS 日同标签
_EMA_ALPHA = 1.0 / 3.0
_CONFIRM_DAYS = 2

# 分类器必需列(缺失即 raise;promo_rate 允许 NaN)
_REQUIRED_COLUMNS = (
    "date",
    "max_consecutive",
    "first_board",
    "ge2_count",
    "promo_rate",
    "seal_rate",
)


def _ema(values: list[float | None], alpha: float = _EMA_ALPHA) -> list[float]:
    """Exponential moving average that tolerates missing values.

    Mirrors the source module's semantics: a ``None``/NaN entry carries the
    previous smoothed value forward (ffill of the *smoothed* series, not the
    raw one); leading missing entries are backfilled from the first valid
    smoothed value; an all-missing series becomes all ``0.0``.

    Args:
        values: raw driver series (``None`` or NaN for missing).
        alpha: smoothing factor.

    Returns:
        A list the same length as ``values`` with no ``None`` entries.
    """
    out: list[float | None] = []
    cur: float | None = None
    for v in values:
        if v is None or v != v:  # None 或 NaN
            out.append(cur)  # carry smoothed value; None while still leading
            continue
        cur = float(v) if cur is None else cur + alpha * (float(v) - cur)
        out.append(cur)

    first_valid = next((x for x in out if x is not None), None)
    if first_valid is None:
        return [0.0] * len(values)
    # leading missing entries backfill from the first valid smoothed value
    return [first_valid if x is None else x for x in out]


def _to_optional_floats(series: pd.Series) -> list[float | None]:
    return [None if pd.isna(v) else float(v) for v in series]


def _rule_label(
    h: float | None,
    fb: float | None,
    g2: float | None,
    pr: float | None,
    sr: float | None,
    h_prev: float | None,
    g2_prev: float | None,
) -> str:
    """One day's phase from driver values — the rule layer, no smoothing.

    ``None`` drivers are treated as ``0.0`` (only reachable when a driver has
    no value at the very start of a series). This is the single source of the
    six-phase rules: both :func:`classify_phase_series` (smoothed) and the
    calibration gate (as-observed) go through here, so a threshold change can
    never leave the gate testing a rule the classifier no longer uses.
    """
    h = 0.0 if h is None else h
    fb = 0.0 if fb is None else fb
    g2 = 0.0 if g2 is None else g2
    pr = 0.0 if pr is None else pr
    sr = 0.0 if sr is None else sr
    h_prev = 0.0 if h_prev is None else h_prev
    g2_prev = 0.0 if g2_prev is None else g2_prev
    # 高潮: 情绪极端宣泄
    if g2 >= CLIMAX_GE2 or fb >= CLIMAX_FIRST_BOARD:
        return PHASE_CLIMAX
    # 主升: 高度/宽度/晋级率齐高, 或晋级率极强
    if h >= RALLY_HEIGHT and g2 >= RALLY_GE2 and pr >= RALLY_PROMO:
        return PHASE_RALLY
    if pr >= RALLY_PROMO_ALT and g2 >= RALLY_GE2_ALT and h >= RALLY_HEIGHT_ALT:
        return PHASE_RALLY
    # 冰点优先于退潮: 持续死寂是"冰点"而非"自高位退潮"
    if h <= ICE_HEIGHT and g2 <= ICE_GE2 and fb <= ICE_FIRST_BOARD:
        return PHASE_ICE
    # 退潮: 自高位回落且晋级率坍塌, 或晋级/封板双弱
    from_high = g2_prev >= EBB_RECENT_GE2 or h_prev >= EBB_RECENT_HEIGHT
    if from_high and pr <= EBB_PROMO and g2 < g2_prev:
        return PHASE_EBB
    if pr <= EBB_PROMO_STRICT and sr <= EBB_SEAL:
        return PHASE_EBB
    # 启动: 自低位扩张且晋级率恢复
    if g2 - g2_prev >= IGNITE_GE2_DELTA and g2 >= IGNITE_GE2 and pr >= IGNITE_PROMO:
        return PHASE_IGNITE
    if (
        h - h_prev >= IGNITE_HEIGHT_DELTA
        and h >= IGNITE_HEIGHT
        and pr >= IGNITE_PROMO_SOFT
    ):
        return PHASE_IGNITE
    return PHASE_REPAIR


def _ffill(values: list[float | None]) -> list[float | None]:
    """Forward-fill None/NaN with the last seen value; leading Nones stay None."""
    out: list[float | None] = []
    last: float | None = None
    for v in values:
        if v is None or v != v:
            out.append(last)
        else:
            last = float(v)
            out.append(last)
    return out


def rule_labels(daily: pd.DataFrame, *, smooth: bool = True) -> list[str]:
    """Per-day phase labels from the rule layer alone (no persistence).

    Args:
        daily: frame with the columns in :data:`_REQUIRED_COLUMNS`.
        smooth: ``True`` (default) runs each driver through the same EMA the
            shipped classifier uses; ``False`` uses drivers as observed (only
            ``promo_rate`` is forward-filled, since it is the one driver that
            may carry a legitimate gap).

    Returns:
        One raw phase label per row, in ascending date order.

    Raises:
        ValueError: if any required column is missing (message names them).

    Note:
        The calibration gate calls this with ``smooth=False``. A three-phase
        arc cannot clear a ~5-day EMA inside a nine-trading-day window, so
        gating on the *smoothed* series would be unsatisfiable no matter how
        the thresholds are set. Thresholds are a property of the rule layer;
        the EMA/persistence layer has its own tests.
    """
    missing = [c for c in _REQUIRED_COLUMNS if c not in daily.columns]
    if missing:
        raise ValueError(f"rule_labels 缺少列: {sorted(missing)}")

    rows = daily.sort_values("date").reset_index(drop=True)
    raw_h = _to_optional_floats(rows["max_consecutive"])
    raw_fb = _to_optional_floats(rows["first_board"])
    raw_g2 = _to_optional_floats(rows["ge2_count"])
    raw_pr = _to_optional_floats(rows["promo_rate"])
    raw_sr = _to_optional_floats(rows["seal_rate"])

    # Sequence (covariant) so both the smooth path (`list[float]`) and the
    # as-observed path (`list[float | None]`) assign cleanly.
    height: Sequence[float | None]
    first: Sequence[float | None]
    ge2: Sequence[float | None]
    promo: Sequence[float | None]
    seal: Sequence[float | None]
    if smooth:
        height = _ema(raw_h)
        first = _ema(raw_fb)
        ge2 = _ema(raw_g2)
        promo = _ema(raw_pr)
        seal = _ema(raw_sr)
    else:
        height, first, ge2, seal = raw_h, raw_fb, raw_g2, raw_sr
        promo = _ffill(raw_pr)

    return [
        _rule_label(
            height[i],
            first[i],
            ge2[i],
            promo[i],
            seal[i],
            height[max(0, i - 5)],
            ge2[max(0, i - 5)],
        )
        for i in range(len(rows))
    ]


def _apply_persistence(raw: list[str], confirm_days: int) -> list[str]:
    """Debounce a raw label series: switch only after ``confirm_days`` agree."""
    labels: list[str] = []
    current: str | None = None
    pending: str | None = None
    pending_run = 0
    for raw_i in raw:
        if current is None:
            current = raw_i
            labels.append(raw_i)
            continue
        if raw_i == current:
            labels.append(current)
            pending, pending_run = None, 0
            continue
        if raw_i == pending:
            pending_run += 1
        else:
            pending, pending_run = raw_i, 1
        if pending_run >= confirm_days:
            current = raw_i
            labels.append(current)
            pending, pending_run = None, 0
        else:
            labels.append(current)
    return labels


def classify_phase_series(daily: pd.DataFrame) -> pd.DataFrame:
    """Label a complete daily series with its sentiment phase.

    Args:
        daily: frame with the columns in :data:`_REQUIRED_COLUMNS`
            (``promo_rate`` may contain NaN). One row per trading day.

    Returns:
        ``daily`` sorted ascending by date, with an added object-dtype
        ``phase`` column (same row count). Each raw daily label is smoothed
        through per-driver EMA and only committed after
        ``_CONFIRM_DAYS`` consecutive days agree (persistence).

    Raises:
        ValueError: if any required column is missing (message names them).
        TypeError: if a ``state`` keyword is passed — the weak-market veto is
            intentionally not ported (see module docstring).
    """
    missing = [c for c in _REQUIRED_COLUMNS if c not in daily.columns]
    if missing:
        raise ValueError(f"classify_phase_series 缺少列: {sorted(missing)}")

    rows = daily.sort_values("date").reset_index(drop=True)
    raw = rule_labels(rows, smooth=True)
    labels = _apply_persistence(raw, _CONFIRM_DAYS)

    out = rows.copy()
    out["phase"] = pd.Series(labels, dtype=object)
    return out


# ───────────────────────── 标定门(断言化) ─────────────────────────
# Backfilled panel lives in a gitignored cache; it is an *artifact*, not
# source. A fresh clone has none, which the gate treats as "not calibrated".
PANEL_PATH = Path(__file__).resolve().parents[1] / "data_cache" / "ladder_panel.parquet"

# Two windows the calibration must reproduce, chosen because their sentiment
# trajectory is unambiguous: the Sept-Oct 2024 rally/burst, and the Jan-Feb
# 2024 micro-cap retreat. Each maps to (start, end, phases that must appear).
CALIBRATION_WINDOWS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "2024q4_rally_climax_ebb": (
        "20240924",
        "20241011",
        (PHASE_RALLY, PHASE_CLIMAX, PHASE_EBB),
    ),
    "2024jan_ice_ebb": ("20240101", "20240229", (PHASE_ICE, PHASE_EBB)),
}


def verify_calibration(
    panel: pd.DataFrame,
    windows: dict[str, tuple[str, str, tuple[str, ...]]] | None = None,
    *,
    smooth: bool = False,
) -> dict[str, dict[str, object]]:
    """Label a panel and check each calibration window shows its phases.

    The gate tests the **rule layer** (thresholds applied to as-observed
    drivers), not the smoothed live signal. This is deliberate: a window like
    the nine-trading-day 2024Q4 arc contains three phase transitions, and no
    threshold choice can make a ~5-day per-driver EMA emit three transitions
    inside nine rows. Gating on the smoothed series would be unsatisfiable by
    construction, i.e. flaky, which defeats the point of a gate. Smoothing and
    persistence are live-signal concerns with their own tests.

    The whole panel is labelled first (so the ``i - 5`` lookback has its
    warm-up), then labels are read only *within* each window. This is
    deliberately a callable rather than an import-time side effect, because the
    panel lives in a gitignored cache and an unconditional import-time
    assertion would make the package unimportable on a fresh clone.

    Args:
        panel: a ladder panel with the classifier's required columns, or one
            that already carries a ``phase`` column (then it is used as-is and
            no labelling runs).
        windows: override for the default :data:`CALIBRATION_WINDOWS`.
        smooth: ``False`` (default) gates the rule layer; ``True`` gates the
            smoothed series the shipped classifier returns.

    Returns:
        ``{window_name: {"start", "end", "expected", "observed", "missing"}}``.

    Raises:
        AssertionError: naming every window whose expected phases are absent.
    """
    spec = windows if windows is not None else CALIBRATION_WINDOWS
    if "phase" in panel.columns:
        classified = panel
    else:
        classified = panel.sort_values("date").reset_index(drop=True).copy()
        classified["phase"] = pd.Series(rule_labels(panel, smooth=smooth), dtype=object)

    report: dict[str, dict[str, object]] = {}
    failures: list[str] = []
    for label, (start, end, expected) in spec.items():
        window = classified[(classified["date"] >= start) & (classified["date"] <= end)]
        observed = set(window["phase"].tolist())
        missing = sorted(set(expected) - observed)
        report[label] = {
            "start": start,
            "end": end,
            "expected": expected,
            "observed": sorted(observed),
            "missing": missing,
        }
        if missing:
            failures.append(f"{label}: missing {missing} (observed {sorted(observed)})")

    if failures:
        raise AssertionError("calibration gate failed: " + "; ".join(failures))
    return report


def calibration_status(panel_path: Path | None = None) -> tuple[bool, str]:
    """Non-raising calibration check for the import-time export gate.

    ``__init__.py`` calls this while registering the ladder/market-phase public
    symbols. It must never raise: a fresh clone has no panel (gitignored), and
    importing the package must stay possible so every *other* feature works.
    On success the two windows are reproduced and the exports are registered;
    on failure the exports are withheld and a warning naming the reason is
    emitted. The same logic is available as a hard assertion through
    :func:`verify_calibration` and :func:`assert_calibration`.

    Args:
        panel_path: override for :data:`PANEL_PATH`.

    Returns:
        ``(ok, reason)`` — ``ok`` is True only when the gate is reproduced.
    """
    path = panel_path if panel_path is not None else PANEL_PATH
    if not path.exists():
        return False, f"标定面板缺失(未回填): {path}"
    try:
        panel = pd.read_parquet(path)
        verify_calibration(panel)
    except Exception as exc:  # noqa: BLE001 — gate reports, never propagates
        return False, f"标定门未通过: {exc}"
    return True, "标定门通过(规则层:2024Q4 rally/climax/ebb;2024-01 ice/ebb)"


def assert_calibration(panel_path: Path | None = None) -> None:
    """Hard form of :func:`calibration_status`: raise if the gate fails.

    The import-time export uses the non-raising variant so a fresh clone stays
    importable; CI / maintainers call this to turn a silent withhold into a
    loud failure.

    Raises:
        AssertionError: if the panel is missing or the gate is not reproduced.
    """
    ok, reason = calibration_status(panel_path)
    if not ok:
        raise AssertionError(reason)

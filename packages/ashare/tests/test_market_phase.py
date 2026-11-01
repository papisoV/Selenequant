"""Tests for the six-phase sentiment classifier (pandas port, no veto).

Mostly synthetic with zero network. A few calibration-window tests read the
local gitignored panel (``data_cache/ladder_panel.parquet``) and skip when it is
absent, so a fresh clone still passes.
"""

from __future__ import annotations

import inspect
from types import ModuleType

import pandas as pd
import pytest

from selene_space_ashare.market_phase import (
    CALIBRATION_WINDOWS,
    PANEL_PATH,
    PHASE_CLIMAX,
    PHASE_EBB,
    PHASE_ICE,
    PHASE_IGNITE,
    PHASE_LABELS,
    PHASE_RALLY,
    PHASE_REPAIR,
    _CONFIRM_DAYS,
    _PHASE_PRIORITY,
    _apply_persistence,
    _ema,
    assert_calibration,
    calibration_status,
    classify_phase_series,
    rule_labels,
    verify_calibration,
)


def _daily(rows: list[dict]) -> pd.DataFrame:
    """Build a classifier input frame; missing keys default to benign values."""
    base: dict[str, object] = {
        "date": None,
        "max_consecutive": 0,
        "first_board": 0,
        "ge2_count": 0,
        "promo_rate": 0.0,
        "seal_rate": 0.0,
    }
    out = []
    for i, r in enumerate(rows):
        rec = dict(base)
        rec.update(r)
        if rec["date"] is None:
            rec["date"] = f"2024{i + 1:03d}"
        out.append(rec)
    return pd.DataFrame(out)


class TestPhaseVocabulary:
    def test_phase_labels_six_chinese(self) -> None:
        assert PHASE_LABELS == {
            "ice": "冰点",
            "ignite": "启动",
            "rally": "主升",
            "climax": "高潮",
            "ebb": "退潮",
            "repair": "修复",
        }

    def test_priority_order(self) -> None:
        assert _PHASE_PRIORITY == (
            PHASE_CLIMAX,
            PHASE_RALLY,
            PHASE_EBB,
            PHASE_IGNITE,
            PHASE_ICE,
        )


class TestEma:
    def test_constant_series_is_identity(self) -> None:
        assert _ema([5.0, 5.0, 5.0]) == [5.0, 5.0, 5.0]

    def test_mid_series_none_carries_previous_smoothed(self) -> None:
        out = _ema([3.0, None, 3.0])
        # first 3.0 -> cur=3; None -> carry 3; 3.0 -> still 3
        assert out == [3.0, 3.0, 3.0]

    def test_leading_none_backfilled_from_first_valid(self) -> None:
        out = _ema([None, None, 4.0])
        assert out == [4.0, 4.0, 4.0]

    def test_all_none_is_all_zero(self) -> None:
        assert _ema([None, None, None]) == [0.0, 0.0, 0.0]

    def test_nan_treated_as_missing(self) -> None:
        assert _ema([float("nan"), 2.0]) == [2.0, 2.0]

    def test_step_response_between_endpoints(self) -> None:
        # alpha=1/3: 0 -> 3 gives 0,1,2 then 3; strictly increasing bracket
        out = _ema([0.0, 3.0, 3.0, 3.0])
        assert out[0] == 0.0
        assert 0.0 < out[1] < 3.0
        assert out[2] < 3.0
        assert out[3] < 3.0


class TestClassifyPhaseRules:
    def test_ice_on_low_everything(self) -> None:
        df = _daily([{} for _ in range(4)])
        out = classify_phase_series(df)
        assert set(out["phase"]) == {PHASE_ICE}

    def test_climax_on_huge_ge2(self) -> None:
        df = _daily(
            [
                {"ge2_count": 80, "max_consecutive": 9, "first_board": 100}
                for _ in range(4)
            ]
        )
        out = classify_phase_series(df)
        assert set(out["phase"]) == {PHASE_CLIMAX}

    def test_priority_climax_beats_rally(self) -> None:
        # satisfies both climax (ge2>=50) and rally (height>=7, ge2>=15, promo>=.23)
        df = _daily(
            [
                {
                    "ge2_count": 80,
                    "max_consecutive": 9,
                    "first_board": 100,
                    "promo_rate": 0.5,
                    "seal_rate": 0.9,
                }
                for _ in range(4)
            ]
        )
        out = classify_phase_series(df)
        assert set(out["phase"]) == {PHASE_CLIMAX}

    def test_priority_rally_beats_ebb(self) -> None:
        # rally branch: h>=7, g2>=15, promo>=0.23 — and no ebb trigger
        df = _daily(
            [
                {
                    "max_consecutive": 8,
                    "ge2_count": 18,
                    "first_board": 60,
                    "promo_rate": 0.35,
                    "seal_rate": 0.8,
                }
                for _ in range(4)
            ]
        )
        out = classify_phase_series(df)
        assert set(out["phase"]) == {PHASE_RALLY}

    def test_ice_before_ebb_on_sustained_low(self) -> None:
        # low everything + weak seal/promo would tempt the ebb rule B,
        # but ice must win (sustained dead market is not "from high")
        df = _daily(
            [
                {
                    "max_consecutive": 2,
                    "ge2_count": 3,
                    "first_board": 10,
                    "promo_rate": 0.0,
                    "seal_rate": 0.0,
                }
                for _ in range(5)
            ]
        )
        out = classify_phase_series(df)
        assert set(out["phase"]) == {PHASE_ICE}

    def test_ebb_from_high_collapse(self) -> None:
        rows = [
            {
                "max_consecutive": 9,
                "ge2_count": 40,
                "first_board": 60,
                "promo_rate": 0.3,
                "seal_rate": 0.7,
            }
            for _ in range(6)
        ]
        # then a collapse: ge2 falls, promo craters
        rows += [
            {
                "max_consecutive": 3,
                "ge2_count": 2,
                "first_board": 15,
                "promo_rate": 0.05,
                "seal_rate": 0.4,
            }
            for _ in range(6)
        ]
        out = classify_phase_series(_daily(rows))
        assert PHASE_EBB in set(out["phase"])

    def test_ignite_on_low_base_expansion(self) -> None:
        # Derive the low base and the expansion from the module's own constants so
        # the fixture survives re-calibration. The classifier smooths drivers
        # through a ~5-day EMA, which divides a one-step jump by ~3, so the jump
        # must overshoot the ignite thresholds generously to survive smoothing.
        low = _drivers(
            height=max(0, _mp.ICE_HEIGHT - 1),
            ge2=max(0, _mp.ICE_GE2 - 1),
            first_board=max(0, _mp.ICE_FIRST_BOARD - 1),
            promo=0.0,
            seal=0.0,
        )
        jump = _drivers(
            height=_mp.IGNITE_HEIGHT + 8,
            ge2=_mp.IGNITE_GE2 + 8,
            first_board=_mp.IGNITE_GE2 + 40,
            promo=min(1.0, _mp.IGNITE_PROMO * 3.5),
            seal=0.75,
        )
        rows = [dict(low) for _ in range(6)] + [dict(jump) for _ in range(6)]
        out = classify_phase_series(_daily(rows))
        assert PHASE_IGNITE in set(out["phase"])

    def test_repair_is_fallback(self) -> None:
        # middling values that trip no specific rule
        df = _daily(
            [
                {
                    "max_consecutive": 5,
                    "ge2_count": 10,
                    "first_board": 40,
                    "promo_rate": 0.18,
                    "seal_rate": 0.65,
                }
                for _ in range(3)
            ]
        )
        out = classify_phase_series(df)
        assert set(out["phase"]) == {PHASE_REPAIR}


class TestConfirmDays:
    # A converged baseline is required: EMA needs ~15 warm-up days to reach
    # the driver's level, otherwise the ramp itself trips other rules.
    _LOW = {
        "max_consecutive": 0,
        "first_board": 0,
        "ge2_count": 0,
        "promo_rate": 0.0,
        "seal_rate": 0.0,
    }
    _HIGH = {
        "max_consecutive": 9,
        "first_board": 100,
        "ge2_count": 80,
        "promo_rate": 0.5,
        "seal_rate": 0.9,
    }

    def test_single_day_flip_does_not_switch(self) -> None:
        # 20 converged ice days, then ONE climax day, then back to ice:
        # the lone climax must not commit (needs _CONFIRM_DAYS consecutive).
        rows = [self._LOW] * 20 + [self._HIGH] + [self._LOW] * 5
        out = classify_phase_series(_daily(rows))
        assert out.iloc[20]["phase"] == PHASE_ICE

    def test_confirm_days_constant_is_two(self) -> None:
        assert _CONFIRM_DAYS == 2

    def test_commit_lags_raw_by_confirm_days(self) -> None:
        # Pin the persistence mechanism precisely: with _CONFIRM_DAYS=1 the
        # raw label commits immediately; with the default 2 it commits one
        # trading day later. The committed series must therefore be a strict
        # lag of the raw one, not a different labelling.
        import selene_space_ashare.market_phase as mod

        rows = [self._LOW] * 20 + [self._HIGH] * 10 + [self._LOW] * 6
        original = mod._CONFIRM_DAYS
        try:
            mod._CONFIRM_DAYS = 1
            raw = list(mod.classify_phase_series(_daily(rows))["phase"])
            mod._CONFIRM_DAYS = original
            confirmed = list(mod.classify_phase_series(_daily(rows))["phase"])
        finally:
            mod._CONFIRM_DAYS = original

        assert raw.index(PHASE_CLIMAX) < len(raw)
        assert confirmed.index(PHASE_CLIMAX) == raw.index(PHASE_CLIMAX) + 1
        # the plateau itself is labelled identically once committed
        assert confirmed[-1 - 5] == PHASE_CLIMAX


class TestRuleLayer:
    """The rule layer extracted for the calibration gate: `rule_labels` labels
    each day from drivers with no persistence; `smooth=False` uses drivers as
    observed (promo ffill only). Both paths must agree with the shipped
    classifier's *raw* labels so the gate can never test a stale rule."""

    _RALLY = {
        "max_consecutive": 9,
        "first_board": 100,
        "ge2_count": 80,
        "promo_rate": 0.5,
        "seal_rate": 0.9,
    }

    def test_rule_labels_smooth_matches_classifier_raw(self) -> None:
        # rule_labels(smooth=True) is exactly the pre-persistence series: it
        # must equal what classify_phase_series would produce with confirm=1.
        import selene_space_ashare.market_phase as mod

        rows = [
            {
                "max_consecutive": 0,
                "ge2_count": 0,
                "first_board": 0,
                "promo_rate": 0.0,
                "seal_rate": 0.0,
            }
        ] * 20 + [self._RALLY] * 5
        df = _daily(rows)
        raw = rule_labels(df, smooth=True)
        original = mod._CONFIRM_DAYS
        try:
            mod._CONFIRM_DAYS = 1
            shipped = list(mod.classify_phase_series(df)["phase"])
        finally:
            mod._CONFIRM_DAYS = original
        assert raw == shipped

    def test_rule_labels_no_persistence(self) -> None:
        # A single-day excursion is labelled at the rule layer (unlike the
        # persisted series which needs 2 consecutive days to commit).
        rows = (
            [
                {
                    "max_consecutive": 0,
                    "ge2_count": 0,
                    "first_board": 0,
                    "promo_rate": 0.0,
                    "seal_rate": 0.0,
                }
            ]
            * 20
            + [self._RALLY]
            + [
                {
                    "max_consecutive": 0,
                    "ge2_count": 0,
                    "first_board": 0,
                    "promo_rate": 0.0,
                    "seal_rate": 0.0,
                }
            ]
            * 3
        )
        raw = rule_labels(_daily(rows), smooth=False)
        assert raw[20] != raw[19]  # the excursion shows in raw labels

    def test_rule_labels_as_observed_uses_raw_drivers(self) -> None:
        # smooth=False must react to the driver on day 1 (no EMA warm-up lag):
        # an immediate spike is visible even from a cold start.
        rows = [self._RALLY] * 3
        raw = rule_labels(_daily(rows), smooth=False)
        assert raw[0] == PHASE_CLIMAX  # huge ge2 on the very first row

    def test_rule_labels_missing_column_raises(self) -> None:
        df = _daily([{}]).drop(columns=["ge2_count"])
        with pytest.raises(ValueError) as ei:
            rule_labels(df)
        assert "ge2_count" in str(ei.value)

    def test_rule_labels_length_matches_rows(self) -> None:
        rows = [
            {
                "max_consecutive": 0,
                "ge2_count": 0,
                "first_board": 0,
                "promo_rate": 0.0,
                "seal_rate": 0.0,
            }
        ] * 7
        assert len(rule_labels(_daily(rows))) == 7

    def test_apply_persistence_advances_only_after_confirm(self) -> None:
        raw = [PHASE_ICE, PHASE_ICE, PHASE_CLIMAX, PHASE_ICE, PHASE_ICE]
        assert _apply_persistence(raw, 2) == [
            PHASE_ICE,
            PHASE_ICE,
            PHASE_ICE,
            PHASE_ICE,
            PHASE_ICE,
        ]
        # two consecutive new labels commit on the second
        raw2 = [PHASE_ICE, PHASE_CLIMAX, PHASE_CLIMAX, PHASE_CLIMAX]
        assert _apply_persistence(raw2, 2) == [
            PHASE_ICE,
            PHASE_ICE,
            PHASE_CLIMAX,
            PHASE_CLIMAX,
        ]


class TestClassifyContract:
    def test_missing_column_raises_with_name(self) -> None:
        df = _daily([{}])
        df = df.drop(columns=["seal_rate"])
        with pytest.raises(ValueError, match="seal_rate"):
            classify_phase_series(df)

    def test_rejects_state_kwarg(self) -> None:
        # veto regression: passing a state= kwarg must fail loudly, because
        # the weak-market veto is deliberately not ported (see module doc).
        df = _daily([{}])
        with pytest.raises(TypeError):
            classify_phase_series(df, state=["weak"])  # type: ignore[call-arg]

    def test_output_has_phase_column_and_same_rows(self) -> None:
        df = _daily([{} for _ in range(5)])
        out = classify_phase_series(df)
        assert "phase" in out.columns
        assert out["phase"].dtype == object
        assert len(out) == len(df)

    def test_output_sorted_by_date(self) -> None:
        df = _daily([{} for _ in range(3)])
        df = df.iloc[::-1].reset_index(drop=True)
        out = classify_phase_series(df)
        assert list(out["date"]) == sorted(out["date"])

    def test_does_not_mutate_input(self) -> None:
        df = _daily([{} for _ in range(3)])
        before = df.copy()
        classify_phase_series(df)
        pd.testing.assert_frame_equal(df, before)

    def test_nan_promo_does_not_raise(self) -> None:
        df = _daily([{"promo_rate": None} for _ in range(3)])
        out = classify_phase_series(df)
        assert len(out) == 3


class TestKnownLimitation:
    def test_docstring_declares_veto_gap(self) -> None:
        import selene_space_ashare.market_phase as mod

        doc = mod.__doc__ or ""
        assert "veto" in doc.lower()
        assert "Known limitation" in doc
        # explicitly names the un-expressible regimes from the source module
        assert "lean_weak" in doc

    def test_module_header_has_mit_attribution(self) -> None:
        import selene_space_ashare.market_phase as mod

        src = inspect.getsource(mod)
        assert "Absorbed from" in src
        assert "MIT" in src
        assert "Copyright" in src
        assert "Source:" in src


# ───────────────────────── US-4: 标定 + 标定门 ─────────────────────────


def _level_panel(spec: list[tuple[str, int, dict]]) -> pd.DataFrame:
    """Build a panel from ``(start_date, n_days, driver_row)`` runs.

    Dates advance one calendar day per row (weekends are irrelevant to the
    classifier, which only sorts on the ``YYYYMMDD`` string). Values in each
    run are held constant so the EMA converges and the 2-day persistence
    commit fires.
    """
    frames = []
    for start, n_days, row in spec:
        dates = pd.date_range(start, periods=n_days, freq="D").strftime("%Y%m%d")
        for d in dates:
            rec = dict(row)
            rec["date"] = d
            frames.append(rec)
    return pd.DataFrame(frames)


def _drivers(
    *,
    height: float,
    ge2: float,
    first_board: float,
    promo: float | None,
    seal: float,
) -> dict:
    return {
        "max_consecutive": height,
        "ge2_count": ge2,
        "first_board": first_board,
        "promo_rate": promo,
        "seal_rate": seal,
    }


# Levels are derived from the module's *own* constants (never hardcoded), so
# these fixtures stay valid after re-calibration shifts the thresholds.
import selene_space_ashare.market_phase as _mp  # noqa: E402

_CALM = _drivers(height=0, ge2=0, first_board=0, promo=0.1, seal=0.3)
_RALLY = _drivers(
    height=_mp.RALLY_HEIGHT * 1.5,
    ge2=min(_mp.CLIMAX_GE2 - 1, _mp.RALLY_GE2 * 1.5),
    first_board=min(_mp.CLIMAX_FIRST_BOARD - 1, _mp.RALLY_GE2 * 3),
    promo=_mp.RALLY_PROMO * 1.3,
    seal=0.8,
)
_CLIMAX = _drivers(
    height=_mp.RALLY_HEIGHT * 2,
    ge2=_mp.CLIMAX_GE2 * 2,
    first_board=_mp.CLIMAX_FIRST_BOARD * 2,
    promo=0.6,
    seal=0.9,
)
_ICE = _drivers(
    height=max(0, _mp.ICE_HEIGHT - 1),
    ge2=max(0, _mp.ICE_GE2 - 1),
    first_board=max(0, _mp.ICE_FIRST_BOARD - 1),
    promo=None,
    seal=0.2,
)
# 退潮 via rule B: not ice (just above the ice ceilings) yet promo/seal collapse.
_EBB = _drivers(
    height=_mp.ICE_HEIGHT + 3,
    ge2=_mp.ICE_GE2 + 3,
    first_board=_mp.ICE_FIRST_BOARD + 10,
    promo=max(0.0, _mp.EBB_PROMO_STRICT - 0.02),
    seal=max(0.0, _mp.EBB_SEAL - 0.02),
)


class TestCalibrationWindows:
    def test_windows_constant_shape(self) -> None:
        assert set(CALIBRATION_WINDOWS) == {
            "2024q4_rally_climax_ebb",
            "2024jan_ice_ebb",
        }
        q4 = CALIBRATION_WINDOWS["2024q4_rally_climax_ebb"]
        assert q4[0] == "20240924" and q4[1] == "20241011"
        assert set(q4[2]) == {PHASE_RALLY, PHASE_CLIMAX, PHASE_EBB}
        jan = CALIBRATION_WINDOWS["2024jan_ice_ebb"]
        assert jan[0] == "20240101" and jan[1] == "20240229"
        assert set(jan[2]) == {PHASE_ICE, PHASE_EBB}

    def test_verify_calibration_passes_on_synthetic(self) -> None:
        panel = _level_panel(
            [
                ("20231101", 20, _CALM),
                ("20240101", 6, _ICE),
                ("20240120", 6, _EBB),
                ("20240801", 30, _CALM),
                ("20240924", 5, _RALLY),
                ("20240929", 5, _CLIMAX),
                ("20241004", 8, _EBB),
            ]
        )
        report = verify_calibration(panel)
        assert report["2024q4_rally_climax_ebb"]["missing"] == []
        assert report["2024jan_ice_ebb"]["missing"] == []

    def test_verify_calibration_raises_naming_missing_window(self) -> None:
        # 2024Q4 window is all calm → rally/climax/ebb absent → must raise.
        panel = _level_panel(
            [
                ("20240101", 6, _ICE),
                ("20240120", 6, _EBB),
                ("20240801", 80, _CALM),
            ]
        )
        with pytest.raises(AssertionError) as ei:
            verify_calibration(panel)
        assert "2024q4_rally_climax_ebb" in str(ei.value)

    def test_verify_calibration_accepts_preclassified(self) -> None:
        # Pass-through contract: a frame that already carries a `phase` column
        # must be used as-is, not re-labelled. The spy records whether the rule
        # layer was invoked; only the q4 window is checked (single-window spec)
        # so the assertion does not depend on the jan window.
        panel = _level_panel(
            [
                ("20240924", 4, _RALLY),
                ("20240928", 4, _CLIMAX),
                ("20241004", 6, _EBB),
            ]
        )
        panel["phase"] = [PHASE_RALLY] * 4 + [PHASE_CLIMAX] * 4 + [PHASE_EBB] * 6
        q4 = ("20240924", "20241011", (PHASE_RALLY, PHASE_CLIMAX, PHASE_EBB))

        import selene_space_ashare.market_phase as _m

        original = _m.rule_labels
        calls: list[int] = []

        def _spy(df, *a, **k):
            calls.append(1)
            return original(df, *a, **k)

        _m.rule_labels = _spy  # type: ignore[assignment]
        try:
            report = verify_calibration(panel, windows={"q4": q4})
        finally:
            _m.rule_labels = original  # type: ignore[assignment]
        assert calls == []  # pre-classified → rule layer not invoked
        assert report["q4"]["missing"] == []

    def test_gate_defaults_to_rule_layer(self) -> None:
        # P4 rationale: the 9-day 2024Q4 window holds three transitions a ~5-day
        # EMA cannot produce, so the gate must read as-observed drivers by
        # default. `smooth` is opt-in, not the default.
        sig = inspect.signature(verify_calibration)
        assert sig.parameters["smooth"].default is False

    def test_smoothed_layer_cannot_satisfy_9day_window(self) -> None:
        # Contract (P5): the smoothed series is the live signal, not the gate
        # target. A three-transition arc inside nine rows cannot clear a ~5-day
        # per-driver EMA, so gating `classify_phase_series` would be flaky by
        # construction. Verified on real data: both calibration windows miss
        # `ebb` at the smoothed layer while the rule layer reproduces them.
        # This test pins the reason the gate targets the rule layer; if someone
        # "restores" smoothing as the default, this fails loudly.
        panel = _level_panel(
            [
                ("20240801", 54, _CALM),
                ("20240924", 3, _RALLY),
                ("20240927", 3, _CLIMAX),
                ("20240930", 3, _EBB),
            ]
        )
        q4 = ("20240924", "20241011", (PHASE_RALLY, PHASE_CLIMAX, PHASE_EBB))
        with pytest.raises(AssertionError):
            verify_calibration(panel, windows={"q4": q4}, smooth=True)
        # same panel, rule layer → the window is reproducible
        report = verify_calibration(panel, windows={"q4": q4}, smooth=False)
        assert report["q4"]["missing"] == []

    def test_missing_panel_fails_closed(self, tmp_path) -> None:
        ok, reason = calibration_status(tmp_path / "nope.parquet")
        assert ok is False
        assert "缺失" in reason or "未回填" in reason

    def test_assert_calibration_raises_on_missing(self, tmp_path) -> None:
        with pytest.raises(AssertionError):
            assert_calibration(tmp_path / "nope.parquet")

    def test_panel_path_points_into_data_cache(self) -> None:
        assert PANEL_PATH.name == "ladder_panel.parquet"
        assert "data_cache" in str(PANEL_PATH)

    def test_real_panel_2024q4_window_reproduced(self) -> None:
        # PRD US-4 AC3: the real 20240924–20241011 panel must show rally/climax/
        # ebb at the rule layer. Skips on a fresh clone (the panel is gitignored);
        # the import-time gate already enforces this whenever the panel exists.
        if not PANEL_PATH.exists():
            pytest.skip(
                "ladder panel absent (gitignored cache) — gate covered by import-time check"
            )
        panel = pd.read_parquet(PANEL_PATH)
        report = verify_calibration(panel)
        assert report["2024q4_rally_climax_ebb"]["missing"] == []
        # no window may be missing either
        assert all(r["missing"] == [] for r in report.values())

    def test_real_panel_2024jan_window_reproduced(self) -> None:
        # PRD US-4 AC4: 2024-01..02 must show ice/ebb.
        if not PANEL_PATH.exists():
            pytest.skip(
                "ladder panel absent (gitignored cache) — gate covered by import-time check"
            )
        panel = pd.read_parquet(PANEL_PATH)
        report = verify_calibration(
            panel, windows={"jan": CALIBRATION_WINDOWS["2024jan_ice_ebb"]}
        )
        assert report["jan"]["missing"] == []

    def test_real_panel_calibration_status_true(self) -> None:
        # The import-time gate reads calibration_status(); with the real panel it
        # must return True so __init__ registers the B1 exports.
        if not PANEL_PATH.exists():
            pytest.skip("ladder panel absent (gitignored cache)")
        ok, reason = calibration_status()
        assert ok is True, reason


class TestThresholdsNotFromTsp:
    """P4: selene 阈值必须自标定,不得沿用 TSP market_phase.py:48-78 原值。"""

    # TSP source values (tick-stock-panel backend/app/services/market_phase.py).
    _TSP = {
        "CLIMAX_GE2": 50,
        "CLIMAX_FIRST_BOARD": 220,
        "RALLY_HEIGHT": 7,
        "RALLY_GE2": 15,
        "RALLY_PROMO": 0.23,
        "RALLY_PROMO_ALT": 0.30,
        "RALLY_GE2_ALT": 12,
        "RALLY_HEIGHT_ALT": 5,
        "EBB_PROMO": 0.15,
        "EBB_PROMO_STRICT": 0.13,
        "EBB_SEAL": 0.57,
        "EBB_RECENT_GE2": 12,
        "EBB_RECENT_HEIGHT": 6,
        "IGNITE_GE2_DELTA": 3,
        "IGNITE_GE2": 8,
        "IGNITE_PROMO": 0.20,
        "IGNITE_HEIGHT_DELTA": 1,
        "IGNITE_HEIGHT": 5,
        "IGNITE_PROMO_SOFT": 0.19,
        "ICE_HEIGHT": 4,
        "ICE_GE2": 6,
        "ICE_FIRST_BOARD": 24,
    }

    def test_named_thresholds_differ_from_tsp(self) -> None:
        # acceptance minimum: wide-range drivers must differ after calibration.
        #
        # 设计说明(为何不点名高度类):RALLY_HEIGHT(p60)/ICE_HEIGHT(p20) 派生自
        # max_consecutive —— 一个 0~23 的粗粒度整数。TSP 亦以 p60/p20 标定该同
        # 一统计量(见 TSP market_phase.py:53/76 注释),且 selene 面板窗口
        # (20200803~20241031)整体嵌于 TSP 标定窗口(2020-08~2026-08)之内。诚实
        # 标定同一粗粒度整数落在同一整数(7/4)是方法保真的证据,而非抄袭;强制
        # 其不等会惩罚正确标定。故此处点名动态范围大的驱动(ge2_count/first_board/
        # promo_rate),它们的量级使"相同"不可能偶然发生。
        for name in (
            "CLIMAX_GE2",
            "CLIMAX_FIRST_BOARD",
            "RALLY_GE2",
            "RALLY_PROMO",
            "EBB_SEAL",
        ):
            assert getattr(_mp, name) != self._TSP[name], (
                f"{name} 仍等于 TSP 原值 {self._TSP[name]} — 未完成自标定"
            )

    def test_height_family_locked_to_selene_calibration(self) -> None:
        # 高度类派生自 max_consecutive(0~23 粗粒度整数),诚实标定可能与 TSP 同值。
        # 这里把 selene 面板(20200803~20241031, n=1029)标定出的具体值钉死为回归锁:
        # 若改动这些常量,须重跑 scripts/calibrate_phase_thresholds.py 并同步更新此测试。
        # 面板分位点:p20=4.0 p40=5.0 p60=7.0(见 scratch/b1_calibrate_out.txt)。
        assert _mp.RALLY_HEIGHT == 7  # p60(max_consecutive)=7.0
        assert _mp.RALLY_HEIGHT_ALT == 5  # p40(max_consecutive)=5.0
        assert _mp.IGNITE_HEIGHT == 5  # p40(max_consecutive)=5.0
        assert _mp.ICE_HEIGHT == 4  # p20(max_consecutive)=4.0

    def test_height_family_matches_selene_percentile_derivation(self) -> None:
        # 派生式一致性:高度类 = round(分位),而非 TSP 手写常数。用面板实测分位点
        # (p20=4.0/p40=5.0/p60=7.0)复算,确认新值等于派生式输出。
        p20, p40, p60 = 4.0, 5.0, 7.0
        assert _mp.ICE_HEIGHT == max(1, round(p20))
        assert _mp.RALLY_HEIGHT_ALT == max(1, round(p40))
        assert _mp.IGNITE_HEIGHT == max(1, round(p40))
        assert _mp.RALLY_HEIGHT == max(1, round(p60))

    def test_calibratable_block_not_identical_to_tsp(self) -> None:
        current = {k: getattr(_mp, k) for k in self._TSP}
        differing = [k for k, v in self._TSP.items() if current[k] != v]
        # 实测 17/22 不等;门槛设为 ≥15 以容忍轻微的整数分位并列。
        assert len(differing) >= 15, f"标定块与 TSP 雷同,仅 {differing} 不等"

    def test_structural_constants_unchanged_by_design(self) -> None:
        # 结构常量(非标定对象)按设计保持与来源一致
        assert _mp.PROMO_MIN_POOL == 10
        assert _mp._CONFIRM_DAYS == 2


class TestCalibrationGateBlocksExport:
    """标定门未过时 __init__ 不登记公开符号(import-time 求值,不抛出)。

    门的判定发生在 import 时且只增不删,故用 ``sys.modules`` 驱逐 + 重新 import
    取全新命名空间;``importlib.reload`` 复用旧 __dict__,一旦本地面板存在过一次
    便会残留符号,测不出"未通过即不登记"。
    """

    @staticmethod
    def _fresh_import(calib: bool) -> ModuleType:
        import importlib
        import sys
        from unittest import mock

        saved = sys.modules.pop("selene_space_ashare", None)
        try:
            with mock.patch.object(
                _mp, "calibration_status", return_value=(calib, "synthetic")
            ):
                return importlib.import_module("selene_space_ashare")
        finally:
            if saved is not None:
                sys.modules["selene_space_ashare"] = saved

    def test_gate_withholds_symbols_when_uncalibrated(self) -> None:
        pkg = self._fresh_import(calib=False)
        assert not hasattr(pkg, "classify_phase_series")
        assert not hasattr(pkg, "build_ladder_daily")
        # 既有导出不受影响
        assert hasattr(pkg, "calc_index_regime")
        assert hasattr(pkg, "should_trade")
        assert "classify_phase_series" not in pkg.__all__

    def test_gate_registers_symbols_when_calibrated(self) -> None:
        pkg = self._fresh_import(calib=True)
        assert hasattr(pkg, "classify_phase_series")
        assert hasattr(pkg, "build_ladder_daily")
        assert hasattr(pkg, "attach_ladder_promo")
        assert hasattr(pkg, "ladder_completeness")
        assert hasattr(pkg, "PHASE_LABELS")
        for sym in (
            "classify_phase_series",
            "build_ladder_daily",
            "attach_ladder_promo",
            "ladder_completeness",
            "PHASE_LABELS",
        ):
            assert sym in pkg.__all__


# ───────────────────────── 网络端到端冒烟(默认不跑) ─────────────────────────


@pytest.mark.network
def test_real_pull_to_phase_end_to_end() -> None:
    """Real Tencent pull → ladder panel → classifier, end to end.

    Marked ``network`` (excluded by the default ``-m "not network"`` addopts),
    run with ``pytest -m network``. Unlike the ladder-only smoke, this drives
    the *whole* B1 pipeline: klines are replayed into a daily panel, then the
    6-phase classifier labels it — proving the shipped classifier accepts a
    real panel's dtypes (dates as strings, ``promo_rate`` possibly NaN) and
    emits only vocabulary phases.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    from backfill_ladder_panel import build_panel, fetch_one  # type: ignore

    # The Sept-Oct 2024 rally window: a real, known active stretch.
    frame = fetch_one("600519", "贵州茅台", "20240924", "20241011")
    assert frame is not None, "Tencent fetch returned nothing"
    panel = build_panel([frame])
    assert not panel.empty

    out = classify_phase_series(panel)
    assert "phase" in out.columns
    assert len(out) == len(panel)
    assert set(out["phase"].unique()) <= set(PHASE_LABELS)

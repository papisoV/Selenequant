"""SeleneQuant A-Share — Factor mining, IC/IR evaluation, regime detection, and backtesting."""

from selene_space_ashare.factor_calc import calculate_technical_factors
from selene_space_ashare.factor_eval import (
    calc_ic_neutralized,
    factor_evaluation_report,
)
from selene_space_ashare.fundamentals_pit import attach_fundamentals, load_financials
from selene_space_ashare.regime import (
    calc_index_regime,
    get_regime_on_date,
    should_trade,
)
from selene_space_ashare.composite import (
    build_composite_panel,
    compute_ic_weights_walkforward,
)
from selene_space_ashare.backtest import backtest_full_strategy
from selene_space_ashare.concept_rps import build_concept_rps
from selene_space_ashare.factors import ALL_FACTORS
from selene_space_ashare.market_daily import market_daily_universe

__all__ = [
    "calculate_technical_factors",
    "factor_evaluation_report",
    "calc_index_regime",
    "get_regime_on_date",
    "should_trade",
    "build_composite_panel",
    "backtest_full_strategy",
    "ALL_FACTORS",
    # factor evaluation & PIT fundamentals
    "compute_ic_weights_walkforward",
    "attach_fundamentals",
    "load_financials",
    "calc_ic_neutralized",
    # 全市场日线读面(B3):筛选器起手截面
    "market_daily_universe",
    # 概念级 120 日 RPS(B5):等权成员涨幅的截面百分位
    "build_concept_rps",
]
# ── B1 情绪周期 6 阶段 + 连板梯队(COEXIST 追加;不动 regime/should_trade)──
# 导出以标定门为门(import-time 求值):门通过 → 登记公开符号;门未通过
# (如干净 clone 无回填面板)→ 仅告警、不登记,保证包其余功能照常 import。
# 硬断言入口:market_phase.assert_calibration()。
from selene_space_ashare import market_phase as _market_phase  # noqa: E402

_calib_ok, _calib_reason = _market_phase.calibration_status()
if _calib_ok:
    from selene_space_ashare.ladder import (  # noqa: E402
        attach_ladder_promo,
        build_ladder_daily,
        ladder_completeness,
    )
    from selene_space_ashare.market_phase import (  # noqa: E402
        PHASE_CLIMAX,
        PHASE_EBB,
        PHASE_ICE,
        PHASE_IGNITE,
        PHASE_LABELS,
        PHASE_RALLY,
        PHASE_REPAIR,
        classify_phase_series,
    )

    __all__ += [
        "build_ladder_daily",
        "attach_ladder_promo",
        "ladder_completeness",
        "classify_phase_series",
        "PHASE_LABELS",
        "PHASE_ICE",
        "PHASE_IGNITE",
        "PHASE_RALLY",
        "PHASE_CLIMAX",
        "PHASE_EBB",
        "PHASE_REPAIR",
    ]
else:
    import warnings as _warnings

    _warnings.warn(
        "selene_space_ashare: 标定门未通过,情绪周期/连板梯队导出暂缓 — "
        + _calib_reason,
        stacklevel=2,
    )

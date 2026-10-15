"""Stack-based virtual machine for formula execution (pandas/numpy-native).

Mirrors the logic of vm.py StackVM exactly, but operates on pandas Series
instead of torch tensors. This lets AI-discovered formula token sequences
be evaluated within SeleneQuant's pandas-based backtest engine.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pandas as pd

from ..ops.ops_pandas import OPS_CONFIG_PANDAS
from ..factors.pandas_bridge import ALL_FACTORS
from ..vocab import FORMULA_VOCAB, FormulaVocab


class PandasStackVM:
    """Executes formula token sequences on pandas DataFrames.

    Token < operator_offset: feature index (compute factor from DataFrame)
    Token >= operator_offset: operator (pop arity args, push result)

    The feature columns are pre-computed and cached to avoid redundant work.
    """

    def __init__(self, vocab: FormulaVocab | None = None):
        vocab = vocab or FORMULA_VOCAB
        self._vocab = vocab
        self.feat_offset = vocab.operator_offset

        # Build op_map from vocab's operator_names, not OPS_CONFIG_PANDAS index.
        # This ensures token IDs match vocab.token_names correctly,
        # even when vocab filters out certain operators (e.g. GATE).
        ops_by_name: dict[str, tuple[object, int]] = {
            cfg[0]: (cfg[1], cfg[2]) for cfg in OPS_CONFIG_PANDAS
        }
        self.op_map: dict[int, object] = {}
        self.arity_map: dict[int, int] = {}
        for i, op_name in enumerate(vocab.operator_names):
            token_id = self.feat_offset + i
            if op_name in ops_by_name:
                self.op_map[token_id] = ops_by_name[op_name][0]
                self.arity_map[token_id] = ops_by_name[op_name][1]

        # Build ordered list of factor compute functions matching vocab
        # (ALL_FACTORS values are typed `object` upstream; all real entries are
        # callables — the None slot marks "read column from DataFrame".)
        self._factor_fns: list[
            tuple[str, Callable[[pd.DataFrame], pd.Series] | None]
        ] = []

        # Map vocab feature names to compute functions
        _FACTOR_NAME_MAP = {
            "RET": "meme_ret",
            "LIQ_SCORE": "meme_liq_score",
            "PRESSURE": "meme_pressure",
            "FOMO": "meme_fomo",
            "DEV": "meme_dev",
            "LOG_VOL": "meme_log_vol",
            "VOL_CLUSTER": "meme_vol_cluster",
            "MOMENTUM_REV": "meme_momentum_rev",
            "REL_STRENGTH": "meme_rel_strength",
            "HL_RANGE": "meme_hl_range",
            "CLOSE_POS": "meme_close_pos",
            "VOL_TREND": "meme_vol_trend",
            # V4 features
            "WICK_RATIO": "hf_wick_ratio",
            "BODY_SIZE": "hf_body_size",
            "IMBALANCE": "hf_imbalance",
            "VWAP_DEV": "hf_vwap_dev",
            "SPREAD_PROXY": "hf_spread_proxy",
            "GAP": "hf_gap",
            "RS_RET5": "hf_rs_ret5",
            "RS_VOL5": "hf_rs_vol5",
            "RS_SKEW": "hf_rs_skew",
            "RS_KURT": "hf_rs_kurt",
            # Generic multi-TF (auto-detects TF*_ prefix in DataFrame)
            "TF_TREND": "hf_tf_trend",
            "TF_VOL_RATIO": "hf_tf_vol_ratio",
            "TF_RANGE_POS": "hf_tf_range_pos",
            "TF_PRESSURE": "hf_tf_pressure",
            "TF_DEV": "hf_tf_dev",
            "TF_MOM": "hf_tf_mom",
            # Backward-compatible TF15_ naming
            "TF15_TREND": "hf_tf15_trend",
            "TF15_VOL_RATIO": "hf_tf15_vol_ratio",
            "TF15_RANGE_POS": "hf_tf15_range_pos",
            "TF15_PRESSURE": "hf_tf15_pressure",
            "TF15_DEV": "hf_tf15_dev",
            "TF15_MOM": "hf_tf15_mom",
            # Derivatives features
            "FUNDING_RATE": "deriv_funding_rate",
            "FUNDING_RATE_Z": "deriv_funding_rate_z",
            "OI_CHANGE": "deriv_oi_change",
            "LS_RATIO": "deriv_ls_ratio",
            # v13 Orderflow factors (8)
            "OF_GLOBAL_LS_RATIO": "of_global_ls_ratio",
            "OF_GLOBAL_LS_EXTREME": "of_global_ls_extreme",
            "OF_SMART_CROWD_DIV": "of_smart_crowd_div",
            "OF_BUY_VOL_PRESSURE": "of_buy_vol_pressure",
            "OF_SELL_VOL_PRESSURE": "of_sell_vol_pressure",
            "OF_VOL_IMBALANCE": "of_vol_imbalance",
            "OF_TOP_ACC_CHANGE": "of_top_acc_change",
            "OF_TOP_POS_ACC_DIV": "of_top_pos_acc_div",
            # v13 Derivatives factors (2)
            "DERIV_FUNDING_MOMENTUM": "deriv_funding_momentum",
            "DERIV_FUNDING_EXTREME": "deriv_funding_extreme",
            # Volume shock features
            "VOL_SHOCK": "vs_vol_shock",
            "VOL_SHOCK_Z": "vs_vol_shock_z",
            "VOL_SURGE": "vs_vol_surge",
            # Serenity fundamental features
            "SERENITY_SCORE": "ser_score",
            "SERENITY_CONSENSUS": "ser_consensus",
            "SERENITY_LIQUIDITY": "ser_liquidity",
            "SERENITY_PREPAY": "ser_prepay",
            "SERENITY_EVIDENCE": "ser_evidence",
            "SERENITY_PENALTY": "ser_penalty",
            # FIB factors (9) — merged from selenespace-research
            "FIB_POS": "fib_pos",
            "FIB_CONFLUENCE": "fib_confluence",
            "FIB_DENSITY": "fib_density",
            "FIB_BOUNCE": "fib_bounce",
            "FIB_STRENGTH": "fib_strength",
            "FIB_MOMENTUM": "fib_momentum",
            "FIB_RESONANCE": "fib_resonance",
            "FIB_CROSSOVER": "fib_crossover",
            "FIB_DECAY_POS": "fib_decay_pos",
            # FIB2 factors (6)
            "FIB2_HIGH_DENSITY": "fib2_high_density",
            "FIB2_ATR_PROX": "fib2_atr_prox",
            "FIB2_TREND_BOUNCE": "fib2_trend_bounce",
            "FIB2_MULTI_GRID": "fib2_multi_grid",
            "FIB2_BREAKOUT_CONFIRM": "fib2_breakout_confirm",
            "FIB2_LEVEL_MOMENTUM": "fib2_level_momentum",
            # FIB3 factors (6)
            "FIB3_TIME_AT_LEVEL": "fib3_time_at_level",
            "FIB3_ACCELERATION": "fib3_acceleration",
            "FIB3_VOLUME_PROFILE": "fib3_volume_profile",
            "FIB3_REACTION_REGIME": "fib3_reaction_regime",
            "FIB3_MAGNET_STRENGTH": "fib3_magnet_strength",
            "FIB3_REJECTION_RATIO": "fib3_rejection_ratio",
            # OF factors (8 new — research-only, master already had 8)
            "OF_TAKER_RATIO": "of_taker_ratio",
            "OF_TAKER_SPIKE": "of_taker_spike",
            "OF_TOP_POS_RATIO": "of_top_pos_ratio",
            "OF_TOP_POS_CHANGE": "of_top_pos_change",
            "OF_TOP_ACC_RATIO": "of_top_acc_ratio",
            "OF_FUNDING_IMPULSE": "of_funding_impulse",
            "OF_LIQUIDATION_MAGNET": "of_liquidation_magnet",
            "OF_CROWD_DIVERGENCE": "of_crowd_divergence",
            # DERIV factors (14 new — research-only)
            "DERIV_FUNDING_RATE": "deriv_funding_rate",
            "DERIV_FUNDING_Z": "deriv_funding_z",
            "DERIV_OI_CHANGE": "deriv_oi_change",
            "DERIV_OI_PRICE_DIV": "deriv_oi_price_div",
            "DERIV_OI_Z": "deriv_oi_z",
            "DERIV_OI_FUNDING_DIV": "deriv_oi_funding_div",
            "DERIV_OI_VOL_RATIO": "deriv_oi_vol_ratio",
            "DERIV_LIQ_IMBALANCE": "deriv_liq_imbalance",
            "DERIV_LIQ_SPIKE": "deriv_liq_spike",
            "DERIV_LIQ_PRESSURE_PROXY": "deriv_liq_pressure_proxy",
            "DERIV_LIQ_SPIKE_PROXY": "deriv_liq_spike_proxy",
            "DERIV_LIQ_MAGNET_PROXY": "deriv_liq_magnet_proxy",
            "DERIV_BASIS": "deriv_basis",
            "DERIV_BASIS_EXTREME": "deriv_basis_extreme",
            # Awesome-systematic-trading factors (20)
            "AW_52W_HIGH": "aw_52w_high",
            "AW_TSMOM_12M": "aw_tsmom_12m",
            "AW_TSMOM_SIGN": "aw_tsmom_sign",
            "AW_MOM_6M": "aw_mom_6m",
            "AW_CONSISTENT_MOM": "aw_consistent_mom",
            "AW_REVERSAL_5D": "aw_reversal_5d",
            "AW_REVERSAL_10D": "aw_reversal_10d",
            "AW_LOW_VOL": "aw_low_vol",
            "AW_VOL_RISK_PREMIUM": "aw_vol_risk_premium",
            "AW_SKEW_12M": "aw_skew_12m",
            "AW_RETURN_ASYMMETRY": "aw_return_asymmetry",
            "AW_TREND_MA_SLOPE": "aw_trend_ma_slope",
            "AW_TREND_ABOVE_MA": "aw_trend_above_ma",
            "AW_TURN_OF_MONTH": "aw_turn_of_month",
            "AW_OVERNIGHT_8H": "aw_overnight_8h",
            "AW_MOM_ACCELERATION": "aw_mom_acceleration",
            "AW_TREND_MA_CROSS": "aw_trend_ma_cross",
            "AW_DRAWDOWN": "aw_drawdown",
            "AW_VOL_OF_VOL": "aw_vol_of_vol",
            "AW_VOLUME_TREND": "aw_volume_trend",
            # QD-absorption B3 factors (3)
            "CHOPPINESS_14": "qd_choppiness_14",
            "ELDER_BULL_13": "qd_elder_bull_13",
            "ELDER_BEAR_13": "qd_elder_bear_13",
            # QD-absorption B2 background factors (3)
            "BTC_BG_RS_LEVEL": "btc_bg_rs_level",
            "BTC_BG_RS_SLOPE": "btc_bg_rs_slope",
            "BTC_BG_RS_EXTREME": "btc_bg_rs_extreme",
            # Artifact-bridge factors (7) — *_autogen_factor.py ports
            "ICHIMOKU_KIJUN_DEV": "art_ichimoku_kijun_dev",
            "GK_VOL": "art_gk_vol",
            "RET_ENTROPY_20": "art_ret_entropy_20",
            "RVOL": "art_rvol",
            "MACD_HIST": "art_macd_hist",
            "TRIX_15": "art_trix_15",
            "HMA_DEV_20": "art_hma_dev_20",
        }
        for feat_name in vocab.feature_names:
            pandas_name = _FACTOR_NAME_MAP.get(feat_name)
            if pandas_name and pandas_name in ALL_FACTORS:
                fn = cast(
                    "Callable[[pd.DataFrame], pd.Series]",
                    ALL_FACTORS[pandas_name][0],
                )
                self._factor_fns.append((feat_name, fn))
            else:
                self._factor_fns.append((feat_name, None))

    def _compute_features(
        self, data: pd.DataFrame, needed: set[int] | None = None
    ) -> dict[int, pd.Series]:
        """Pre-compute feature columns from a DataFrame.

        ``needed`` restricts computation to the referenced feature indices;
        a formula touches 4-8 features out of ~110, several of which carry
        per-bar Python loops, so computing the unused ones dominates the
        bar-by-bar backtest cost for zero benefit.
        """
        features: dict[int, pd.Series] = {}
        for idx, (feat_name, fn) in enumerate(self._factor_fns):
            if needed is not None and idx not in needed:
                continue
            if fn is not None:
                features[idx] = fn(data).fillna(0.0)  # type: ignore[operator]
            else:
                # Fallback: try to use column directly
                col_name = feat_name.lower()
                if col_name in data.columns:
                    features[idx] = data[col_name].fillna(0.0)
                else:
                    features[idx] = pd.Series(0.0, index=data.index)
        return features

    def execute(
        self,
        formula_tokens: list[int],
        data: pd.DataFrame,
        precomputed_features: dict[int, pd.Series] | None = None,
    ) -> pd.Series | None:
        """Execute a formula on a DataFrame.

        Args:
            formula_tokens: List of token indices (same format as StackVM).
            data: DataFrame with OHLCV + liquidity/fdv columns.
            precomputed_features: Optional pre-computed feature dict to skip
                _compute_features(). Keys are feature indices, values are Series.

        Returns:
            pd.Series with the formula result, or None on error.
        """
        needed = {t for t in formula_tokens if int(t) < self.feat_offset}
        features = precomputed_features or self._compute_features(data, needed)
        stack: list[pd.Series] = []

        try:
            for token in formula_tokens:
                token = int(token)
                if token < self.feat_offset:
                    if token not in features:
                        return None
                    stack.append(features[token])
                elif token in self.op_map:
                    arity = self.arity_map[token]
                    if len(stack) < arity:
                        return None
                    args = []
                    for _ in range(arity):
                        args.append(stack.pop())
                    args.reverse()
                    func = self.op_map[token]
                    res = func(*args)  # type: ignore[operator]
                    if isinstance(res, pd.Series):
                        res = res.fillna(0.0)
                        res = res.replace([float("inf")], 1e6).replace(
                            [float("-inf")], -1e6
                        )
                        # Clip to prevent exponential blowup
                        res = res.clip(-1e6, 1e6)
                    stack.append(res)
                else:
                    return None

            if len(stack) == 1:
                return stack[0]
            return None
        except Exception:
            return None

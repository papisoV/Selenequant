"""Real multi-symbol data loader for factor mining.

Uses actual K-line data from multiple trading pairs (BTC, ETH, SOL)
as "tokens" instead of synthetic noise. Removes LIQ_SCORE (not available
on CEX) and uses 11 real features from AdvancedFactorEngineer.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from selene_space_alpha.factors.feature_engineer import AdvancedFactorEngineer
from selene_space_core.kline_paths import default_klines_db_path

logger = logging.getLogger(__name__)

# 11 real features for CEX data (no LIQ_SCORE)
CEX_FEATURE_NAMES = (
    "RET",
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
)

# V6: 31 features for derivatives-enhanced mining (V4 27 + 4 derivatives)
CEX_FEATURE_NAMES_V6 = (
    # Original 11
    "RET",
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
    # Microstructure 6
    "WICK_RATIO",
    "BODY_SIZE",
    "IMBALANCE",
    "VWAP_DEV",
    "SPREAD_PROXY",
    "GAP",
    # Rolling statistical 4
    "RS_RET5",
    "RS_VOL5",
    "RS_SKEW",
    "RS_KURT",
    # Multi-timeframe 6 (generic TF_ — auto-detects prefix in DataFrame)
    "TF_TREND",
    "TF_VOL_RATIO",
    "TF_RANGE_POS",
    "TF_PRESSURE",
    "TF_DEV",
    "TF_MOM",
    # Derivatives 4
    "FUNDING_RATE",
    "FUNDING_RATE_Z",
    "OI_CHANGE",
    "LS_RATIO",
)

# V4: 27 features for short-interval mining (15m base + 1h aux)
CEX_FEATURE_NAMES_V4 = (
    # Original 11
    "RET",
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
    # Microstructure 6
    "WICK_RATIO",
    "BODY_SIZE",
    "IMBALANCE",
    "VWAP_DEV",
    "SPREAD_PROXY",
    "GAP",
    # Rolling statistical 4
    "RS_RET5",
    "RS_VOL5",
    "RS_SKEW",
    "RS_KURT",
    # Multi-timeframe 6 (generic TF_ — auto-detects prefix in DataFrame)
    "TF_TREND",
    "TF_VOL_RATIO",
    "TF_RANGE_POS",
    "TF_PRESSURE",
    "TF_DEV",
    "TF_MOM",
)

# Backward-compatible alias
CEX_FEATURE_NAMES_V4_SHORT = CEX_FEATURE_NAMES_V4

# V4 Long: 27 features for long-interval mining (4h base + 1d aux)
CEX_FEATURE_NAMES_V4_LONG = CEX_FEATURE_NAMES_V4

# V8: 30 features for volume-shock-enhanced mining (V4 27 + 3 volume shock)
CEX_FEATURE_NAMES_V8 = (
    *CEX_FEATURE_NAMES_V4,
    # Volume shock 3
    "VOL_SHOCK",
    "VOL_SHOCK_Z",
    "VOL_SURGE",
)

# Serenity: 36 features for fundamental-enhanced mining (V8 30 + 6 Serenity)
CEX_FEATURE_NAMES_SERENITY = (
    *CEX_FEATURE_NAMES_V8,
    # Serenity fundamental factors (6)
    "SERENITY_SCORE",
    "SERENITY_CONSENSUS",
    "SERENITY_LIQUIDITY",
    "SERENITY_PREPAY",
    "SERENITY_EVIDENCE",
    "SERENITY_PENALTY",
)

# V13: 47 features for orderflow-enhanced mining (V6 31 + 10 v13 orderflow/deriv)
# Includes global LS ratio, volume-based orderflow, account-level, and funding advanced
CEX_FEATURE_NAMES_V13 = (
    *CEX_FEATURE_NAMES_V6,
    # v13 Orderflow factors (8)
    "OF_GLOBAL_LS_RATIO",
    "OF_GLOBAL_LS_EXTREME",
    "OF_SMART_CROWD_DIV",
    "OF_BUY_VOL_PRESSURE",
    "OF_SELL_VOL_PRESSURE",
    "OF_VOL_IMBALANCE",
    "OF_TOP_ACC_CHANGE",
    "OF_TOP_POS_ACC_DIV",
    # v13 Derivatives factors (2)
    "DERIV_FUNDING_MOMENTUM",
    "DERIV_FUNDING_EXTREME",
)

# V13 derivatives-only: 14 features (deriv + OF only, no kline/TF)
CEX_FEATURE_NAMES_V13_DERIV_ONLY = (
    # Existing 4 derivatives
    "FUNDING_RATE",
    "FUNDING_RATE_Z",
    "OI_CHANGE",
    "LS_RATIO",
    # v13 Orderflow (8)
    "OF_GLOBAL_LS_RATIO",
    "OF_GLOBAL_LS_EXTREME",
    "OF_SMART_CROWD_DIV",
    "OF_BUY_VOL_PRESSURE",
    "OF_SELL_VOL_PRESSURE",
    "OF_VOL_IMBALANCE",
    "OF_TOP_ACC_CHANGE",
    "OF_TOP_POS_ACC_DIV",
    # v13 Derivatives (2)
    "DERIV_FUNDING_MOMENTUM",
    "DERIV_FUNDING_EXTREME",
)

# V13 derivatives-focus: 20 features (deriv + OF + TF, no basic/micro/RS)
CEX_FEATURE_NAMES_V13_DERIV_FOCUS = (
    # Multi-timeframe 6
    "TF_TREND",
    "TF_VOL_RATIO",
    "TF_RANGE_POS",
    "TF_PRESSURE",
    "TF_DEV",
    "TF_MOM",
    # Existing 4 derivatives
    "FUNDING_RATE",
    "FUNDING_RATE_Z",
    "OI_CHANGE",
    "LS_RATIO",
    # v13 Orderflow (8)
    "OF_GLOBAL_LS_RATIO",
    "OF_GLOBAL_LS_EXTREME",
    "OF_SMART_CROWD_DIV",
    "OF_BUY_VOL_PRESSURE",
    "OF_SELL_VOL_PRESSURE",
    "OF_VOL_IMBALANCE",
    "OF_TOP_ACC_CHANGE",
    "OF_TOP_POS_ACC_DIV",
    # v13 Derivatives (2)
    "DERIV_FUNDING_MOMENTUM",
    "DERIV_FUNDING_EXTREME",
)


class RealMultiSymbolLoader:
    """Loads real multi-symbol CEX kline data as feature tensors.

    Each symbol becomes a "token" in the tensor — no synthetic noise.
    """

    def __init__(self, config=None):
        self.config = config
        self.feat_tensor: torch.Tensor | None = None
        self.raw_data_cache: dict[str, torch.Tensor] = {}
        self.target_ret: torch.Tensor | None = None
        self.addresses: list[str] = []
        self.feature_names = CEX_FEATURE_NAMES

    def load_from_sqlite(
        self,
        db_path: str | Path | None = None,
        symbols: list[str] | None = None,
        exchanges: list[str] | None = None,
        intervals: list[str] | None = None,
        limit_bars: int = 500,
    ) -> None:
        """Load real multi-symbol CEX kline data.

        Each (symbol, interval) combination becomes a "token".
        For example: BTC/4h, ETH/4h, SOL/4h, BTC/1d, ETH/1d, SOL/1d = 6 tokens.
        """
        import sqlite3

        if db_path is None:
            db_path = default_klines_db_path()
        if symbols is None:
            symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        if exchanges is None:
            exchanges = ["binance"] * len(symbols)
        if intervals is None:
            intervals = ["4h", "1d"]

        db = Path(db_path)
        if not db.exists():
            raise FileNotFoundError(f"Kline DB not found: {db}")

        # Build all (symbol, exchange, interval) combos
        combos: list[tuple[str, str, str]] = []
        for sym, exc in zip(symbols, exchanges):
            for iv in intervals:
                combos.append((sym, exc, iv))

        all_dfs: dict[str, pd.DataFrame] = {}
        common_timestamps: set[int] | None = None

        for symbol, exchange, interval in combos:
            key = f"{symbol}:{interval}"
            conn = sqlite3.connect(str(db_path))
            query = (
                "SELECT timestamp, open, high, low, close, volume "
                "FROM klines "
                "WHERE symbol = ? AND exchange = ? AND interval = ? "
                "ORDER BY timestamp DESC LIMIT ?"
            )
            df = pd.read_sql_query(
                query, conn, params=(symbol, exchange, interval, limit_bars)
            )
            conn.close()

            if df.empty:
                logger.warning(
                    "No data for %s/%s/%s, skipping", symbol, exchange, interval
                )
                continue

            df = df.sort_values("timestamp").reset_index(drop=True)
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            ts_set = set(df["timestamp"].tolist())
            if common_timestamps is None:
                common_timestamps = ts_set
            else:
                common_timestamps &= ts_set

            all_dfs[key] = df

        if not all_dfs:
            raise ValueError("No data loaded")

        # Each key is a token
        token_list = list(all_dfs.keys())
        self.addresses = token_list

        if common_timestamps:
            ts_sorted = sorted(common_timestamps)
            n_bars = len(ts_sorted)
        else:
            n_bars = min(len(df) for df in all_dfs.values())

        close_list, open_list, high_list, low_list, vol_list = [], [], [], [], []

        for key in token_list:
            df = all_dfs[key]
            if common_timestamps:
                df = (
                    df[df["timestamp"].isin(common_timestamps)]
                    .sort_values("timestamp")
                    .reset_index(drop=True)
                )

            if len(df) > n_bars:
                df = df.iloc[-n_bars:]
            elif len(df) < n_bars:
                pad_len = n_bars - len(df)
                pad_row = df.iloc[[-1]]
                pad_df = pd.concat([pad_row] * pad_len, ignore_index=True)
                df = pd.concat([df, pad_df], ignore_index=True)

            close_list.append(df["close"].values.astype(np.float32))
            open_list.append(df["open"].values.astype(np.float32))
            high_list.append(df["high"].values.astype(np.float32))
            low_list.append(df["low"].values.astype(np.float32))
            vol_list.append(df["volume"].values.astype(np.float32))

        self.raw_data_cache = {
            "close": torch.tensor(np.stack(close_list), dtype=torch.float32),
            "open": torch.tensor(np.stack(open_list), dtype=torch.float32),
            "high": torch.tensor(np.stack(high_list), dtype=torch.float32),
            "low": torch.tensor(np.stack(low_list), dtype=torch.float32),
            "volume": torch.tensor(np.stack(vol_list), dtype=torch.float32),
        }
        self.raw_data_cache["liquidity"] = self.raw_data_cache["volume"] * 1000
        self.raw_data_cache["fdv"] = self.raw_data_cache["close"] * 21e6

        fe = AdvancedFactorEngineer()
        # AdvancedFactorEngineer produces 12 features; we drop LIQ_SCORE (index 1)
        full_features = fe.compute_advanced_features(self.raw_data_cache)
        # Drop LIQ_SCORE at index 1 → 11 features
        indices = [i for i in range(12) if i != 1]
        self.feat_tensor = full_features[:, indices, :]

        self.target_ret = torch.log(
            self.raw_data_cache["close"]
            / (torch.roll(self.raw_data_cache["close"], 1, dims=1) + 1e-9)
        )

        logger.info(
            "Loaded real CEX data: %d tokens × %d bars × %d features",
            len(self.addresses),
            n_bars,
            len(self.feature_names),
        )

    def load_data(self, limit_tokens: int = 300) -> None:
        self.load_from_sqlite()

    def _load_kline_df(self, *args, **kwargs):
        raise NotImplementedError("Use load_from_sqlite() directly")

"""CryptoDataLoader adapter for alpha-core mining.

Adapts SeleneQuant's SQLite kline data into the tensor format
expected by the mining engine, while also supporting
loading from DEX data pipelines.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from selene_space_alpha.factors.feature_engineer import (
    FeatureEngineer,
    AdvancedFactorEngineer,
)
from selene_space_alpha.config import AlphaCoreConfig
from selene_space_core.kline_paths import default_klines_db_path

logger = logging.getLogger(__name__)


class CryptoDataLoader:
    """Loads crypto market data and produces feature tensors for alpha mining.

    Supports two data sources:
      1. SeleneQuant SQLite klines (CEX data, single symbol)
      2. DEX pipeline data (multi-token, PostgreSQL/SQLite)

    For CEX data, creates a synthetic multi-token tensor by applying
    rolling windows to simulate multiple "tokens" from a single price series.
    """

    def __init__(self, config: AlphaCoreConfig | None = None):
        self.config = config or AlphaCoreConfig()
        self.feat_tensor: torch.Tensor | None = None
        self.raw_data_cache: dict[str, torch.Tensor] = {}
        self.target_ret: torch.Tensor | None = None
        self.addresses: list[str] = []

    def load_from_sqlite(
        self,
        db_path: str | Path | None = None,
        symbol: str = "BTC/USDT",
        exchange: str = "binance",
        market_type: str = "futures",
        interval: str = "1h",
        limit_bars: int = 500,
        n_synthetic_tokens: int = 300,
    ) -> None:
        """Load CEX kline data from SeleneQuant's SQLite DB.

        Creates synthetic multi-token tensor by splitting the time series
        with random noise to simulate the multi-token format.
        """

        if db_path is None:
            db_path = default_klines_db_path()
        db = Path(db_path)
        if not db.exists():
            raise FileNotFoundError(f"Kline DB not found: {db}")

        df = self._load_kline_df(
            db, symbol, exchange, market_type, interval, limit_bars
        )
        if df.empty:
            raise ValueError(f"No kline data for {symbol}")

        self.addresses = [f"{exchange}:{symbol}:{i}" for i in range(n_synthetic_tokens)]
        self._build_tensors_from_df(df, n_synthetic_tokens)
        logger.info(
            "Loaded CEX data: %d bars × %d synthetic tokens",
            len(df),
            n_synthetic_tokens,
        )

    def load_from_dataframe(
        self,
        df: pd.DataFrame,
        n_synthetic_tokens: int = 300,
    ) -> None:
        """Load data from an existing DataFrame.

        DataFrame must have columns: time, address, open, high, low, close,
        volume, liquidity, fdv (DEX format) or open, high, low, close, volume
        (CEX format).
        """
        if "address" in df.columns and "liquidity" in df.columns:
            # Multi-token DEX format
            unique_addresses = df["address"].unique().tolist()
            self.addresses = unique_addresses[:n_synthetic_tokens]
            self._build_tensors_multi_token(df)
        else:
            # Single symbol CEX format
            self.addresses = [f"synth:{i}" for i in range(n_synthetic_tokens)]
            self._build_tensors_from_df(df, n_synthetic_tokens)

    def load_from_parquet(
        self,
        path: str | Path,
        n_synthetic_tokens: int = 300,
        limit_bars: int | None = None,
    ) -> None:
        """Load OHLCV kline data from a local parquet file.

        Accepts any parquet with OHLCV columns (case-insensitive; ``open_time``
        /``timestamp``/``time``/``date`` all work as the time column). Single
        symbol CEX format is assumed; a multi-token DEX file (address+liquidity)
        is routed to the multi-token builder.
        """
        df = pd.read_parquet(path)
        if limit_bars:
            df = df.tail(limit_bars)

        rename_map = {}
        for col in df.columns:
            lc = str(col).lower()
            if lc in ("open", "high", "low", "close", "volume"):
                rename_map[col] = lc
            elif lc in ("open_time", "timestamp", "time", "date"):
                rename_map[col] = "time"
        df = df.rename(columns=rename_map)

        required = {"open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Parquet missing OHLCV columns: {sorted(missing)}")

        if "address" in df.columns and "liquidity" in df.columns:
            self.load_from_dataframe(df, n_synthetic_tokens)
            return

        if "time" in df.columns:
            df = df.sort_values("time").reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"]).reset_index(drop=True)
        if df.empty:
            raise ValueError(f"Parquet has no usable rows: {path}")

        self.addresses = [
            f"parquet:{Path(path).stem}:{i}" for i in range(n_synthetic_tokens)
        ]
        self._build_tensors_from_df(df, n_synthetic_tokens)
        logger.info("Loaded parquet: %s → %d bars", path, len(df))

    def load_data(self, limit_tokens: int = 300) -> None:
        """Convenience entry point matching AlphaEngine's expected interface."""
        # Idempotent guard: an explicit load (e.g. endpoint-loaded real data)
        # must not be silently replaced by this default-path convenience call.
        if self.feat_tensor is not None:
            return
        # Default: try SQLite first, fall back to synthetic
        try:
            self.load_from_sqlite(n_synthetic_tokens=limit_tokens)
        except (FileNotFoundError, ValueError, Exception) as e:
            logger.warning(
                "SQLite kline data unavailable (%s), creating synthetic data", e
            )
            self._create_synthetic_data(limit_tokens)

    def _load_kline_df(
        self,
        db_path: Path,
        symbol: str,
        exchange: str,
        market_type: str,
        interval: str,
        limit: int,
    ) -> pd.DataFrame:
        import sqlite3

        conn = sqlite3.connect(str(db_path))

        # Try unified 'klines' table first (SeleneQuant format),
        # fall back to per-exchange-interval table if not found.
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='klines'"
        )
        if cursor.fetchone():
            query = (
                "SELECT timestamp AS open_time, open, high, low, close, volume "
                "FROM klines "
                "WHERE symbol = ? AND exchange = ? AND market_type = ? AND interval = ? "
                "ORDER BY timestamp DESC LIMIT ?"
            )
            df = pd.read_sql_query(
                query,
                conn,
                params=(symbol, exchange, market_type, interval, limit),
            )
        else:
            table_name = f"klines_{exchange}_{interval}"
            query = (
                f"SELECT open_time, open, high, low, close, volume "
                f"FROM {table_name} "
                f"WHERE symbol = ? ORDER BY open_time DESC LIMIT ?"
            )
            df = pd.read_sql_query(query, conn, params=(symbol, limit))
        conn.close()

        if df.empty:
            return df

        df = df.sort_values("open_time").reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def _build_tensors_from_df(self, df: pd.DataFrame, n_tokens: int) -> None:
        cols = ["open", "high", "low", "close", "volume"]
        arr = df[cols].values.T  # [5, time]
        n_bars = arr.shape[1]

        # Add synthetic liquidity and fdv (not available in CEX data)
        liquidity = np.abs(np.random.randn(n_tokens, n_bars)) * 1e6
        fdv = np.abs(np.random.randn(n_tokens, n_bars)) * 1e8

        # Broadcast single token's OHLCV to multiple tokens with noise
        base = np.tile(arr, (n_tokens, 1, 1))  # [tokens, 5, time]
        noise = np.random.randn(*base.shape) * 0.01
        ohlcv_tensor = torch.tensor(base + base * noise, dtype=torch.float32)

        liquidity_t = torch.tensor(liquidity, dtype=torch.float32)
        fdv_t = torch.tensor(fdv, dtype=torch.float32)

        self.raw_data_cache = {
            "close": ohlcv_tensor[:, 3, :],
            "open": ohlcv_tensor[:, 0, :],
            "high": ohlcv_tensor[:, 1, :],
            "low": ohlcv_tensor[:, 2, :],
            "volume": ohlcv_tensor[:, 4, :],
            "liquidity": liquidity_t,
            "fdv": fdv_t,
        }

        fe = FeatureEngineer()
        self.feat_tensor = fe.compute_features(self.raw_data_cache)
        self.target_ret = torch.log(
            self.raw_data_cache["close"]
            / (torch.roll(self.raw_data_cache["close"], 1, dims=1) + 1e-9)
        )

    def _build_tensors_multi_token(self, df: pd.DataFrame) -> None:
        pivot_dfs: dict[str, pd.DataFrame] = {}
        for col in ["open", "high", "low", "close", "volume", "liquidity", "fdv"]:
            if col in df.columns:
                pivot_dfs[col] = (
                    df.pivot_table(
                        index="time", columns="address", values=col, aggfunc="last"
                    )
                    .fillna(method="ffill")
                    .fillna(0)
                    .T
                )

        self.raw_data_cache = {
            k: torch.tensor(v.values, dtype=torch.float32) for k, v in pivot_dfs.items()
        }

        fe = AdvancedFactorEngineer()
        self.feat_tensor = fe.compute_advanced_features(self.raw_data_cache)
        self.target_ret = torch.log(
            self.raw_data_cache["close"]
            / (torch.roll(self.raw_data_cache["close"], 1, dims=1) + 1e-9)
        )

    def _create_synthetic_data(self, n_tokens: int = 300, n_bars: int = 500) -> None:
        self.addresses = [f"synth:{i}" for i in range(n_tokens)]
        base_price = np.abs(np.random.randn(n_bars)).cumsum() + 100
        base = np.tile(base_price, (n_tokens, 1))
        noise = np.random.randn(*base.shape) * 0.02
        prices = base * (1 + noise)

        self.raw_data_cache = {
            "close": torch.tensor(prices, dtype=torch.float32),
            "open": torch.tensor(
                prices * (1 + np.random.randn(*prices.shape) * 0.001),
                dtype=torch.float32,
            ),
            "high": torch.tensor(
                prices * (1 + np.abs(np.random.randn(*prices.shape)) * 0.005),
                dtype=torch.float32,
            ),
            "low": torch.tensor(
                prices * (1 - np.abs(np.random.randn(*prices.shape)) * 0.005),
                dtype=torch.float32,
            ),
            "volume": torch.tensor(
                np.abs(np.random.randn(n_tokens, n_bars)) * 1e6, dtype=torch.float32
            ),
            "liquidity": torch.tensor(
                np.abs(np.random.randn(n_tokens, n_bars)) * 1e6, dtype=torch.float32
            ),
            "fdv": torch.tensor(
                np.abs(np.random.randn(n_tokens, n_bars)) * 1e8, dtype=torch.float32
            ),
        }

        fe = FeatureEngineer()
        self.feat_tensor = fe.compute_features(self.raw_data_cache)
        self.target_ret = torch.log(
            self.raw_data_cache["close"]
            / (torch.roll(self.raw_data_cache["close"], 1, dims=1) + 1e-9)
        )
        logger.info("Created synthetic data: %d tokens × %d bars", n_tokens, n_bars)

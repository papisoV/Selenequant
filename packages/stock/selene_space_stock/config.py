"""Pydantic-settings based configuration for selene-space-stock.

Replaces the frozen dataclass ``AppConfig`` from ai-market-pulse with a
``StockConfig`` that reads from environment variables (prefix ``SELENE_STOCK_``)
and/or YAML files.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

from selene_space_types.stock import NotificationTarget, StockAsset


# ---------------------------------------------------------------------------
# Environment variable expansion
# ---------------------------------------------------------------------------

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: os.getenv(match.group(1), match.group(2) or ""), value)
    return value


# ---------------------------------------------------------------------------
# Nested settings models
# ---------------------------------------------------------------------------


class LLMSettings(BaseModel):
    """LLM integration settings."""

    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    model: str | None = None
    temperature: float = 0.2
    timeout_seconds: int = 45
    cache_enabled: bool = True
    cache_dir: str = "data/ai-cache"
    prompts_dir: str | None = None


class NewsSettings(BaseModel):
    """News fetching settings."""

    enabled: bool = True
    items_per_asset: int = 5
    language: str = "en-US"
    region: str = "US"
    lookback_days: int = 2


class AnalysisSettings(BaseModel):
    """Analysis parameters."""

    lookback_days: int = 220
    language: str = "zh-CN"
    risk_free_rate: float = 0.02
    min_history_rows: int = 45


class DataSettings(BaseModel):
    """Data provider settings."""

    providers: list[str] = Field(default_factory=lambda: ["akshare", "yfinance"])


class BenchmarkSettings(BaseModel):
    """Benchmark index settings."""

    enabled: bool = True
    symbols: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "000300.SS", "^HSI"])
    default_by_market: dict[str, str] = Field(
        default_factory=lambda: {
            "US": "SPY",
            "CN": "000300.SS",
            "HK": "^HSI",
            "CRYPTO": "BTC-USD",
        }
    )
    compare: dict[str, str] = Field(default_factory=dict)
    names: dict[str, str] = Field(
        default_factory=lambda: {
            "SPY": "S&P 500 ETF",
            "QQQ": "Nasdaq 100 ETF",
            "000300.SS": "CSI 300",
            "399300.SZ": "CSI 300",
            "^HSI": "Hang Seng Index",
            "BTC-USD": "Bitcoin",
        }
    )
    stale_after_days: int = 4


# ---------------------------------------------------------------------------
# Main config
# ---------------------------------------------------------------------------


class StockConfig(BaseSettings):
    """Root configuration for selene-space-stock.

    Reads from environment variables with prefix ``SELENE_STOCK_`` (e.g.
    ``SELENE_STOCK_TITLE``, ``SELENE_STOCK_TIMEZONE``) and/or from a YAML
    file via ``load_stock_config()``.
    """

    model_config = {"env_prefix": "SELENE_STOCK_"}

    title: str = "Stock Market Pulse"
    timezone: str = "Asia/Shanghai"
    assets: list[StockAsset] = Field(default_factory=list)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    data: DataSettings = Field(default_factory=DataSettings)
    benchmarks: BenchmarkSettings = Field(default_factory=BenchmarkSettings)
    news: NewsSettings = Field(default_factory=NewsSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    notifications: list[NotificationTarget] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------


def load_stock_config(path: str | Path) -> StockConfig:
    """Load a ``StockConfig`` from a YAML file.

    Environment variable placeholders of the form ``${VAR}`` or
    ``${VAR:-default}`` are expanded before parsing.
    """
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return _config_from_mapping(raw)


def _config_from_mapping(raw: dict[str, Any]) -> StockConfig:
    raw = _expand_env(raw)
    assets = [_parse_asset(item) for item in raw.get("assets", [])]

    analysis_raw = raw.get("analysis", {}) or {}
    data_raw = raw.get("data", {}) or {}
    benchmarks_raw = raw.get("benchmarks", {}) or {}
    news_raw = raw.get("news", {}) or {}
    llm_raw = raw.get("llm", {}) or {}

    return StockConfig(
        title=raw.get("title", "Stock Market Pulse"),
        timezone=raw.get("timezone", "Asia/Shanghai"),
        assets=assets,
        analysis=AnalysisSettings(**_filter_model(AnalysisSettings, analysis_raw)),
        data=DataSettings(**_filter_model(DataSettings, data_raw)),
        benchmarks=BenchmarkSettings(**_filter_model(BenchmarkSettings, benchmarks_raw)),
        news=NewsSettings(**_filter_model(NewsSettings, news_raw)),
        llm=LLMSettings(**_filter_model(LLMSettings, llm_raw)),
        notifications=[
            NotificationTarget(
                type=item["type"],
                name=item.get("name", ""),
                enabled=item.get("enabled", True),
                settings={k: v for k, v in item.items() if k not in {"type", "name", "enabled"}},
            )
            for item in raw.get("notifications", []) or []
        ],
    )


def _parse_asset(item: dict[str, Any] | str) -> StockAsset:
    if isinstance(item, str):
        return StockAsset(symbol=item)
    return StockAsset(
        symbol=item["symbol"],
        name=item.get("name"),
        market=item.get("market", "US"),
        currency=item.get("currency"),
        tags=list(item.get("tags", []) or []),
        quantity=item.get("quantity"),
        cost_basis=item.get("cost_basis"),
        note=item.get("note"),
    )


def _filter_model(cls: type[BaseModel], values: dict[str, Any]) -> dict[str, Any]:
    """Keep only keys that are valid fields for the given Pydantic model."""
    fields = set(cls.model_fields.keys())
    return {key: value for key, value in values.items() if key in fields}

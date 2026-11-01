from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import Asset


ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")


@dataclass(frozen=True)
class LLMSettings:
    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    model: str | None = None
    temperature: float = 0.2
    timeout_seconds: int = 45
    cache_enabled: bool = True
    cache_dir: str = "data/ai-cache"
    prompts_dir: str | None = None


@dataclass(frozen=True)
class NewsSettings:
    enabled: bool = True
    items_per_asset: int = 5
    language: str = "en-US"
    region: str = "US"
    lookback_days: int = 2


@dataclass(frozen=True)
class AnalysisSettings:
    lookback_days: int = 220
    language: str = "zh-CN"
    risk_free_rate: float = 0.02
    min_history_rows: int = 45


@dataclass(frozen=True)
class DataSettings:
    providers: list[str] = field(default_factory=lambda: ["akshare", "yfinance"])


@dataclass(frozen=True)
class BenchmarkSettings:
    enabled: bool = True
    symbols: list[str] = field(default_factory=lambda: ["SPY", "QQQ", "000300.SS", "^HSI"])
    default_by_market: dict[str, str] = field(
        default_factory=lambda: {
            "US": "SPY",
            "CN": "000300.SS",
            "HK": "^HSI",
            "CRYPTO": "BTC-USD",
        }
    )
    compare: dict[str, str] = field(default_factory=dict)
    names: dict[str, str] = field(
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


@dataclass(frozen=True)
class NotificationTarget:
    type: str
    name: str | None = None
    enabled: bool = True
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AppConfig:
    title: str
    timezone: str
    assets: list[Asset]
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    data: DataSettings = field(default_factory=DataSettings)
    benchmarks: BenchmarkSettings = field(default_factory=BenchmarkSettings)
    news: NewsSettings = field(default_factory=NewsSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    notifications: list[NotificationTarget] = field(default_factory=list)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return load_config_from_mapping(raw)


def load_config_from_mapping(raw: dict[str, Any]) -> AppConfig:
    raw = _expand_env(raw)
    assets = [_parse_asset(item) for item in raw.get("assets", [])]
    if not assets:
        raise ValueError("Config must define at least one asset.")

    analysis_raw = raw.get("analysis", {}) or {}
    data_raw = raw.get("data", {}) or {}
    benchmarks_raw = raw.get("benchmarks", {}) or {}
    news_raw = raw.get("news", {}) or {}
    llm_raw = raw.get("llm", {}) or {}

    return AppConfig(
        title=raw.get("title", "AI Market Pulse"),
        timezone=raw.get("timezone", "America/Los_Angeles"),
        assets=assets,
        analysis=AnalysisSettings(**_filter_dataclass(AnalysisSettings, analysis_raw)),
        data=DataSettings(**_filter_dataclass(DataSettings, data_raw)),
        benchmarks=BenchmarkSettings(**_filter_dataclass(BenchmarkSettings, benchmarks_raw)),
        news=NewsSettings(**_filter_dataclass(NewsSettings, news_raw)),
        llm=LLMSettings(**_filter_dataclass(LLMSettings, llm_raw)),
        notifications=[
            NotificationTarget(
                type=item["type"],
                name=item.get("name"),
                enabled=item.get("enabled", True),
                settings={k: v for k, v in item.items() if k not in {"type", "name", "enabled"}},
            )
            for item in raw.get("notifications", []) or []
        ],
    )


def _parse_asset(item: dict[str, Any] | str) -> Asset:
    if isinstance(item, str):
        return Asset(symbol=item)
    return Asset(
        symbol=item["symbol"],
        name=item.get("name"),
        market=item.get("market", "US"),
        currency=item.get("currency"),
        tags=list(item.get("tags", []) or []),
        quantity=item.get("quantity"),
        cost_basis=item.get("cost_basis"),
        note=item.get("note"),
    )


def _filter_dataclass(cls: type, values: dict[str, Any]) -> dict[str, Any]:
    fields = set(cls.__dataclass_fields__.keys())
    return {key: value for key, value in values.items() if key in fields}


def _expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, str):
        return ENV_PATTERN.sub(lambda match: os.getenv(match.group(1), match.group(2) or ""), value)
    return value

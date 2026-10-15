from __future__ import annotations

import re

import yaml


DEFAULT_CONFIG = """# AI Market Pulse starter config
title: "My AI Market Pulse"
timezone: "America/Los_Angeles"

analysis:
  language: "zh-CN"
  lookback_days: 220
  min_history_rows: 45

data:
  providers: ["akshare", "yfinance"]

benchmarks:
  enabled: true
  symbols: ["SPY", "QQQ", "000300.SS", "^HSI"]
  default_by_market:
    US: "SPY"
    CN: "000300.SS"
    HK: "^HSI"
    CRYPTO: "BTC-USD"
  compare:
    AAPL: "QQQ"
    NVDA: "QQQ"
  stale_after_days: 4

news:
  enabled: true
  items_per_asset: 4
  language: "zh-CN"
  region: "CN"
  lookback_days: 2

llm:
  enabled: false
  base_url: "${OPENAI_BASE_URL:-https://api.openai.com/v1}"
  api_key_env: "OPENAI_API_KEY"
  model: "${OPENAI_MODEL:-}"
  temperature: 0.2

assets:
  - symbol: "AAPL"
    name: "Apple"
    market: "US"
    quantity: 10
    cost_basis: 185
    tags: ["mega-cap", "consumer-tech"]
  - symbol: "NVDA"
    name: "NVIDIA"
    market: "US"
    quantity: 6
    cost_basis: 140
    tags: ["ai", "semiconductor"]
  - symbol: "SPY"
    name: "S&P 500 ETF"
    market: "US"
    tags: ["index"]
  - symbol: "600519.SS"
    name: "Kweichow Moutai"
    market: "CN"
    tags: ["a-share"]

notifications:
  # Generic webhook. Works for many internal bots.
  - type: "webhook"
    name: "internal-bot"
    enabled: false
    url_env: "MARKET_PULSE_WEBHOOK_URL"

  # Telegram bot.
  - type: "telegram"
    name: "telegram"
    enabled: false
    token_env: "TELEGRAM_BOT_TOKEN"
    chat_id_env: "TELEGRAM_CHAT_ID"

  # Feishu custom bot.
  - type: "feishu"
    name: "feishu"
    enabled: false
    url_env: "FEISHU_WEBHOOK_URL"

  # WeCom group bot.
  - type: "wecom"
    name: "wecom"
    enabled: false
    url_env: "WECOM_WEBHOOK_URL"
"""


US_TECH_CONFIG = """# AI Market Pulse US tech template
title: "US Tech Market Pulse"
timezone: "America/Los_Angeles"

analysis:
  language: "zh-CN"
  lookback_days: 220
  min_history_rows: 45

data:
  providers: ["yfinance"]

benchmarks:
  enabled: true
  symbols: ["SPY", "QQQ"]
  default_by_market:
    US: "SPY"
  compare:
    AAPL: "QQQ"
    NVDA: "QQQ"
    MSFT: "QQQ"
    GOOGL: "QQQ"
    AMZN: "QQQ"
    META: "QQQ"
    TSLA: "QQQ"
  stale_after_days: 4

news:
  enabled: true
  items_per_asset: 4
  language: "zh-CN"
  region: "CN"
  lookback_days: 2

llm:
  enabled: false
  base_url: "${OPENAI_BASE_URL:-https://api.openai.com/v1}"
  api_key_env: "OPENAI_API_KEY"
  model: "${OPENAI_MODEL:-}"
  temperature: 0.2

assets:
  - symbol: "AAPL"
    name: "Apple"
    market: "US"
    tags: ["mega-cap", "consumer-tech"]
  - symbol: "NVDA"
    name: "NVIDIA"
    market: "US"
    tags: ["ai", "semiconductor"]
  - symbol: "MSFT"
    name: "Microsoft"
    market: "US"
    tags: ["cloud", "ai"]
  - symbol: "GOOGL"
    name: "Alphabet"
    market: "US"
    tags: ["search", "ai"]
  - symbol: "AMZN"
    name: "Amazon"
    market: "US"
    tags: ["cloud", "commerce"]
  - symbol: "META"
    name: "Meta"
    market: "US"
    tags: ["ads", "ai"]
  - symbol: "TSLA"
    name: "Tesla"
    market: "US"
    tags: ["ev", "robotics"]
  - symbol: "QQQ"
    name: "Nasdaq 100 ETF"
    market: "US"
    tags: ["index"]

notifications: []
"""


CN_STOCK_CONFIG = """# AI Market Pulse China A-share template
title: "China Stock Market Pulse"
timezone: "Asia/Shanghai"

analysis:
  language: "zh-CN"
  lookback_days: 220
  min_history_rows: 45

data:
  providers: ["akshare", "yfinance"]

benchmarks:
  enabled: true
  symbols: ["000300.SS", "399300.SZ", "^HSI"]
  default_by_market:
    CN: "000300.SS"
    HK: "^HSI"
  stale_after_days: 4

news:
  enabled: true
  items_per_asset: 4
  language: "zh-CN"
  region: "CN"
  lookback_days: 2

llm:
  enabled: false
  base_url: "${OPENAI_BASE_URL:-https://api.openai.com/v1}"
  api_key_env: "OPENAI_API_KEY"
  model: "${OPENAI_MODEL:-}"
  temperature: 0.2

assets:
  - symbol: "600519.SS"
    name: "Kweichow Moutai"
    market: "CN"
    tags: ["a-share", "consumer"]
  - symbol: "300750.SZ"
    name: "CATL"
    market: "CN"
    tags: ["a-share", "battery"]
  - symbol: "000001.SZ"
    name: "Ping An Bank"
    market: "CN"
    tags: ["a-share", "bank"]
  - symbol: "510300.SS"
    name: "CSI 300 ETF"
    market: "CN"
    tags: ["etf", "index"]

notifications: []
"""


CRYPTO_CONFIG = """# AI Market Pulse crypto template
title: "Crypto Market Pulse"
timezone: "America/Los_Angeles"

analysis:
  language: "zh-CN"
  lookback_days: 220
  min_history_rows: 45

data:
  providers: ["yfinance"]

benchmarks:
  enabled: true
  symbols: ["BTC-USD", "ETH-USD"]
  default_by_market:
    CRYPTO: "BTC-USD"
  stale_after_days: 4

news:
  enabled: true
  items_per_asset: 4
  language: "zh-CN"
  region: "CN"
  lookback_days: 2

llm:
  enabled: false
  base_url: "${OPENAI_BASE_URL:-https://api.openai.com/v1}"
  api_key_env: "OPENAI_API_KEY"
  model: "${OPENAI_MODEL:-}"
  temperature: 0.2

assets:
  - symbol: "BTC-USD"
    name: "Bitcoin"
    market: "CRYPTO"
    tags: ["crypto", "large-cap"]
  - symbol: "ETH-USD"
    name: "Ethereum"
    market: "CRYPTO"
    tags: ["crypto", "smart-contract"]
  - symbol: "SOL-USD"
    name: "Solana"
    market: "CRYPTO"
    tags: ["crypto", "high-beta"]

notifications: []
"""


SAMPLE_CONFIGS = {
    "default": DEFAULT_CONFIG,
    "us-tech": US_TECH_CONFIG,
    "cn-stock": CN_STOCK_CONFIG,
    "crypto": CRYPTO_CONFIG,
}

SAMPLE_CONFIG = SAMPLE_CONFIGS["default"]


def custom_watchlist_config(
    symbols: list[str],
    title: str = "My AI Market Pulse",
    timezone: str = "America/Los_Angeles",
    language: str = "zh-CN",
    providers: list[str] | None = None,
    telegram_token: str | None = None,
    telegram_chat_id: str | None = None,
    feishu_webhook: str | None = None,
) -> str:
    cleaned = [_normalize_symbol(symbol) for symbol in symbols if symbol.strip()]
    if not cleaned:
        raise ValueError("At least one symbol is required.")

    config = {
        "title": title,
        "timezone": timezone,
        "analysis": {
            "language": language,
            "lookback_days": 220,
            "min_history_rows": 45,
        },
        "data": {
            "providers": providers or ["yfinance"],
        },
        "benchmarks": {
            "enabled": True,
            "symbols": ["SPY", "QQQ", "000300.SS", "^HSI", "BTC-USD"],
            "default_by_market": {
                "US": "SPY",
                "CN": "000300.SS",
                "HK": "^HSI",
                "CRYPTO": "BTC-USD",
            },
            "compare": _benchmark_overrides(cleaned),
            "stale_after_days": 4,
        },
        "news": {
            "enabled": True,
            "items_per_asset": 4,
            "language": language,
            "region": "CN" if language.startswith("zh") else "US",
            "lookback_days": 2,
        },
        "llm": {
            "enabled": False,
            "base_url": "${OPENAI_BASE_URL:-https://api.openai.com/v1}",
            "api_key_env": "OPENAI_API_KEY",
            "model": "${OPENAI_MODEL:-}",
            "temperature": 0.2,
            "cache_enabled": True,
            "cache_dir": "data/ai-cache",
            "prompts_dir": "prompts",
        },
        "assets": [
            {
                "symbol": symbol,
                "market": _infer_market(symbol),
            }
            for symbol in cleaned
        ],
        "notifications": _notification_targets(telegram_token, telegram_chat_id, feishu_webhook),
    }
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)


def _notification_targets(
    telegram_token: str | None,
    telegram_chat_id: str | None,
    feishu_webhook: str | None,
) -> list[dict]:
    targets: list[dict] = []
    if telegram_token and telegram_chat_id:
        targets.append(
            {
                "type": "telegram",
                "name": "telegram",
                "enabled": True,
                "token_env": "TELEGRAM_BOT_TOKEN",
                "chat_id_env": "TELEGRAM_CHAT_ID",
            }
        )
    if feishu_webhook:
        targets.append(
            {
                "type": "feishu",
                "name": "feishu",
                "enabled": True,
                "url_env": "FEISHU_WEBHOOK_URL",
            }
        )
    return targets


def parse_symbols(value: str) -> list[str]:
    return [symbol.strip() for symbol in re.split(r"[,，\s]+", value) if symbol.strip()]


def _benchmark_overrides(symbols: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for symbol in symbols:
        market = _infer_market(symbol)
        if market == "US" and symbol.upper() not in {"SPY", "QQQ"}:
            overrides[symbol] = "QQQ"
    return overrides


def _normalize_symbol(symbol: str) -> str:
    text = symbol.strip().upper()
    if re.fullmatch(r"\d{1,6}", text):
        padded = f"{int(text):06d}"
        if padded.startswith(("6", "5", "9")):
            return f"{padded}.SS"
        return f"{padded}.SZ"
    return text


def _infer_market(symbol: str) -> str:
    text = symbol.strip().upper()
    if text.endswith((".SS", ".SZ")) or re.fullmatch(r"\d{6}", text):
        return "CN"
    if text.endswith(".HK"):
        return "HK"
    if text.endswith("-USD"):
        return "CRYPTO"
    return "US"

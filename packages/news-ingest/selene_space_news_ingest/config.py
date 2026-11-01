"""news-ingest 配置(env-first)。

selene 化改造点(vs binance-quant):
- 全部配置走 SELENE_NI_* env 前缀,带安全默认值,不依赖 config.yaml
- 绝不写 os.environ 全局代理(原项目 config.py 设置全局 HTTP_PROXY 后,
  news 模块被迫 trust_env=False 对抗自己 config 的污染——这里只把 proxy
  作为显式参数传递)
- DB 默认 data/news.db,与 tgwatcher.db 同级独立(.gitignore data/*.db 已覆盖)
"""

from __future__ import annotations

import os
from pathlib import Path

_BASE_DIR = (
    Path(__file__).resolve().parents[3]
)  # repo root (packages/news-ingest/selene_space_news_ingest 上数 3 层)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


NEWS_DB_PATH = Path(os.getenv("SELENE_NI_DB_PATH") or (_BASE_DIR / "data" / "news.db"))

NEWS_RSS_URLS = [
    u.strip()
    for u in os.getenv(
        "SELENE_NI_RSS_URLS",
        "https://www.panewslab.com/rss.xml?lang=en,"
        "https://www.panewslab.com/rss.xml?lang=zh",
    ).split(",")
    if u.strip()
]

# 逃生口: 部署环境需要代理时显式传入,不污染全局环境变量
NEWS_HTTP_PROXY = os.getenv("SELENE_NI_HTTP_PROXY", "")

NEWS_POLL_INTERVAL = _env_int("SELENE_NI_POLL_INTERVAL", 420)
TAGGER_POLL_INTERVAL = _env_int("SELENE_NI_TAGGER_POLL_INTERVAL", 30)
DETECTOR_POLL_INTERVAL = _env_int("SELENE_NI_DETECTOR_POLL_INTERVAL", 60)

LLM_BASE_URL = os.getenv("SELENE_NI_LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_API_KEY = os.getenv("SELENE_NI_LLM_API_KEY", "")
LLM_MODEL = os.getenv("SELENE_NI_LLM_MODEL", "deepseek-chat")

SYMBOL_WHITELIST = [
    s.strip().upper()
    for s in os.getenv("SELENE_NI_SYMBOL_WHITELIST", "BTC,ETH,BNB,SOL,XRP").split(",")
    if s.strip()
]

ANOMALY_BASELINE_PERIODS = _env_int("SELENE_NI_ANOMALY_BASELINE_PERIODS", 20)
ANOMALY_RATIO_THRESHOLD = _env_float("SELENE_NI_ANOMALY_RATIO_THRESHOLD", 3.0)
ANOMALY_WINDOW_MINUTES = _env_int("SELENE_NI_ANOMALY_WINDOW_MINUTES", 5)

# Binance 1m K线直连(detector 专用)
BINANCE_KLINES_URL = os.getenv(
    "SELENE_NI_BINANCE_KLINES_URL", "https://fapi.binance.com/fapi/v1/klines"
)

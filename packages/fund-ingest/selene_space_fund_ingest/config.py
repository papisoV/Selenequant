"""fund-ingest 配置(env-first,SELENE_FI_* 前缀)。

模式复刻 news-ingest config.py:env-first + 安全默认值,不依赖 yaml;
DB 与 tgwatcher.db/news.db 同级独立(data/*.db 已被 .gitignore 覆盖)。
"""

from __future__ import annotations

import os
from pathlib import Path

_BASE_DIR = (
    Path(__file__).resolve().parents[3]
)  # repo root (packages/fund-ingest/selene_space_fund_ingest 上数 3 层)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


FUND_DB_PATH = Path(
    os.getenv("SELENE_FI_DB_PATH") or (_BASE_DIR / "data" / "fund_ingest.db")
)

#: LOF/ETF 溢价率告警阈值(%),abs() 判定 — stock 项目 TARGET_PREMIUM=4 的策略形态
LOF_PREMIUM_THRESHOLD = _env_float("SELENE_FI_LOF_PREMIUM_THRESHOLD", 4.0)

#: 采集失败退化为空表前的重试次数
FETCH_RETRIES = 2

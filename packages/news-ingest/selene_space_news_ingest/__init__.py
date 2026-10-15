"""selene_space_news_ingest — RSS 新闻采集 + LLM 打标 + 成交量异动回验。

移植自作者本人既有项目 binance-quant 的 news 模块(2026-09-03, 无第三方许可问题)。四环:
news_collector (RSS 采集) -> news_store (崩溃安全 claim 队列) ->
news_tagger (LLM 打标) -> anomaly_detector (成交量异动回验)。
"""

from selene_space_news_ingest import config
from selene_space_news_ingest.news_store import NewsStore, get_news_store

__all__ = ["config", "NewsStore", "get_news_store"]

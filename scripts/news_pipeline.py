"""news-ingest 独立监听入口: 起 collector/tagger/detector 三循环(不经 local-api)。

与 scripts/news_listen.py(tg-ingest NewsFetcher 链,走 TG SignalEngine)互不
相关——那条链管 TG 信号形态的 RSS;本脚本管 binance-quant 吸收的
claim 队列 + LLM 打标 + 成交量异动回验管线。

用法: call activate.bat && python scripts/news_pipeline.py
按 Ctrl+C 退出(全部循环经 asyncio.gather cancel 干净收尾)。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "packages" / "news-ingest")
)

from selene_space_news_ingest import anomaly_detector, news_collector, news_tagger  # noqa: E402
from selene_space_news_ingest.news_store import get_news_store  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("news_pipeline")


async def main() -> None:
    get_news_store()  # 触发建库
    logger.info("news-ingest pipeline: collector + tagger + detector")
    await asyncio.gather(
        news_collector.poll_loop(),
        news_tagger.tagger_loop(),
        anomaly_detector.detector_loop(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("interrupted, exiting")

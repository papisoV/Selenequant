"""tg_crawl — scheduled Telegram group crawler.

Usage:
    python scripts/tg_crawl.py [--config path/to/config.yaml]

Modes (in config.yaml):
    crawl.mode: incremental (default) | full | date_range
    crawl.limit: max messages per group per iteration
    crawl.interval_minutes: pause between iterations (incremental only)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "tg-ingest"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tg_crawl")


async def cmd_crawl(config):
    from selene_space_tg_ingest.client import TGClient
    from selene_space_tg_ingest.storage import Storage
    from selene_space_tg_ingest.tz_utils import local_to_utc, set_tz_offset

    runtime = config.to_runtime_dict()
    set_tz_offset(config.utc_offset_hours)

    groups = runtime["groups"]
    if not groups:
        logger.error("No groups configured. Edit config.yaml and add groups.")
        return

    crawl_cfg = runtime.get("crawl", {})
    mode = crawl_cfg.get("mode", "incremental")
    limit = crawl_cfg.get("limit", 100)
    interval = crawl_cfg.get("interval_minutes", 30)

    offset_date = None
    until_date = None
    if crawl_cfg.get("offset_date"):
        offset_date = local_to_utc(datetime.fromisoformat(crawl_cfg["offset_date"])).replace(tzinfo=timezone.utc)
    if crawl_cfg.get("until_date"):
        until_local = datetime.fromisoformat(crawl_cfg["until_date"]).replace(hour=23, minute=59, second=59, microsecond=999999)
        until_date = local_to_utc(until_local).replace(tzinfo=timezone.utc)

    storage = Storage(db_path=config.db_path)
    storage.init_db()

    tg = TGClient(runtime)
    await tg.connect()

    stop_event = asyncio.Event()

    def _signal_handler(sig, frame):
        logger.info("Received stop signal, finishing current crawl...")
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        iteration = 0
        while not stop_event.is_set():
            iteration += 1
            logger.info("=== Crawl iteration #%d | mode=%s | at %s ===",
                        iteration, mode, datetime.now().strftime("%H:%M:%S"))

            for group in groups:
                if stop_event.is_set():
                    break

                chat_id = group.get("id") or group.get("username")
                group_name = group.get("name", chat_id)
                if not chat_id:
                    logger.warning("Skipping group with no id/username: %s", group)
                    continue

                if mode == "full":
                    min_id = 0
                    logger.info("Crawling %s (full history, limit=%d)", group_name, limit)
                elif mode == "date_range":
                    min_id = 0
                    logger.info("Crawling %s (date range: %s ~ %s)", group_name,
                                offset_date or "earliest", until_date or "now")
                else:
                    last_id = storage.get_last_message_id(
                        chat_id if isinstance(chat_id, int) else 0
                    )
                    min_id = last_id if last_id else 0
                    if last_id:
                        logger.info("Crawling %s (incremental from msg %d)", group_name, last_id)
                    else:
                        logger.info("Crawling %s (first run, full history, limit=%d)", group_name, limit)

                try:
                    messages = await tg.fetch_messages(
                        chat_id=chat_id,
                        limit=limit,
                        min_id=min_id,
                        offset_date=offset_date if mode == "date_range" else None,
                        until_date=until_date if mode == "date_range" else None,
                    )
                    if messages:
                        saved = storage.save_messages(messages)
                        logger.info("Saved %d/%d messages from %s", saved, len(messages), group_name)
                    else:
                        logger.info("No new messages from %s", group_name)
                except Exception as e:
                    logger.error("Error crawling %s: %s", group_name, e)

            if mode != "incremental":
                logger.info("One-time crawl (%s) complete.", mode)
                break

            if stop_event.is_set():
                break

            logger.info("Crawl complete. Next crawl in %d minutes.", interval)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval * 60)
            except asyncio.TimeoutError:
                pass
    finally:
        await tg.disconnect()
        logger.info("tg_crawl stopped.")


def main():
    parser = argparse.ArgumentParser(description="tg_crawl — scheduled Telegram crawler")
    parser.add_argument("--config", type=str, default=None, help="Path to config.yaml")
    args = parser.parse_args()

    from selene_space_tg_ingest.config import TgIngestConfig
    config = TgIngestConfig.load(args.config)
    asyncio.run(cmd_crawl(config))


if __name__ == "__main__":
    main()

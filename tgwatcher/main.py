"""TGWatcher — Telegram group message crawler.

Usage:
    python main.py --login       First-time login (saves session)
    python main.py --list        List joined groups/channels
    python main.py --stats       Show crawl statistics
    python main.py               Start scheduled crawling
    python main.py --listen      Start real-time listener (reserved)
"""
import argparse
import asyncio
import logging
import os
import signal
from datetime import datetime, timezone
from pathlib import Path

import yaml

from tgwatcher.client import TGClient
from tgwatcher.storage import Storage
from tgwatcher.listener import start_listener

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tgwatcher")

CONFIG_PATH = str(Path.cwd() / "config.yaml")


def _resolve_config_path(cli_path: str | None = None) -> str:
    if cli_path:
        return cli_path
    env_path = os.environ.get("TGWATCHER_CONFIG")
    if env_path:
        return env_path
    return CONFIG_PATH


def load_config(path: str | Path = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def cmd_login(config: dict) -> None:
    tg = TGClient(config)
    await tg.connect()
    logger.info("Session saved. You can now run crawling or listing.")
    await tg.disconnect()


async def cmd_list(config: dict) -> None:
    tg = TGClient(config)
    await tg.connect()
    try:
        dialogs = await tg.list_dialogs()
        if not dialogs:
            logger.info("No groups or channels found.")
            return
        print(f"\n{'ID':<16} {'Type':<8} {'Members':>8} {'Username':<20} {'Title'}")
        print("-" * 80)
        for d in dialogs:
            kind = "Channel" if d["is_channel"] else "Group"
            members = d.get("members") or "-"
            username = d.get("username") or "-"
            print(f"{d['id']:<16} {kind:<8} {str(members):>8} {username:<20} {d['title']}")
        print(f"\nTotal: {len(dialogs)} groups/channels")
    finally:
        await tg.disconnect()


async def cmd_stats(config: dict) -> None:
    db_path = config["storage"]["db_path"]
    storage = Storage(db_path)
    storage.init_db()
    stats = storage.get_stats()
    print("\n=== TGWatcher Statistics ===")
    print(f"  Total messages:    {stats['total_messages']}")
    print(f"  Monitored chats:   {stats['monitored_chats']}")
    print(f"  Earliest message:  {stats['earliest_message']}")
    print(f"  Latest message:    {stats['latest_message']}")
    print()


async def cmd_crawl(config: dict) -> None:
    groups = config.get("groups", [])
    if not groups:
        logger.error("No groups configured. Edit config.yaml and add groups.")
        return

    crawl_cfg = config.get("crawl", {})
    mode = crawl_cfg.get("mode", "incremental")
    limit = crawl_cfg.get("limit", 100)
    interval = crawl_cfg.get("interval_minutes", 30)

    offset_date = None
    until_date = None
    from tgwatcher.tz_utils import local_to_utc
    if crawl_cfg.get("offset_date"):
        offset_date = local_to_utc(datetime.fromisoformat(crawl_cfg["offset_date"])).replace(tzinfo=timezone.utc)
    if crawl_cfg.get("until_date"):
        until_local = datetime.fromisoformat(crawl_cfg["until_date"]).replace(hour=23, minute=59, second=59, microsecond=999999)
        until_date = local_to_utc(until_local).replace(tzinfo=timezone.utc)

    db_path = config["storage"]["db_path"]
    storage = Storage(db_path)
    storage.init_db()

    tg = TGClient(config)
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
                else:  # incremental
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
        logger.info("TGWatcher stopped.")


async def cmd_listen(config: dict) -> None:
    groups = config.get("groups", [])
    if not groups:
        logger.error("No groups configured. Edit config.yaml and add groups.")
        return

    db_path = config["storage"]["db_path"]
    storage = Storage(db_path)
    storage.init_db()

    tg = TGClient(config)
    await tg.connect()

    try:
        await start_listener(tg, storage, groups)
    finally:
        await tg.disconnect()


def main():
    parser = argparse.ArgumentParser(description="TGWatcher - Telegram group message crawler")
    parser.add_argument("--login", action="store_true", help="First-time login (saves session)")
    parser.add_argument("--list", action="store_true", help="List joined groups/channels")
    parser.add_argument("--stats", action="store_true", help="Show crawl statistics")
    parser.add_argument("--listen", action="store_true", help="Start real-time listener")
    parser.add_argument("--config", type=str, default=None, help="Path to config.yaml")
    args = parser.parse_args()

    config_path = _resolve_config_path(args.config)
    config = load_config(config_path)

    from tgwatcher.tz_utils import set_tz_offset
    set_tz_offset(config.get("timezone", {}).get("utc_offset_hours", 8))

    if args.login:
        asyncio.run(cmd_login(config))
    elif args.list:
        asyncio.run(cmd_list(config))
    elif args.stats:
        asyncio.run(cmd_stats(config))
    elif args.listen:
        asyncio.run(cmd_listen(config))
    else:
        asyncio.run(cmd_crawl(config))


if __name__ == "__main__":
    main()

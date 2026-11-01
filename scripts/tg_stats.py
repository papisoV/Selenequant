"""tg_stats — print tgwatcher.db statistics.

Usage:
    python scripts/tg_stats.py [--config path/to/config.yaml]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "tg-ingest"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tg_stats")


async def cmd_stats(config):
    from selene_space_tg_ingest.storage import Storage
    storage = Storage(db_path=config.db_path)
    storage.init_db()
    stats = storage.get_stats()
    print("\n=== tg_ingest Statistics ===")
    print(f"  Total messages:    {stats['total_messages']}")
    print(f"  Monitored chats:   {stats['monitored_chats']}")
    print(f"  Earliest message:  {stats['earliest_message']}")
    print(f"  Latest message:    {stats['latest_message']}")
    print()


def main():
    parser = argparse.ArgumentParser(description="tg_stats — print tgwatcher.db stats")
    parser.add_argument("--config", type=str, default=None, help="Path to config.yaml")
    args = parser.parse_args()

    from selene_space_tg_ingest.config import TgIngestConfig
    config = TgIngestConfig.load(args.config)
    asyncio.run(cmd_stats(config))


if __name__ == "__main__":
    main()

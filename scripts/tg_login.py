"""tg_login — first-time Telegram login (saves session).

Usage:
    python scripts/tg_login.py [--config path/to/config.yaml]
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
logger = logging.getLogger("tg_login")


async def cmd_login(config):
    from selene_space_tg_ingest.client import TGClient
    runtime = config.to_runtime_dict()
    tg = TGClient(runtime)
    await tg.connect()
    logger.info("Session saved. You can now run crawling or listening.")
    await tg.disconnect()


def main():
    parser = argparse.ArgumentParser(description="tg_login — first-time Telegram login")
    parser.add_argument("--config", type=str, default=None, help="Path to config.yaml")
    args = parser.parse_args()

    from selene_space_tg_ingest.config import TgIngestConfig
    config = TgIngestConfig.load(args.config)
    asyncio.run(cmd_login(config))


if __name__ == "__main__":
    main()

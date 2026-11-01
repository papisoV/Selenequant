"""tg_listen — start real-time Telegram listener with optional EventGuard hook.

Usage:
    python scripts/tg_listen.py [--config path/to/config.yaml] [--macro-db path/to/macro_events.db]

Reads TgIngestConfig (env SELENE_TG_* overrides + yaml fallback).
On each completed/skipped signal_factor, fires on_signal_factor callback.

If --macro-db is provided (or SELENE_TG_MACRO_DB env is set), the callback
ingests the message text into macro_events.db.tg_events — same path as the
offline scripts/tg_ingest_to_events.py, but in real time. The bridge lives in
selene_space_tg_ingest.eventguard_hook.build_eventguard_hook (shared with
news_listen.py — single source of truth, see its docstring).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "tg-ingest"))
sys.path.insert(0, str(REPO / "packages" / "alpha-core"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tg_listen")


async def cmd_listen(config: Any, macro_db: Path | None) -> None:
    from selene_space_tg_ingest.client import TGClient
    from selene_space_tg_ingest.eventguard_hook import build_eventguard_hook
    from selene_space_tg_ingest.listener import start_listener
    from selene_space_tg_ingest.signal_dedup import SignalDeduper
    from selene_space_tg_ingest.signal_engine import SignalEngine
    from selene_space_tg_ingest.signal_filter import KeywordFilter
    from selene_space_tg_ingest.signal_llm import SignalLLMClient, LLMConfig
    from selene_space_tg_ingest.storage import Storage
    from selene_space_tg_ingest.tz_utils import set_tz_offset

    runtime = config.to_runtime_dict()
    set_tz_offset(config.utc_offset_hours)

    storage = Storage(db_path=config.db_path)
    storage.init_db()

    kw_filter = KeywordFilter(config={
        "min_text_length": config.filter_min_text_length,
        "keywords": config.keywords,
    })

    llm_client = None
    if config.llm_enabled:
        llm_client = SignalLLMClient(LLMConfig.from_dict(runtime["signal"]["llm"]))

    # Shared EventGuard bridge + dedup (dedup only guards an active downstream push).
    on_signal = build_eventguard_hook(macro_db) if macro_db else None
    deduper = SignalDeduper() if on_signal else None
    engine = SignalEngine(
        storage=storage,
        keyword_filter=kw_filter,
        llm=llm_client,
        config=runtime["signal"],
        on_signal_factor=on_signal,
        deduper=deduper,
    )

    tg = TGClient(runtime)
    await tg.connect()
    try:
        await start_listener(tg, storage, runtime["groups"], signal_engine=engine)
    finally:
        await tg.disconnect()


def main():
    parser = argparse.ArgumentParser(description="tg_listen — real-time Telegram listener with optional EventGuard hook")
    parser.add_argument("--config", type=str, default=None, help="Path to config.yaml (else env/CWD fallback)")
    parser.add_argument("--macro-db", type=str, default=None,
                        help="Path to macro_events.db. Enables real-time EventGuard ingestion. "
                             "Also via SELENE_TG_MACRO_DB env.")
    args = parser.parse_args()

    macro_db_env = os.environ.get("SELENE_TG_MACRO_DB")
    macro_db = Path(args.macro_db) if args.macro_db else (Path(macro_db_env) if macro_db_env else None)

    from selene_space_tg_ingest.config import TgIngestConfig
    config = TgIngestConfig.load(args.config)

    asyncio.run(cmd_listen(config, macro_db))


if __name__ == "__main__":
    main()

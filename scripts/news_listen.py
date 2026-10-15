"""News listen entrypoint — poll RSS feeds, feed to SignalEngine.

Mirrors tg_listen.py structure: argparse → config → Storage + SignalLLMClient
+ SignalEngine + NewsFetcher → run_forever.

Usage:
    python scripts/news_listen.py --config config.yaml
    python scripts/news_listen.py --macro-db data/macro_events.db  # enable EventGuard hook
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packages" / "tg-ingest"))
sys.path.insert(0, str(REPO / "packages" / "alpha-core"))

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Poll RSS news feeds and feed to SignalEngine")
    p.add_argument("--config", default=None, help="Path to config.yaml (default: CWD or package example)")
    p.add_argument("--macro-db", default=None, help="Optional macro_events.db path to enable EventGuard hook")
    p.add_argument("--poll-interval", type=int, default=None, help="Override poll interval seconds")
    p.add_argument("--dry-run", action="store_true", help="Fetch once and print, don't run LLM")
    p.add_argument("--once", action="store_true", help="Fetch once and exit (no loop)")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    args = parse_args()

    from selene_space_tg_ingest.config import TgIngestConfig
    from selene_space_tg_ingest.eventguard_hook import build_eventguard_hook
    from selene_space_tg_ingest.news_fetcher import NewsFetcher, load_news_config_from_env, _DEFAULT_SOURCES
    from selene_space_tg_ingest.signal_dedup import SignalDeduper
    from selene_space_tg_ingest.signal_engine import SignalEngine
    from selene_space_tg_ingest.signal_filter import KeywordFilter
    from selene_space_tg_ingest.signal_llm import LLMConfig, SignalLLMClient
    from selene_space_tg_ingest.storage import Storage

    cfg = TgIngestConfig.load(args.config)
    runtime = cfg.to_runtime_dict()

    storage = Storage(db_path=cfg.db_path)
    storage.init_db()

    kw_filter = KeywordFilter(config={
        "min_text_length": cfg.filter_min_text_length,
        "keywords": cfg.keywords,
    })

    llm_client = None
    if cfg.llm_enabled:
        llm_client = SignalLLMClient(LLMConfig.from_dict(runtime["signal"]["llm"]))

    news_cfg = load_news_config_from_env()
    if args.poll_interval is not None:
        news_cfg.poll_interval = args.poll_interval
    if args.dry_run or args.once:
        news_cfg.enabled = True

    fetcher = NewsFetcher(news_cfg)

    if args.dry_run:
        logger.info("Dry run: fetching once per source, printing normalized entries")
        for source in _DEFAULT_SOURCES:
            msgs = fetcher.fetch_once(source)
            print(f"\n=== {source.name} ({source.url}) — {len(msgs)} new entries ===")
            for m in msgs[:3]:
                print(f"  chat_id={m['chat_id']} msg_id={m['message_id']} text={m['text'][:80]}...")
        return 0

    if not news_cfg.enabled:
        logger.error("NewsFetcher disabled. Set SELENE_NEWS_ENABLED=1 or use --dry-run")
        return 1

    # Optional EventGuard hook + dedup (dedup guards the downstream push, so only
    # when a hook exists). Shared builder is the single source of truth.
    on_signal_fn = build_eventguard_hook(Path(args.macro_db)) if args.macro_db else None
    deduper = SignalDeduper() if on_signal_fn else None
    if on_signal_fn:
        logger.info("EventGuard hook enabled: %s", args.macro_db)

    engine = SignalEngine(
        storage=storage,
        keyword_filter=kw_filter,
        llm=llm_client,
        config=runtime["signal"],
        on_signal_factor=on_signal_fn,
        deduper=deduper,
    )
    logger.info("Starting NewsFetcher poll loop (interval=%ds, %d sources)",
                news_cfg.poll_interval, len(news_cfg.sources))

    # process_new_message (not process_message) so the engine-level
    # on_signal_factor emit + signal dedup apply — same path as tg_listen.
    if args.once:
        for source in _DEFAULT_SOURCES:
            msgs = fetcher.fetch_once(source)
            for msg in msgs:
                engine.process_new_message(msg)
        return 0

    fetcher.run_forever(engine.process_new_message)
    return 0


if __name__ == "__main__":
    sys.exit(main())

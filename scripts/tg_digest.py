"""tg_digest — generate/query LLM market digests from signal_factors.

Usage:
    python scripts/tg_digest.py [--config path/to/config.yaml]
    python scripts/tg_digest.py --lookback-hours 48
    python scripts/tg_digest.py --latest
    python scripts/tg_digest.py --list [--limit 10]
    python scripts/tg_digest.py --list --json
"""
from __future__ import annotations

import argparse
import json
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
logger = logging.getLogger("tg_digest")


def _load_storage_and_llm(config, need_llm: bool):
    from selene_space_tg_ingest.storage import Storage
    from selene_space_tg_ingest.signal_llm import LLMConfig, SignalLLMClient

    storage = Storage(db_path=str(config.db_path))
    storage.init_db()

    llm = None
    if need_llm:
        llm_cfg = config.to_runtime_dict()["signal"]["llm"]
        try:
            llm = SignalLLMClient(LLMConfig.from_dict(llm_cfg))
        except ValueError as e:
            sys.exit(f"ERROR: cannot build LLM client — {e}\n"
                     "Set SELENE_TG_LLM_API_KEY (or a real api_key in config.yaml "
                     "under signal.llm) to generate summaries.")
    return storage, llm


def cmd_generate(config, lookback_hours):
    from selene_space_tg_ingest import digest

    storage, llm = _load_storage_and_llm(config, need_llm=True)
    result = digest.generate_digest(storage, llm, lookback_hours=lookback_hours)
    print(result.summary or "")
    logger.info("Digest #%s: %d signals, window %s → %s",
                result.id, result.signal_count,
                result.from_at, result.to_at)


def cmd_latest(config, as_json):
    from selene_space_tg_ingest import digest

    storage, _ = _load_storage_and_llm(config, need_llm=False)
    result = digest.get_latest_digest(storage)
    if result is None:
        print("No digest yet. Run tg_digest.py to generate one.")
        return
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(result.summary or "")


def cmd_list(config, limit, as_json):
    from selene_space_tg_ingest import digest

    storage, _ = _load_storage_and_llm(config, need_llm=False)
    rows = digest.list_digests(storage, limit=limit)
    if as_json:
        print(json.dumps([r.to_dict() for r in rows], ensure_ascii=False, indent=2))
        return
    for r in rows:
        print(f"#{r.id}  {r.signal_count} signals  {r.created_at}  "
              f"{str(r.summary)[:60]}..." if r.summary else f"#{r.id}  {r.signal_count} signals")


def main():
    parser = argparse.ArgumentParser(description="tg_digest — LLM market digest generator")
    parser.add_argument("--config", type=str, default=None, help="Path to config.yaml")
    parser.add_argument("--lookback-hours", type=int, default=None,
                        help="Force window to last N hours (default: incremental from last digest, cold start 36h)")
    parser.add_argument("--latest", action="store_true", help="Print latest digest")
    parser.add_argument("--list", action="store_true", help="List recent digests")
    parser.add_argument("--limit", type=int, default=20, help="Max rows for --list (default 20)")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    args = parser.parse_args()

    from selene_space_tg_ingest.config import TgIngestConfig
    config = TgIngestConfig.load(args.config)

    if args.latest:
        cmd_latest(config, args.json)
    elif args.list:
        cmd_list(config, args.limit, args.json)
    else:
        cmd_generate(config, args.lookback_hours)


if __name__ == "__main__":
    main()

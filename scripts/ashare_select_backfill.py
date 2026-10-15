"""US-3 数据回填: 300 股样本 × 2023 起三年日K (腾讯源 hfq).

用法: python scripts/ashare_select_backfill.py [--n 300] [--start 20230101]
进度写 data/ashare_backfill.log (含失败清单, 支持断点续跑).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "ashare-select"))

from selene_space_ashare_select.sync import AshareSelectSync  # noqa: E402

DB = REPO / "data" / "ashare_select.db"
SAMPLE = REPO / "data" / "ashare_sample300.json"
LOG = REPO / "data" / "ashare_backfill.log"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--start", default="20230101")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()

    for k in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        os.environ.pop(k, None)
    os.environ["NO_PROXY"] = "*"

    logging.basicConfig(
        filename=str(LOG),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    with open(SAMPLE, encoding="utf-8") as f:
        symbols = json.load(f)[: args.n]
    sync = AshareSelectSync(db_path=args.db, provider="akshare", start_date=args.start)
    t0 = time.time()
    stats = sync.backfill(
        symbols, start_date=args.start, adjust="hfq", max_retries=3, sleep_fn=time.sleep
    )
    print(f"backfill done in {time.time() - t0:.0f}s: {stats}")
    print(f"local symbols: {len(sync.get_local_symbols())}")


if __name__ == "__main__":
    main()

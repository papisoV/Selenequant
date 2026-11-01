"""fund-ingest 手动采集脚本 — 真实网络面(端点级波动,不进 pytest)。

用法(先 activate.bat):
    python scripts/fund_ingest_collect.py           # 三域全采
    python scripts/fund_ingest_collect.py --only cb  # 仅转债
"""

from __future__ import annotations

import argparse
import sys

import akshare as ak

from selene_space_fund_ingest import collector, store


def main() -> int:
    parser = argparse.ArgumentParser(description="fund-ingest 手动采集")
    parser.add_argument(
        "--only",
        choices=["cb", "lof", "reits"],
        default=None,
        help="只采一个域(默认三域全采)",
    )
    args = parser.parse_args()

    conn = store.connect()
    jobs = []
    if args.only in (None, "cb"):
        jobs.append(("cb_bonds", collector.fetch_cb_bonds))
    if args.only in (None, "lof"):
        jobs.append(("lof_etf_spot", collector.fetch_lof_etf_spot))
    if args.only in (None, "reits"):
        jobs.append(("reits_spot", collector.fetch_reits_spot))

    for table, fetch in jobs:
        rows = fetch(ak)
        n = store.upsert_rows(conn, table, rows)
        print(
            f"{table}: {n} rows" if n else f"{table}: 0 rows (endpoint failed/degraded)"
        )

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

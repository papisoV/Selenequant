#!/usr/bin/env python
"""Issue redemption codes into the billing DB (monetize Phase 1.3).

Operator-side fulfillment tool: buy order on 爱发电 → run this → paste code
to the buyer. Codes are stored with plan + optional fixed expiry; the
entitlement window starts at REDEEM time (expires_at is computed then).

Usage:
  python scripts/gen_redemption_codes.py --count 5 --plan monthly
  python scripts/gen_redemption_codes.py --count 1 --plan trial
  python scripts/gen_redemption_codes.py --count 10 --plan yearly --db path/to/selenespace.db

Requires SELENE_BILLING_CODE_PREFIX (optional, default "SL-").
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _p in (
    REPO / "packages" / "storage",
    REPO / "packages" / "shared-types",
    REPO / "packages" / "core",
):
    sys.path.insert(0, str(_p))

# 与服务端主库一致(AsyncSQLiteManager DEFAULT_DB_PATH):billing repo 读的是这里,
# 发到别处的码服务端看不见。Docker 内路径不同,用 SELENE_MAIN_DB 覆盖。
DEFAULT_DB = Path(
    os.environ.get("SELENE_MAIN_DB")
    or (Path.home() / ".selenespace" / "data" / "selenespace.db")
)


def _gen_code(prefix: str) -> str:
    # 32-char alphabet without ambiguous glyphs (0/O, 1/I/L).
    alphabet = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
    body = "".join(secrets.choice(alphabet) for _ in range(16))
    return f"{prefix}{body[:4]}-{body[4:8]}-{body[8:12]}-{body[12:16]}"


async def _main(count: int, plan: str, db_path: Path) -> None:
    from selene_space_storage import AsyncSQLiteManager
    from selene_space_storage.repositories.billing import BillingRepository

    manager = AsyncSQLiteManager(db_path=db_path)
    await manager.connect()
    try:
        repo = BillingRepository(manager)
        prefix = "SL-"
        codes = [{"code": _gen_code(prefix), "plan": plan} for _ in range(count)]
        await repo.insert_codes(codes)
        print(f"issued {len(codes)} codes ({plan}) into {db_path}:")
        for c in codes:
            print(f"  {c['code']}")
    finally:
        await manager.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Issue redemption codes")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument(
        "--plan",
        choices=["trial", "monthly", "yearly", "basic"],
        default="monthly",
    )
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()
    if not args.db.exists():
        print(f"error: db not found: {args.db}", file=sys.stderr)
        return 1
    asyncio.run(_main(args.count, args.plan, args.db))
    return 0


if __name__ == "__main__":
    sys.exit(main())

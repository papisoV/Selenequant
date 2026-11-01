"""One-time K-line backfill (deep-history repair). Usage:
    python backfill_klines.py --intervals 5m,1m,1d,1w --days 90

Default 15m/4h@365 covers the most common chart periods. KlineDataManager
sync only runs at backend startup for SELENE_KLINE_INTERVALS (default 30m,1h),
so intervals outside it stay shallow unless backfilled here.
"""

import argparse
import asyncio
import datetime

from selene_space_core.kline_paths import default_klines_db_path
from selene_space_market.kline_manager import KlineDataManager
from selene_space_market.service import MarketDataService
from selene_space_market.sources.binance_source import BinanceSource

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
DEFAULT_INTERVALS = ["15m", "4h"]
DB_PATH = str(default_klines_db_path())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intervals", default=",".join(DEFAULT_INTERVALS))
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--markets", default="futures,spot")
    args = ap.parse_args()
    intervals = [i.strip() for i in args.intervals.split(",") if i.strip()]
    markets = [m.strip() for m in args.markets.split(",") if m.strip()]

    async def run() -> None:
        svc = MarketDataService()
        # direct (proxy=None): probe-verified reachable from this machine
        for mt in markets:
            svc.register_source(BinanceSource(market_type=mt))  # type: ignore[arg-type]

        mgr = KlineDataManager(db_path=DB_PATH)
        mgr.set_service(svc)

        t0 = datetime.datetime.now()
        totals = 0
        for mt in markets:
            for sym in SYMBOLS:
                for ivl in intervals:
                    try:
                        n = await mgr.sync_incremental(
                            "binance", mt, sym, ivl, days=args.days
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"[ERR] binance/{mt} {sym} {ivl}: {exc}")
                        n = 0
                    print(f"sync binance/{mt} {sym} {ivl} +{n}")
                    totals += n
        print(
            f"DONE total +{totals} in {(datetime.datetime.now() - t0).total_seconds():.1f}s"
        )

    asyncio.run(run())


if __name__ == "__main__":
    main()

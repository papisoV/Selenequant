#!/usr/bin/env python
"""定点拉取关键位置的秒级/逐笔行情（研究用）。

用户的用法是"定点"：只在手绘 fib 线的关键位置、关键时刻要秒级证据，
不要全时段扫描。本脚本就是那个定点入口。

两条路径，按需要选：

  1. **秒级 K 线**（``--mode klines``，默认）
     走 :meth:`BinanceSource.fetch_klines_rest` 的新 1s 路由
     （合约 = ``continuousKlines``，因 ``/fapi/v1/klines`` 的 1s 是 400）。
     只有 OHLCV，**没有主动买卖方向、没有成交笔数**。
     深度是滚动的（约 3.5 个月），更早的时间点取不到。

  2. **逐笔成交**（``--mode ticks``）
     走 :mod:`selene_space_market.tick_store.binance_archive`
     （``data.binance.vision`` 日档 ZIP），**带主动方方向**。
     历史可达 2021 起，但日档 T+1 才发布。
     可直接落进 :class:`TickStore`（``--db``）供 ``TickReplay`` 复用。

  3. **盘口深度**（``--mode depth``）
     走 :mod:`selene_space_market.tick_store.depth_archive`
     （``data.binance.vision`` 的 ``bookDepth`` 日档），**按百分比档位的累计挂单量**。
     历史可达 2023-01-01，T+1 发布；每 30 秒一个快照，12 档（±0.2/1/2/3/4/5%）。
     落进 :class:`DepthStore`（``--db``）。
     注意：**不是**逐档 L2，档内看不到单个价位；只有"距中间价 X% 内共多少量"。

相对哪一侧定价：合约腿用 ``--market futures``（默认），现货腿用 ``--market spot``。
注意现货 1s K 线同样**没有**主动方方向——要方向只能走逐笔。

用法::

    # 合约 1s K 线，定点取 10-08 23:14 前后 25 分钟
    python scripts/fetch_scene.py --symbol SOL/USDT --around "2026-10-08 23:14:42" --minutes 12

    # 逐笔落库（带方向），同一定点
    python scripts/fetch_scene.py --mode ticks --symbol SOL/USDT \\
        --around "2026-10-08 23:14:42" --minutes 12 --db data/ticks_sol.db

    # 指定 fib 线位，打印每秒穿越情况
    python scripts/fetch_scene.py --symbol SOL/USDT --around "2026-10-08 23:14:42" \\
        --level "图3[0.5]=111.257" --level "图1[0.382]=105.935"
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from selene_space_market.sources.binance_source import BinanceSource
from selene_space_market.tick_store.binance_archive import iter_day_trades
from selene_space_market.tick_store.depth_archive import iter_day_depth
from selene_space_market.tick_store.depth_db import DepthStore
from selene_space_market.tick_store.tick_db import TickStore

logger = logging.getLogger("fetch_scene")

BJT = timezone(timedelta(hours=8))
_ONE_SECOND_MS = 1_000
# continuousKlines page cap (verified 2026-10-15: limit=2000 -> 400 -1130)
_KLINES_PAGE = 1_500


def _resolve_proxy(explicit: str | None) -> str | None:
    """显式 --proxy 优先，否则回落到环境变量。"""
    return (
        explicit
        or os.environ.get("SELENE_PROXY_URL")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
    )


def _parse_when(text: str) -> datetime:
    """接受 ``2026-10-08 23:14:42``（默认北京时间）或带偏移的 ISO 串。"""
    cleaned = text.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=BJT)
        except ValueError:
            continue
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BJT)
    return parsed


def _parse_level(text: str) -> tuple[str, float]:
    """``图3[0.5]=111.257`` 或裸数字 ``111.257``。"""
    if "=" not in text:
        return (text.strip(), float(text))
    name, _, value = text.partition("=")
    return (name.strip(), float(value))


@dataclass
class Scene:
    """一个定点：时间窗 + 可选的关键线位。"""

    symbol: str
    start_ms: int
    end_ms: int
    levels: list[tuple[str, float]]

    def seconds(self) -> list[int]:
        return list(range(self.start_ms, self.end_ms + 1, _ONE_SECOND_MS))


def _fmt_bjt(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        .astimezone(BJT)
        .strftime("%Y-%m-%d %H:%M:%S")
    )


# ---------------------------------------------------------------------------
# 秒级 K 线
# ---------------------------------------------------------------------------


async def fetch_second_klines(
    scene: Scene, *, market_type: str, proxy_url: str | None
) -> list[Any]:
    """把整个定点窗按 1500 根一页向后翻，直到覆盖 start_ms。"""
    source = BinanceSource(market_type=market_type, proxy_url=proxy_url)  # type: ignore[arg-type]
    collected: dict[int, Any] = {}
    end_time = scene.end_ms + _ONE_SECOND_MS

    while end_time > scene.start_ms:
        page = await source.fetch_klines_rest(
            scene.symbol, "1s", limit=_KLINES_PAGE, end_time=end_time
        )
        if not page:
            break
        for bar in page:
            collected[bar.timestamp] = bar
        oldest = min(bar.timestamp for bar in page)
        if oldest >= end_time:  # no progress — stop rather than spin
            break
        end_time = oldest

    return [
        collected[k] for k in sorted(collected) if scene.start_ms <= k <= scene.end_ms
    ]


# ---------------------------------------------------------------------------
# 逐笔
# ---------------------------------------------------------------------------


def fetch_ticks_to_store(
    scene: Scene,
    *,
    db_path: str,
    market_type: str,
    proxy_url: str | None,
) -> tuple[TickStore, int]:
    """按天把定点窗覆盖到的日档逐笔拉进 TickStore。"""
    store = TickStore(db_path)
    start_day = datetime.fromtimestamp(scene.start_ms / 1000, tz=timezone.utc).date()
    end_day = datetime.fromtimestamp(scene.end_ms / 1000, tz=timezone.utc).date()

    total = 0
    day = start_day
    while day <= end_day:
        try:
            trades = list(
                iter_day_trades(
                    scene.symbol, day, market_type=market_type, proxy_url=proxy_url
                )
            )
        except FileNotFoundError as exc:
            logger.warning("跳过 %s：%s", day, exc)
            day += timedelta(days=1)
            continue

        batch = [
            {
                "exchange": "binance",
                "symbol": scene.symbol,
                "price": t["price"],
                "quantity": t["quantity"],
                "side": t["side"],
                "timestamp_ms": t["timestamp_ms"],
            }
            for t in trades
            if scene.start_ms <= t["timestamp_ms"] <= scene.end_ms
        ]
        inserted = store.insert_trades(batch) if batch else 0
        total += inserted
        logger.info("  %s: %d 笔", day, inserted)
        day += timedelta(days=1)
    return store, total


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def fetch_depth_to_store(
    scene: Scene,
    *,
    db_path: str,
    market_type: str,
    proxy_url: str | None,
) -> tuple[DepthStore, int]:
    """按天把定点窗覆盖到的 bookDepth 日档拉进 DepthStore。"""
    store = DepthStore(db_path)
    start_day = datetime.fromtimestamp(scene.start_ms / 1000, tz=timezone.utc).date()
    end_day = datetime.fromtimestamp(scene.end_ms / 1000, tz=timezone.utc).date()

    total = 0
    day = start_day
    while day <= end_day:
        try:
            rows = list(
                iter_day_depth(
                    scene.symbol, day, market_type=market_type, proxy_url=proxy_url
                )
            )
        except FileNotFoundError as exc:
            logger.warning("跳过 %s：%s", day, exc)
            day += timedelta(days=1)
            continue

        batch = [
            {
                "exchange": "binance",
                "symbol": scene.symbol,
                "timestamp_ms": r["timestamp_ms"],
                "percentage": r["percentage"],
                "depth": r["depth"],
                "notional": r["notional"],
            }
            for r in rows
            if scene.start_ms <= r["timestamp_ms"] <= scene.end_ms
        ]
        inserted = store.insert_depth(batch) if batch else 0
        total += inserted
        logger.info("  %s: %d 行", day, inserted)
        day += timedelta(days=1)
    return store, total


def _render_depth(store: DepthStore, scene: Scene) -> str:
    snaps = store.snapshots(scene.symbol, scene.start_ms, scene.end_ms)
    out: list[str] = []
    if not snaps:
        return "窗口内没有深度快照（bookDepth 日档 T+1 才发布，最近一两天可能还没有）"
    out.append(f"窗口共 {len(snaps):,} 个快照（每 30 秒一个，12 档累计挂单量）")
    out.append(
        f"{'北京时间':<21}{'买-1%':>14}{'卖+1%':>14}{'不对称':>9}{'买-5%':>14}{'卖+5%':>14}"
    )
    for snap in snaps:
        bands = snap["bands"]
        bid1 = bands.get(-1.0, {}).get("depth", 0.0)
        ask1 = bands.get(1.0, {}).get("depth", 0.0)
        bid5 = bands.get(-5.0, {}).get("depth", 0.0)
        ask5 = bands.get(5.0, {}).get("depth", 0.0)
        asym = (bid5 - ask5) / (bid5 + ask5) if (bid5 + ask5) > 0 else 0.0
        out.append(
            f"{_fmt_bjt(snap['timestamp_ms']):<21}{bid1:>14,.0f}{ask1:>14,.0f}"
            f"{asym:>+9.3f}{bid5:>14,.0f}{ask5:>14,.0f}"
        )
    return "\n".join(out)


def _render_klines(bars: list[Any], levels: list[tuple[str, float]]) -> str:
    out: list[str] = []
    out.append(f"{'北京时间':<21}{'开':>9}{'高':>9}{'低':>9}{'收':>9}{'量':>12}  线位")
    for bar in bars:
        stamp = _fmt_bjt(bar.timestamp)
        hits = [
            f"{name}={val:.3f}" for name, val in levels if bar.low <= val <= bar.high
        ]
        mark = f"  ← 穿越 {'、'.join(hits)}" if hits else ""
        out.append(
            f"{stamp:<21}{bar.open:>9.2f}{bar.high:>9.2f}{bar.low:>9.2f}"
            f"{bar.close:>9.2f}{bar.volume:>12.2f}{mark}"
        )
    return "\n".join(out)


def _render_ticks(store: TickStore, scene: Scene) -> str:
    rows = store.query(scene.symbol, scene.start_ms, scene.end_ms)
    by_second: dict[int, dict[str, float]] = {}
    for row in rows:
        bucket = row["timestamp_ms"] // _ONE_SECOND_MS * _ONE_SECOND_MS
        agg = by_second.setdefault(bucket, {"buy": 0.0, "sell": 0.0, "n": 0.0})
        agg[row["side"]] = agg.get(row["side"], 0.0) + row["quantity"]
        agg["n"] += 1

    out: list[str] = []
    out.append(f"逐笔窗口共 {len(rows):,} 笔，覆盖 {len(by_second):,} 秒")
    out.append(f"{'北京时间':<21}{'主动买':>12}{'主动卖':>12}{'净额':>12}{'笔数':>8}")
    for bucket in sorted(by_second):
        agg = by_second[bucket]
        net = agg.get("buy", 0.0) - agg.get("sell", 0.0)
        out.append(
            f"{_fmt_bjt(bucket):<21}{agg.get('buy', 0.0):>12.1f}"
            f"{agg.get('sell', 0.0):>12.1f}{net:>+12.1f}{int(agg['n']):>8}"
        )
    return "\n".join(out)


def _write(text: str, out_path: str | None) -> None:
    if out_path:
        # 非 ASCII 直接 print 到 Windows 控制台会被 GBK 打乱，落盘再读
        io.open(out_path, "w", encoding="utf-8", newline="").write(text)
        print(f"已写入 {out_path}（共 {text.count(chr(10)) + 1} 行）")
    else:
        stdout = sys.stdout
        if hasattr(stdout, "reconfigure"):
            stdout.reconfigure(encoding="utf-8", errors="replace")
        print(text)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="定点拉取关键位置的秒级/逐笔行情",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--symbol", default="SOL/USDT", help="标的（默认 SOL/USDT）")
    parser.add_argument(
        "--around",
        required=True,
        help="定点时刻，如 '2026-10-08 23:14:42'（默认北京时间）",
    )
    parser.add_argument(
        "--minutes", type=int, default=12, help="定点前后各取多少分钟（默认 12）"
    )
    parser.add_argument(
        "--mode",
        choices=("klines", "ticks", "depth"),
        default="klines",
        help="klines=秒级K线，ticks=逐笔，depth=盘口深度归档",
    )
    parser.add_argument(
        "--market",
        choices=("futures", "spot"),
        default="futures",
        help="合约腿或现货腿",
    )
    parser.add_argument(
        "--level",
        action="append",
        default=[],
        metavar="NAME=PRICE",
        help="关键线位，可重复，如 --level '图3[0.5]=111.257'",
    )
    parser.add_argument("--db", help="落库路径（ticks/depth 模式必填）")
    parser.add_argument("--out", help="输出写的文件（UTF-8）；不填则打印到 stdout")
    parser.add_argument(
        "--proxy", help="代理地址；默认读 SELENE_PROXY_URL/HTTPS_PROXY/HTTP_PROXY"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    pivot = _parse_when(args.around)
    span = timedelta(minutes=args.minutes)
    scene = Scene(
        symbol=args.symbol,
        start_ms=int((pivot - span).timestamp() * 1000),
        end_ms=int((pivot + span).timestamp() * 1000),
        levels=[_parse_level(t) for t in args.level],
    )
    proxy = _resolve_proxy(args.proxy)

    logger.info(
        "定点 %s，窗口 %s → %s（%s）",
        scene.symbol,
        _fmt_bjt(scene.start_ms),
        _fmt_bjt(scene.end_ms),
        args.mode,
    )

    if args.mode == "klines":
        bars = asyncio.run(
            fetch_second_klines(scene, market_type=args.market, proxy_url=proxy)
        )
        if not bars:
            logger.error(
                "没取到任何 1s K 线——合约 1s 深度是滚动的（约 3.5 个月），该时点可能已出窗"
            )
            return 1
        logger.info("取到 %d 根 1s K 线", len(bars))
        _write(_render_klines(bars, scene.levels), args.out)
        return 0

    if args.mode == "depth":
        if not args.db:
            parser.error("--mode depth 需要 --db 指定落库路径")
        dstore, dins = fetch_depth_to_store(
            scene, db_path=args.db, market_type=args.market, proxy_url=proxy
        )
        logger.info("落库 %d 行 → %s", dins, args.db)
        _write(_render_depth(dstore, scene), args.out)
        dstore.close()
        return 0

    if not args.db:
        parser.error("--mode ticks 需要 --db 指定落库路径")
    store, inserted = fetch_ticks_to_store(
        scene, db_path=args.db, market_type=args.market, proxy_url=proxy
    )
    logger.info("落库 %d 笔 → %s", inserted, args.db)
    _write(_render_ticks(store, scene), args.out)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

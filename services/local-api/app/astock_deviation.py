"""交易所异动偏离(B2)的读取路径 — 把纯口径接到两条底物上。

口径本体在 :mod:`selene_space_ashare.abnormal`(纯函数,无 I/O)。本模块只做
数据装配,分两条:

- **(a) 日频**:个股收盘取自 KlineDB 的 **raw** 分区(``astock``/``spot``,
  即 live 同步落盘的不复权价),基准指数收盘取自 ``data_fetcher.get_index_daily``;
  两者按共同交易日对齐后交给 :func:`evaluate_deviation`。
- **(b) 盘中**:同一段历史 + 今日实时涨跌幅(个股与基准指数),经
  ``compose_live_deviation`` 叠加。叠加前必须确认「历史最后一根 < 报价日」,
  否则今日会被算两遍(与 TSP build_overview 的 ``includes_today`` 同口径)。

分区选择的意义:批量导入把复权价(qfq,冻结于导入日)写在 ``astock`` 分区,
live 同步把不复权价写在 ``spot``。偏离值要与**原始**指数价比较,故首选
``spot``;仅在 live 分区尚空时回退导入分区,并在行内标出 ``basis`` 以便消费方
识别 —— 拿复权基准去比原始指数会产生看似合理却错误的偏离。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from selene_space_ashare.abnormal import (
    WINDOWS,
    align_by_date,
    bench_candidates_of,
    evaluate_deviation,
)

# 中国无夏令时,固定 +08:00 即精确(与 astock_intraday_source 同约定)。
_CST_TZ = timezone(timedelta(hours=8))

_FETCH_EXTRA = 14

_INDEX_KEYS = ("date", "close")


def _today_str(today: datetime) -> str:
    return today.strftime("%Y%m%d")


def _settled_stock_series(
    kline_db: Any, symbol: str, *, today: datetime, n: int
) -> tuple[list[tuple[str, float]], str]:
    """KlineDB → 严格早于今日的 (YYYYMMDD, close) 升序序列 + 所用分区标签。

    返回 ``(series, basis)``。``basis`` 为 ``"spot"``(raw)或 ``"astock"``
    (导入的 qfq 回退);无任何 bar 时为 ``("", "none")``。
    """
    cutoff_ms = int(
        today.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
    )
    frame = pd.DataFrame()
    basis = "none"
    for market_type in ("spot", "astock"):
        try:
            got = kline_db.get_klines("astock", market_type, symbol, "1d")
        except Exception:  # noqa: BLE001 — fail-open:一个分区读失败不该中断整表
            continue
        if got is not None and not got.empty and "timestamp" in got.columns:
            frame = got
            basis = market_type
            break
    if frame.empty:
        return [], "none"
    series = [
        (
            datetime.fromtimestamp(int(ts) / 1000, _CST_TZ).strftime("%Y%m%d"),
            float(close),
        )
        for ts, close in zip(frame["timestamp"], frame["close"], strict=False)
        if int(ts) < cutoff_ms
    ]
    series.sort(key=lambda pair: pair[0])
    return series[-n:], basis


def _index_series(
    index_daily_fn: Callable[[str], Any], index_code: str, *, n: int
) -> list[tuple[str, float]]:
    """``get_index_daily`` → (YYYYMMDD, close) 升序序列;取不到返回空。"""
    code = index_code.split(".")[0]
    try:
        frame = index_daily_fn(code)
    except Exception:  # noqa: BLE001 — fail-open:指数源失败只降级该标的
        return []
    if frame is None or frame.empty:
        return []
    if not set(_INDEX_KEYS) <= set(frame.columns):
        return []
    rows: list[tuple[str, float]] = []
    for date, close in zip(frame["date"], frame["close"], strict=False):
        key = _to_date_key(date)
        if key is None or pd.isna(close):
            continue
        rows.append((key, float(close)))
    rows.sort(key=lambda pair: pair[0])
    return rows[-n:]


def _resolve_bench(
    index_daily_fn: Callable[[str], Any], symbol: str, *, n: int
) -> tuple[str, list[tuple[str, float]]] | None:
    """按候选顺序取板块基准指数;首个有数据的候选胜出(全无则 None)。

    返回 ``(指数代码, 序列)`` —— 两者同时有或同时无,调用方一次判定即可。
    候选顺序即 TSP ``_BENCHMARK_PREFERENCE`` 的优先级。
    """
    for candidate in bench_candidates_of(symbol):
        found = _index_series(index_daily_fn, candidate, n=n)
        if found:
            return candidate, found
    return None


def _to_date_key(value: Any) -> str | None:
    """归一化指数日期的 YYYYMMDD 键(接受 str / Timestamp / datetime)。"""
    if value is None:
        return None
    if isinstance(value, str):
        digits = value.replace("-", "").strip()
        return digits if len(digits) == 8 and digits.isdigit() else None
    try:
        return str(pd.Timestamp(value).strftime("%Y%m%d"))
    except (TypeError, ValueError):
        return None


async def collect_live_quotes(
    market_svc: Any, symbols: Sequence[str]
) -> dict[str, tuple[str, float]]:
    """从 A股盘中源取 ``{symbol: (报价日YYYYMMDD, 今日涨跌幅小数)}``。

    仅 (b) 路径使用。源缺失或取数失败时返回空字典 —— 调用方据此退回 (a),
    绝不因实时取数失败而中断整个偏离表。报价日取自 tick 的北京时间戳
    (Sina 字段 30/31),不能用「本地今天」顶替:非交易日拉到的仍是上一
    交易日的涨跌幅,用今天当键会让叠加条件误判通过。
    """
    if market_svc is None or not symbols:
        return {}
    source = market_svc.get_source("astock", "intraday")
    if source is None:
        return {}
    try:
        tickers = await source.fetch_tickers_rest(list(symbols))
    except Exception:  # noqa: BLE001 — fail-open:实时取数失败退回 (a)
        return {}
    quotes: dict[str, tuple[str, float]] = {}
    for symbol, ticker in tickers.items():
        if ticker is None:
            continue
        day = datetime.fromtimestamp(int(ticker.timestamp) / 1000, _CST_TZ).strftime(
            "%Y%m%d"
        )
        quotes[symbol] = (day, float(ticker.change_24h_pct) / 100.0)
    return quotes


def deviation_report(
    *,
    symbols: Sequence[str],
    kline_db: Any,
    index_daily_fn: Callable[[str], Any],
    today: datetime,
    live_quotes: dict[str, tuple[str, float]] | None = None,
    windows: Sequence[int] = WINDOWS,
) -> dict[str, Any]:
    """逐标的算偏离 + 接近度。(a)/(b) 共用,仅看是否喂了 ``live_quotes``。

    ``live_quotes`` 形如 ``{symbol: (报价日YYYYMMDD, 今日涨跌幅小数)}`` ——
    个股与基准指数代码都要在里面。**(b) 的叠加条件是**报价日 == 北京今天**且
    个股与基准两侧都有报价;否则今日涨跌会被重复计入(非交易日或尚未开盘时,
    Sina 返回的是上一交易日的涨跌幅,已含在历史里)。任一条件不满足即退回
    (a),行内 ``intraday`` 标 False,绝不静默叠加。

    「历史已含今日」的防护不在这里 —— 由 ``_settled_stock_series`` 的
    「严格早于会话日」过滤保证(见其 docstring),故此处无需再判一次。
    """
    today_key = _today_str(today)
    quotes = live_quotes or {}
    n = max(windows) + 1 + _FETCH_EXTRA
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for symbol in symbols:
        stock_series, basis = _settled_stock_series(kline_db, symbol, today=today, n=n)
        if not stock_series:
            skipped.append({"symbol": symbol, "reason": "no_daily_bars"})
            continue

        resolved = _resolve_bench(index_daily_fn, symbol, n=n)
        if resolved is None:
            skipped.append({"symbol": symbol, "reason": "no_bench_index"})
            continue
        bench_code, index_series = resolved

        stock_closes, index_closes = align_by_date(stock_series, index_series)
        stock_rt: float | None = None
        index_rt: float | None = None
        stock_quote = quotes.get(symbol)
        index_quote = quotes.get(bench_code)
        if (
            stock_quote is not None
            and index_quote is not None
            and stock_quote[0] == today_key
            and index_quote[0] == today_key
        ):
            stock_rt, index_rt = stock_quote[1], index_quote[1]

        deviation = evaluate_deviation(
            stock_closes,
            index_closes,
            symbol=symbol,
            windows=windows,
            stock_rt_pct=stock_rt,
            index_rt_pct=index_rt,
        )
        rows.append(
            {
                "symbol": symbol,
                "board": deviation.board,
                "st": deviation.st,
                "bench": bench_code,
                "basis": basis,
                "basis_date": stock_series[-1][0],
                "intraday": deviation.intraday,
                "max_closeness": round(deviation.max_closeness, 4),
                "status": deviation.status,
                "windows": {
                    f"{w.days}d": {
                        "value": round(w.deviation, 4),
                        "threshold": w.threshold,
                        "closeness": round(w.closeness, 4),
                    }
                    for w in deviation.windows
                },
            }
        )

    rows.sort(key=lambda r: r["max_closeness"], reverse=True)
    counts = {
        "triggered": sum(1 for r in rows if r["status"] == "triggered"),
        "edge": sum(1 for r in rows if r["status"] == "edge"),
        "watch": sum(1 for r in rows if r["status"] == "watch"),
    }
    return {
        "as_of": today_key,
        "live": any(r["intraday"] for r in rows),
        "counts": counts,
        "rows": rows,
        "skipped": skipped,
    }


__all__ = ["collect_live_quotes", "deviation_report"]

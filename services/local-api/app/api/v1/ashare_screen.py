# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""A股交互式选股器 —— 结构化条件筛选全市场截面。

两个入口,同一份逻辑::

    POST /ashare/screen    body: 条件字段(JSON)
    GET  /ashare/screen    query: 同名字段(便于把一次筛选存成链接)

## 为什么复用大盘总览的截面,而不是自己读库

选股器看到的 ``change_pct`` / ``volume_ratio`` / ``boards`` 必须和
``/ashare/overview`` 里同一只股票的数字**逐位相同**,否则两个面板会在同一天
对同一只票给出两个说法。做法是共用 ``build_quote_rows`` 与同一个缓存基线
(``ashare_overview._baseline_for``),而不是各算各的。

代价是筛选请求会带上总览的取数开销;收益是"数字不可能不一致"。基线已按
``(db, session)`` 缓存,重复过滤只付掩码的钱(实测 ~66 ms / 5k 行)。

## 会话选择(与总览同规则,再加覆盖率否决)

1. 显式 ``date`` → 用它。
2. 否则实时快照的会话 → 用它(快照是全市场、带真实成交额)。
3. 否则日线库最新会话,但**必须达到 ``MIN_COVERAGE``**;不够就往前退到第一个
   达标的交易会话。实测最新会话经常只写了一半(2026-10-20:67/5574 = 1.2%),
   直接拿它筛会静默漏掉 98% 的票。退不下去就照实报 ``degraded``。

## 口径与降级

* ``status``:``ok`` / ``empty`` / ``degraded``。``empty`` 是"截面上没有任何
  行";条件筛出 0 只但截面正常时仍是 ``ok`` + ``matched == 0`` —— 前者是数据
  问题,后者是"今天没有票满足你的条件",两者不能混。
* ``coverage``:行情行数 ÷ 股票总数;低于 ``MIN_COVERAGE`` 一律 ``degraded``。
* ``amount`` 只在实时快照里存在。日线回退的成交额是 ``close × volume`` 的
  估算,逐行用 ``turnover_est`` 标出。
* 市值表只有**最新一日**快照,回看历史时市值条件会缺值。``coverage`` 里
  ``market_cap_available`` 是三态:``null`` = 这次没设市值条件、没去取表;
  ``true`` = 取到了;``false`` = 取了但拿不到,市值条件会把行全排掉。

失败一律 200 + ``status``,只有请求体本身不合法才 422。
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request

from app.api.v1.ashare_overview import (
    _baseline_for,
    _daily_db,
    _respond,
    _snapshot_rows,
    _snapshot_session,
    _universe_size,
)
from selene_space_types.common import APIResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ashare", tags=["ashare-screen"])

_CST_TZ = timezone(timedelta(hours=8))

# 往前找达标会话的上限。实测最多退一两个(4 个交易日足够跨过任何一次半截写入)。
_SESSION_LOOKBACK = 6


@dataclass
class _CrossSection:
    """A scored session's fetched quotes plus, once built, the enriched rows.

    ``rows`` starts as ``None``: building them needs the baseline, which the
    caller fetches *after* the session is chosen. The first request pays that
    build (~200 ms over 4k names); every later filter over the same session
    works on the cached list, which is what makes the panel interactive.
    """

    source: str
    quotes: list[dict[str, Any]]
    rows: list[dict[str, Any]] | None = None
    baseline: Any = None


# ---------------------------------------------------------------------------
# Caches — 没有它们,每次筛选都要重读全市场(实测冷 42 s / 热 10 s)
# ---------------------------------------------------------------------------

# (db id, scored session) → (db, cross-section)
#
# 一个会话的截面一旦取到就不再变:行情是那个交易日的,不会因为再筛一次而
# 不同。缓存它(以及由它构建出的行),重复筛选就只付掩码的钱。和总览的
# 基线缓存同一套纪律:保留 db 引用锁住地址,避免 id 被复用后命中别人的条目。
_quotes_cache: dict[tuple[int, str], tuple[Any, _CrossSection]] = {}
_quotes_lock = threading.Lock()
_QUOTES_CACHE_MAX = 4

# (db id, CST 日期) → (monotonic 时间戳, 会话, 原因)
#
# 会话选择要试读候选会话的截面才能判断覆盖率,贵。但"今天该评哪个会话"在
# 一个交易日内只会变一次(新的半截会话落地 / 旧会话补全),所以给 2 分钟 TTL:
# 至多多付一次试读,不会让用户在收盘后一直看到昨天的会话。
_session_cache: dict[tuple[int, str], tuple[float, str | None, str | None]] = {}
_session_lock = threading.Lock()
_SESSION_TTL_SECONDS = 120.0


def _finite(value: Any) -> float | None:
    """标量 → 有限浮点或 ``None``。字符串数字(``"1.5"``)也接受。"""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int_or_none(value: Any) -> int | None:
    number = _finite(value)
    return None if number is None else int(number)


def _bool_or_none(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"无法解析为布尔值: {value!r}")


def _board_list(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(part).strip() for part in value]
    else:
        raise ValueError(f"board_types 必须是列表或逗号分隔字符串,收到 {value!r}")
    return tuple(item for item in items if item)


def _spec_from_mapping(payload: dict[str, Any]) -> Any:
    """请求字段 → ``ScreenSpec``。未知字段忽略(便于前端演进)。

    字段名与 ``ScreenSpec`` 一一对应,``None``/缺省 = 不限制,区间含端点。
    """
    from selene_space_ashare.screener import ScreenSpec

    sort_desc = _bool_or_none(payload.get("sort_desc"))
    return ScreenSpec(
        price_min=_finite(payload.get("price_min")),
        price_max=_finite(payload.get("price_max")),
        change_pct_min=_finite(payload.get("change_pct_min")),
        change_pct_max=_finite(payload.get("change_pct_max")),
        boards_min=_int_or_none(payload.get("boards_min")),
        boards_max=_int_or_none(payload.get("boards_max")),
        volume_ratio_min=_finite(payload.get("volume_ratio_min")),
        volume_ratio_max=_finite(payload.get("volume_ratio_max")),
        turnover_min=_finite(payload.get("turnover_min")),
        float_mv_min=_finite(payload.get("float_mv_min")),
        float_mv_max=_finite(payload.get("float_mv_max")),
        total_mv_min=_finite(payload.get("total_mv_min")),
        total_mv_max=_finite(payload.get("total_mv_max")),
        board_types=_board_list(payload.get("board_types")),
        exclude_st=bool(_bool_or_none(payload.get("exclude_st")) or False),
        above_ma=(str(payload.get("above_ma") or "").strip() or None),
        new_high=bool(_bool_or_none(payload.get("new_high")) or False),
        new_low=bool(_bool_or_none(payload.get("new_low")) or False),
        limit_up=_bool_or_none(payload.get("limit_up")),
        sort_by=str(payload.get("sort_by") or "change_pct"),
        sort_desc=True if sort_desc is None else sort_desc,
        limit=_int_or_none(payload.get("limit")) or 50,
    )


def _recent_sessions(db: Any, limit: int = _SESSION_LOOKBACK) -> list[str]:
    """最近 ``limit`` 个交易会话,新 → 旧。读不到时返回空列表。"""
    getter = getattr(db, "get_session_stamps", None)
    if getter is None:
        return []
    end_ms = int(datetime.now(tz=_CST_TZ).timestamp() * 1000)
    try:
        stamps = getter("astock", "daily_all", "1d", end_ms=end_ms, limit=limit)
    except Exception:  # noqa: BLE001 — 会话枚举是提示性的,失败走别的分支
        logger.warning("screen: session stamps unavailable", exc_info=True)
        return []
    seen = {
        datetime.fromtimestamp(int(s) / 1000.0, tz=_CST_TZ).date().isoformat()
        for s in (stamps or [])
    }
    return sorted(seen, reverse=True)


def _market_cap_frame() -> Any:
    """全市场最新估值快照;不可用时 ``None``。

    冷启动约 24 s,7 天 parquet 缓存命中约 5 ms。只在条件真的要市值时才取。
    """
    from selene_space_ashare.data_fetcher import fetch_market_cap

    try:
        return fetch_market_cap()
    except Exception:  # noqa: BLE001 — 缺市值只影响一个条件
        logger.warning("screen: market cap unavailable", exc_info=True)
        return None


def _cached_cross(db: Any, as_of: str) -> _CrossSection | None:
    """The cross-section for ``as_of`` if already fetched this session."""
    key = (id(db), as_of)
    with _quotes_lock:
        hit = _quotes_cache.get(key)
    if hit is None:
        return None
    cached_db, cross = hit
    # 保留引用已经让地址复用不可能,``is`` 只是把意图写出来,代价为零。
    return cross if cached_db is db else None


def _store_cross(db: Any, as_of: str, cross: _CrossSection) -> None:
    key = (id(db), as_of)
    with _quotes_lock:
        if len(_quotes_cache) >= _QUOTES_CACHE_MAX:
            # 按键入顺序淘汰:旧会话最不可能再被筛到。
            _quotes_cache.pop(next(iter(_quotes_cache)))
        _quotes_cache[key] = (db, cross)


def _fetch_quotes(db: Any, as_of: str, snapshot: Any, snap_session: str | None):
    """Read one session's cross-section from the best available source.

    Returns ``(quote_source, quotes)``; ``("none", [])`` when the session has
    no rows at all.
    """
    from selene_space_ashare.market_overview import (
        quotes_from_daily,
        quotes_from_snapshot,
    )

    if snapshot and snap_session == as_of:
        return "snapshot", quotes_from_snapshot(snapshot)

    from selene_space_ashare.market_daily import market_daily_universe

    quotes = quotes_from_daily(market_daily_universe(db, end_date=as_of), today=as_of)
    return ("daily" if quotes else "none"), quotes


def _session_counts(db: Any, days: int = 30) -> list[tuple[str, int]]:
    """最近 ``days`` 个日历日内每个交易会话的行数,新 → 旧。

    一次分组查询(~0.5 s / 一个月)取代"逐个候选会话读全市场再数行数"
    (每个 ~6 s)。读不到时返回空列表,调用方退回逐个试读。
    """
    getter = getattr(db, "get_session_counts", None)
    if getter is None:
        return []
    end_ms = int(datetime.now(tz=_CST_TZ).timestamp() * 1000)
    start_ms = end_ms - days * 86_400_000
    try:
        rows = getter("astock", "daily_all", "1d", start_ms=start_ms, end_ms=end_ms)
    except Exception:  # noqa: BLE001 — 计数是选会话的优化,失败退回试读
        logger.warning("screen: session counts unavailable", exc_info=True)
        return []
    return [
        (
            datetime.fromtimestamp(int(ts) / 1000.0, tz=_CST_TZ).date().isoformat(),
            int(n),
        )
        for ts, n in (rows or [])
    ]


def _select_session(
    db: Any, snapshot: Any, snap_session: str | None
) -> tuple[str | None, str | None]:
    """当日该评哪个会话 —— 缓存 2 分钟。

    ``snapshot`` / ``snap_session`` 由调用方取好传进来:一次请求只读一次快照,
    选会话和取截面共用同一份。

    Returns:
        ``(session, reason)``。``session`` 为 ``None`` 时 ``reason`` 说明原因。
    """
    day_key = (id(db), datetime.now(tz=_CST_TZ).date().isoformat())
    now = time.monotonic()
    with _session_lock:
        hit = _session_cache.get(day_key)
    if hit is not None and now - hit[0] < _SESSION_TTL_SECONDS:
        return hit[1], hit[2]

    session, reason = _compute_session(db, snapshot, snap_session)

    with _session_lock:
        if len(_session_cache) >= _QUOTES_CACHE_MAX:
            _session_cache.pop(next(iter(_session_cache)))
        _session_cache[day_key] = (time.monotonic(), session, reason)
    return session, reason


def _compute_session(
    db: Any, snapshot: Any, snap_session: str | None
) -> tuple[str | None, str | None]:
    """快照会话优先;否则日线库里第一个达到覆盖率底线的会话。

    实测最新会话经常只写了一半(2026-10-20:67/5574 = 1.2%),直接拿它当全市场
    会让条件看起来"没筛出票"。宁可退一个交易日,也不拿薄截面筛名单。
    """
    from selene_space_ashare.market_overview import MIN_COVERAGE

    # 快照已经确认是当前会话的:它本身就是全市场读数,不需要再试读日线库。
    if snapshot and snap_session:
        return snap_session, None

    universe = _universe_size()
    counts = _session_counts(db)
    if counts:
        # 行数是"这一会话写过多少票"的精确代理,且一段查询就能拿到全部候选。
        # 用股票总数做分母;总数拿不到时直接取最新有行的会话(覆盖率无从判断)。
        for day, n in counts:
            if not universe or n / universe >= MIN_COVERAGE:
                skipped = [f"{d}:{c}/{universe}" for d, c in counts if d > day]
                if skipped:
                    logger.info("screen: skipped thin sessions %s", ", ".join(skipped))
                return day, None
        logger.info(
            "screen: no session cleared coverage floor: %s",
            ", ".join(f"{d}:{n}/{universe}" for d, n in counts),
        )
        return None, "partial_coverage"

    # 计数接口不可用:退回逐个候选会话试读(慢,但正确)。
    reasons: list[str] = []
    for day in _recent_sessions(db):
        cached = _cached_cross(db, day)
        if cached is not None:
            quotes = cached.quotes
        else:
            try:
                source, quotes = _fetch_quotes(db, day, None, None)
            except Exception as exc:  # noqa: BLE001 — 读失败换下一个会话
                logger.warning("screen: universe read failed for %s: %s", day, exc)
                reasons.append(f"{day}:read_failed")
                continue
            _store_cross(db, day, _CrossSection(source=source, quotes=quotes))
        ratio = len(quotes) / universe if universe else None
        if ratio is None or ratio >= MIN_COVERAGE:
            return day, None
        reasons.append(f"{day}:{len(quotes)}/{universe}")

    logger.info("screen: no session cleared coverage floor: %s", ", ".join(reasons))
    return None, "partial_coverage"


def _resolve_quotes(
    db: Any, requested: str
) -> tuple[str | None, str | None, _CrossSection | None]:
    """决定被评分的会话并取回它的截面(行情 + 可能的已构建行)。

    Returns:
        ``(as_of, reason, cross)``。``as_of`` 为 ``None`` 时 ``reason`` 说明
        为什么没有——调用方据此返回 ``degraded``。
    """
    # 一次请求只读一次快照:选会话与取截面共用。
    snapshot, _ = _snapshot_rows()
    snap_session = _snapshot_session(snapshot) if snapshot else None

    if requested:
        cached = _cached_cross(db, requested)
        if cached is not None:
            return requested, None, cached
        # 快照只在与目标会话同一天时才可用 —— 否则它描述的是另一个交易日。
        # 先判这个再决定要不要读日线库:命中快照时那次 6 s 的全市场读是纯浪费。
        try:
            source, quotes = _fetch_quotes(db, requested, snapshot, snap_session)
        except Exception as exc:  # noqa: BLE001 — 降级,不 500
            logger.warning("screen: universe read failed for %s: %s", requested, exc)
            return None, f"universe_read_failed: {exc}", None
        cross = _CrossSection(source=source, quotes=quotes)
        _store_cross(db, requested, cross)
        return requested, None, cross

    as_of, reason = _select_session(db, snapshot, snap_session)
    if as_of is None:
        return None, reason, None

    cached = _cached_cross(db, as_of)
    if cached is not None:
        return as_of, None, cached

    try:
        source, quotes = _fetch_quotes(db, as_of, snapshot, snap_session)
    except Exception as exc:  # noqa: BLE001
        logger.warning("screen: universe read failed for %s: %s", as_of, exc)
        return None, f"universe_read_failed: {exc}", None
    cross = _CrossSection(source=source, quotes=quotes)
    _store_cross(db, as_of, cross)
    return as_of, None, cross


def build_screen(date: str = "", **fields: Any) -> dict[str, Any]:
    """跑一次筛选。永不抛异常(条件本身不合法时抛 ``ValueError``)。

    ``fields`` 是 ``_spec_from_mapping`` 认的请求字段。``date`` 选会话;
    空 = 快照会话,或日线库里第一个达到覆盖率底线的会话。
    """
    from selene_space_ashare.market_overview import MIN_COVERAGE, build_quote_rows
    from selene_space_ashare.screener import describe_spec, screen

    try:
        spec = _spec_from_mapping(fields)
    except ValueError as exc:
        # 条件写错了是调用方的错,不是数据的问题 —— 这条要 422。
        raise ValueError(f"筛选条件不合法: {exc}") from exc

    db = _daily_db()
    if db is None:
        return {
            "status": "degraded",
            "reason": "kline_manager_unavailable",
            "as_of": None,
            "rows": [],
        }

    try:
        as_of, reason, cross = _resolve_quotes(db, date)
    except Exception as exc:  # noqa: BLE001 — 降级,不 500
        logger.exception("screen: quote resolution failed")
        return {
            "status": "degraded",
            "reason": f"quotes_failed: {exc}",
            "as_of": None,
            "rows": [],
        }
    if as_of is None or cross is None:
        return {
            "status": "degraded",
            "reason": reason or "no_session",
            "as_of": None,
            "rows": [],
        }

    quote_source = cross.source
    rows = cross.rows
    baseline = cross.baseline
    if rows is None or baseline is None:
        try:
            baseline = _baseline_for(db, as_of)
        except Exception as exc:  # noqa: BLE001 — 页面降级,不 500
            logger.exception("screen: baseline failed for %s", as_of)
            return {
                "status": "degraded",
                "reason": f"baseline_failed: {exc}",
                "as_of": as_of,
                "rows": [],
            }

        rows = build_quote_rows(
            quotes=cross.quotes, baseline=baseline, trade_date=as_of
        )
        # 存成新的截面对象而不是原地填字段:这个对象同时被其它请求线程持有,
        # 原地改等于让两个请求共写一块内存。
        _store_cross(
            db,
            as_of,
            _CrossSection(
                source=quote_source, quotes=cross.quotes, rows=rows, baseline=baseline
            ),
        )

    universe = _universe_size()
    scanned = len(rows)
    coverage = scanned / universe if universe else None

    status = "ok"
    if scanned == 0:
        status, reason = "empty", reason or "no_quotes"
    elif coverage is not None and coverage < MIN_COVERAGE:
        status, reason = "degraded", "partial_coverage"

    # 三态,不是布尔:没设市值条件时压根没去取表(冷启动 24 s),
    # ``null`` 才如实表示"没问";``false`` 专指"问了但拿不到"。
    market_cap = _market_cap_frame() if spec.needs_market_cap else None
    market_cap_available = None if not spec.needs_market_cap else market_cap is not None
    result = screen(rows, spec, market_cap=market_cap)

    return {
        "status": status,
        "reason": reason,
        "as_of": as_of,
        "adjust": "raw",
        "quote_source": quote_source,
        "coverage": {
            "quoted": scanned,
            "universe": universe,
            "ratio": coverage,
            "baseline_symbols": baseline.symbols,
            "baseline_as_of": baseline.as_of,
            "baseline_sessions": baseline.sessions,
            # 真实成交额只随快照来;日线口径下成交额是估算。
            "amount_available": quote_source == "snapshot",
            # 市值表只有最新一日快照,回看历史时会缺。
            "market_cap_available": market_cap_available,
        },
        "screen": describe_spec(spec),
        "matched": result.matched,
        "scanned": result.scanned,
        "truncated": result.truncated,
        "notes": list(result.notes),
        "rows": result.rows,
    }


def _run(date: str, fields: dict[str, Any]) -> dict[str, Any]:
    """``build_screen`` + 本模块的错误映射。条件非法 → HTTP 422。"""
    try:
        return build_screen(date, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception as exc:  # noqa: BLE001 — fail-open by contract
        logger.exception("screen failed")
        return {"status": "degraded", "reason": f"screen_failed: {exc}", "rows": []}


@router.post("/screen")
def post_screen_endpoint(
    request: Request, payload: dict[str, Any] = Body(...)
) -> APIResponse[Any]:
    """交互式选股:POST 一份结构化条件,返回命中列表(按条件排序、已截断)。

    ``status``:``ok`` / ``empty`` / ``degraded``;``coverage`` 报告截面完整度,
    ``notes`` 说明哪些行因缺数据被排除。条件本身不合法时 422。

    **同步函数,故意如此**:``build_screen`` 会做一次全市场读(冷启动十余秒),
    写成 ``async def`` 会把它放在事件循环上,期间整个进程的其它请求全部停摆。
    同步路由由 FastAPI 派进线程池,慢的只有这一个请求。请求体由 FastAPI 在
    派发前解析,畸形 JSON / 非对象 body 自动 422。
    """
    body = dict(payload)
    date = str(body.pop("date", "") or "")
    return _respond(request, _run(date, body))


@router.get("/screen")
def get_screen_endpoint(
    request: Request,
    date: str = Query("", description="YYYY-MM-DD,空=最新达标交易日"),
    price_min: str = Query(""),
    price_max: str = Query(""),
    change_pct_min: str = Query(""),
    change_pct_max: str = Query(""),
    boards_min: str = Query(""),
    boards_max: str = Query(""),
    volume_ratio_min: str = Query(""),
    volume_ratio_max: str = Query(""),
    turnover_min: str = Query(""),
    float_mv_min: str = Query(""),
    float_mv_max: str = Query(""),
    total_mv_min: str = Query(""),
    total_mv_max: str = Query(""),
    board_types: str = Query("", description="逗号分隔:main,kcb,cyb,bse"),
    exclude_st: str = Query(""),
    above_ma: str = Query(""),
    new_high: str = Query(""),
    new_low: str = Query(""),
    limit_up: str = Query(""),
    sort_by: str = Query("change_pct"),
    sort_desc: str = Query("true"),
    limit: str = Query("50"),
) -> APIResponse[Any]:
    """同 ``POST /ashare/screen``,参数走 query —— 便于把一次筛选存成链接。

    数值参数是字符串:空串 = 不限制。``sort_by`` / ``sort_desc`` / ``limit``
    在 query 里总有默认值,因此总会传给 spec(由 ``ScreenSpec`` 校验白名单)。
    """
    return _respond(
        request,
        _run(
            date,
            {
                "price_min": price_min,
                "price_max": price_max,
                "change_pct_min": change_pct_min,
                "change_pct_max": change_pct_max,
                "boards_min": boards_min,
                "boards_max": boards_max,
                "volume_ratio_min": volume_ratio_min,
                "volume_ratio_max": volume_ratio_max,
                "turnover_min": turnover_min,
                "float_mv_min": float_mv_min,
                "float_mv_max": float_mv_max,
                "total_mv_min": total_mv_min,
                "total_mv_max": total_mv_max,
                "board_types": board_types,
                "exclude_st": exclude_st,
                "above_ma": above_ma,
                "new_high": new_high,
                "new_low": new_low,
                "limit_up": limit_up,
                "sort_by": sort_by,
                "sort_desc": sort_desc,
                "limit": limit,
            },
        ),
    )

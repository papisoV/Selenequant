"""A-share watch-surface endpoints — 涨停池 / 连板梯队 / 情绪周期 / 板块排名.

Board-page aggregation endpoints. Unlike :mod:`app.api.v1.ashare_deep`
(which maps empty upstream data to 404 and failures to 502), these follow a
**fail-open** contract: the board page composes several blocks, and one dead
block must not blank the whole page. Each endpoint returns 200 with a
``status`` discriminator — ``ok`` / ``empty`` / ``degraded`` — and never
raises for upstream data problems. Genuine programming errors (e.g. a bad
``kind`` query value) still surface as HTTP 422/400.

Endpoints are synchronous ``def`` so FastAPI runs the blocking akshare/10jqka
calls in a threadpool.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from fastapi import APIRouter, Query, Request

from app.core.dependencies import (
    build_response_meta,
    get_kline_manager,
    get_market_service,
    get_settings_service,
)
from selene_space_types.common import APIResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ashare", tags=["ashare-watch"])

# 面板（B1 回填产物，gitignored）近 N 日窗口，供连板梯队/情绪周期趋势展示
_HISTORY_WINDOW = 30

# 偏离值最长的窗口是 30 日，需要 30 + 1 个基准日收盘 —— 少一根就整段不出信号。
_BASELINE_BARS = 31

# 中国无夏令时，固定 +08:00 即精确（与 astock_intraday_source 同约定）。
_CST_TZ = timezone(timedelta(hours=8))


def _clean(v: Any) -> Any:
    """Make a scalar JSON-safe: NaN/Inf/NaT -> None, numpy scalars -> python."""
    if v is None:
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    item = getattr(v, "item", None)
    if callable(item):
        try:
            return item()
        except (ValueError, TypeError):
            return v
    return v


def _records(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    """DataFrame -> list of JSON-safe row dicts (NaN/Inf nulled)."""
    if df is None or len(df) == 0:
        return []
    return [
        {k: _clean(v) for k, v in rec.items()} for rec in df.to_dict(orient="records")
    ]


def _panel_history(
    columns: list[str], window: int = _HISTORY_WINDOW
) -> list[dict[str, Any]]:
    """Last ``window`` rows of the backfilled ladder panel, or [] if absent.

    The panel is a gitignored artifact; a fresh checkout has none. Absence is
    not an error here — the caller reports ``degraded``.
    """
    from selene_space_ashare import market_phase

    path = market_phase.PANEL_PATH
    if not path.exists():
        return []
    panel = pd.read_parquet(path)
    keep = [c for c in columns if c in panel.columns]
    if "date" not in keep:
        return []
    tail = panel.sort_values("date").tail(window)
    return _records(tail[keep])


def _respond(request: Request, data: Any) -> APIResponse[Any]:
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


def _safe(fn: Any, *args: Any, **kwargs: Any) -> tuple[bool, Any]:
    """Run a blocking fetcher; return (ok, value). Never raises.

    An import failure (akshare/optional dep missing) and an upstream throw are
    both reported as not-ok so the caller can degrade instead of 5xx-ing.
    """
    try:
        return True, fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 — fail-open by contract
        return False, None


def _session_of(rows: list[dict[str, Any]]) -> str | None:
    """The session date carried by the rows themselves, when they carry one.

    The pool rows come back with a ``date`` column; reporting *that* is what
    lets the card header say which session the numbers belong to. Echoing the
    request's ``date`` query instead conflates "what you asked for" with "what
    you got" — they differ whenever the caller omits the param or the upstream
    answers a different session (weekend query, stale endpoint).
    """
    if not rows:
        return None
    raw = rows[0].get("date")
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    # Upstream has answered both ``20261021`` and ``2026-10-21``; normalise to
    # the compact form the rest of the board uses.
    return text.replace("-", "")[:8] or None


@router.get("/limit-up-pool")
def get_limit_up_pool_endpoint(
    request: Request,
    date: str = Query("", description="YYYYMMDD,空=今日"),
) -> APIResponse[Any]:
    """涨停池 + 涨停/跌停统计(今日或指定交易日)。"""
    from selene_space_ashare.ths_data import (
        fetch_limit_up_pool_full,
        fetch_limit_up_stats,
    )

    ok_pool, pool = _safe(fetch_limit_up_pool_full, date)
    ok_stats, stats = _safe(fetch_limit_up_stats, date)

    rows = _records(pool) if ok_pool else []
    payload: dict[str, Any] = {
        # 实际数据的日期,不是入参:裸调时 ``date`` 是 ""，若原样回给前端，
        # 卡片头部要么空白、要么把调用方传的查询参数当成数据日期显示。
        # 池子有行时取会话日（池子本身就是当日快照），否则退回入参。
        "date": _session_of(rows) or date or None,
        "pool": rows,
        "pool_count": len(rows),
        "stats": stats if (ok_stats and isinstance(stats, dict)) else {},
    }
    payload["status"] = "ok" if rows or payload["stats"] else "empty"
    return _respond(request, payload)


def _live_sealed_counts() -> tuple[dict[str, Any] | None, str | None]:
    """真封/假封 counts from the whole-market snapshot's level-1 depth.

    Returns ``(counts, error)``. ``counts`` is ``None`` when the live path is
    unavailable, with ``error`` saying why — an absent cross-section must not
    be reported as "0 真封", which reads as a wiped-out tape.

    The book is only meaningful in continuous trading, so the classifier
    refuses the auction windows and returns a ``None`` verdict there; those
    land in ``undecided`` rather than being guessed either way (see
    :mod:`selene_space_ashare.sealed`).
    """
    svc = get_market_service()
    if svc is None:
        return None, "market_service_unavailable"
    try:
        src = svc.get_source("astock", "market_snapshot")
    except Exception as exc:  # noqa: BLE001 — fail-open by contract
        return None, f"source_lookup_failed: {exc}"
    if src is None or not hasattr(src, "get_market_rows"):
        return None, "snapshot_source_absent"
    try:
        rows = src.get_market_rows()
    except Exception as exc:  # noqa: BLE001
        return None, f"snapshot_read_failed: {exc}"
    if not rows:
        return None, "snapshot_empty"

    from selene_space_ashare import sealed

    try:
        return sealed.sealed_counts(rows), None
    except Exception as exc:  # noqa: BLE001 — a classification failure degrades
        return None, f"sealed_counts_failed: {exc}"


def _live_ladder_history(
    columns: list[str], window: int
) -> tuple[list[dict[str, Any]], str | None]:
    """Recent ladder trend from the public 10jqka history; ``([], reason)`` on failure.

    The backfilled panel is a *static* artifact that the deployment box never
    builds (it needs a whole-market daily history), so its last date can be
    years behind the desk. The endpoint-derived history reaches back only about
    twenty sessions, but those are the sessions actually being traded.
    """
    from selene_space_ashare import ladder_live

    try:
        panel = ladder_live.live_ladder_history()
    except Exception as exc:  # noqa: BLE001 — fail-open by contract
        return [], f"live_history_failed: {exc}"
    if panel is None or panel.empty:
        return [], "live_history_empty"
    keep = [c for c in columns if c in panel.columns]
    if "date" not in keep:
        return [], "live_history_missing_date"
    tail = panel.sort_values("date").tail(window)
    return _records(tail[keep]), None


@router.get("/ladder")
def get_ladder_endpoint(request: Request) -> APIResponse[Any]:
    """连板梯队 — 今日宽度(实时) + 近 N 日趋势。

    今日行由**两个** 10jqka 端点合成:``limit_up_pool`` 给全部涨停(含首板),
    ``continuous_limit_up`` 给真连板数(``continue_num``,只列 ≥2 板)。
    二者之差恰是首板集。**只用 continuous 会漏掉整个首板层** —— 它的
    ``first_board`` 恒为 0,梯队宽度是假的。

    趋势同样取自端点(近约二十个交易日);回填面板只是退路 —— 它是静态产物,
    部署机从不构建,最后一日可能落后数年。``history_source`` 标出实际来源。
    """
    from selene_space_ashare.ladder import build_ladder_daily, ladder_history_from_pool
    from selene_space_ashare.ths_data import (
        fetch_continuous_limit_up,
        fetch_limit_up_pool_full,
    )

    trend_cols = [
        "date",
        "first_board",
        "ge2_count",
        "ge3_count",
        "ge5_count",
        "max_consecutive",
        "promo_rate",
        "seal_rate",
        "limit_up_count",
    ]

    today: dict[str, Any] | None = None
    date: str | None = None
    # Both fetchers are pinned to the same CST session. The pool request sends
    # an explicit `date=<local today>` while the continuous request, left empty,
    # uses the endpoint's own notion of "today" — on a box not set to +08:00
    # those can disagree (between 16:00–24:00 UTC), and the 首板 difference is
    # then computed across two different sessions. Passing the CST date makes
    # both name one session; on a CST box it is the same value as before.
    session = _now_cst().strftime("%Y%m%d")
    # ``force=True``: this row is today's live breadth, and the pool/continuous
    # caches are date-keyed with **no intra-day age check** — the pool returns
    # any non-empty cached frame regardless of age. Without the bypass the first
    # request after 09:30 freezes the morning's partial pool for the rest of the
    # session, so "今日涨停" would be hours stale while the UI labels it 实时.
    # Both fetches are one page each, so the refetch is cheap.
    # Strict mode: the fetchers otherwise swallow a transport failure into an
    # empty frame, so `ok_cont` / `ok_pool` would be true for a dead endpoint
    # and the guard below would never fire. A genuinely empty answer still
    # returns an empty frame (no raise), which is what makes "degraded upstream"
    # and "quiet tape" distinguishable.
    ok_cont, cont = _safe(
        fetch_continuous_limit_up, date=session, force=True, strict=True
    )
    ok_pool, pool = _safe(
        fetch_limit_up_pool_full, date=session, force=True, strict=True
    )
    # Both endpoints are required: without the pool there is no 首板 layer, and
    # without the continuous list every pool name would be mislabelled a 首板.
    # A one-sided fetch is a degraded upstream, not an empty tape.
    if ok_cont and ok_pool:
        hist = ladder_history_from_pool(pool, cont)
        if not hist.empty:
            daily = build_ladder_daily(hist)
            if not daily.empty:
                row = _records(daily.tail(1))[0]
                today = row
                date = str(row.get("date") or "") or None

    history, live_reason = _live_ladder_history(trend_cols, _HISTORY_WINDOW)
    if history:
        history_source = "live_snapshot"
    else:
        history = _panel_history(trend_cols)
        history_source = "backfill_panel" if history else "none"

    payload: dict[str, Any] = {
        "date": date,
        "today": today,
        "history": history,
        "history_count": len(history),
        "history_source": history_source,
        "history_reason": live_reason,
    }
    # 真封/假封 split from the free level-1 depth (see sealed module). Kept
    # out of `payload["status"]`: the ladder row is authoritative for the
    # session's existence, and a missing book must not downgrade a good ladder
    # to `empty`. `sealed` is null with a reason when the book is unavailable
    # (or when today's quotes sit in an auction window), so the UI can say
    # "未知" instead of rendering a 0 that looks like a wiped-out tape.
    # Guarded independently of the ladder: a classification bug must not blank
    # a block that is otherwise fine (the page's fail-open contract).
    try:
        sealed_counts, sealed_reason = _live_sealed_counts()
    except Exception as exc:  # noqa: BLE001 — degrade the block, not the page
        sealed_counts, sealed_reason = None, f"sealed_counts_failed: {exc}"
    payload["sealed"] = sealed_counts
    payload["sealed_reason"] = sealed_reason
    payload["status"] = "ok" if (today or history) else "empty"
    return _respond(request, payload)


def _live_phase_rows(
    window: int,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str | None, str | None]:
    """Live phase series from the whole-market snapshot.

    Returns ``(series, current, label_scope, error)``. ``series`` is empty when
    the live path is unavailable, in which case ``error`` says why so the
    caller can report the degradation instead of silently serving the stale
    panel as if it were live.

    The closed history is assembled from the public 10jqka endpoints (see
    :mod:`selene_space_ashare.ladder_live`) because the deployment box has no
    whole-market daily history to backfill from; only the final row is folded
    from a session that has not closed, hence ``label_scope``.
    """
    svc = get_market_service()
    if svc is None:
        return [], None, None, "market_service_unavailable"
    try:
        src = svc.get_source("astock", "market_snapshot")
    except Exception as exc:  # noqa: BLE001 — fail-open by contract
        return [], None, None, f"source_lookup_failed: {exc}"
    if src is None or not hasattr(src, "get_market_rows"):
        return [], None, None, "snapshot_source_absent"
    try:
        rows = src.get_market_rows()
    except Exception as exc:  # noqa: BLE001
        return [], None, None, f"snapshot_read_failed: {exc}"
    if not rows:
        return [], None, None, "snapshot_empty"

    from selene_space_ashare import ladder_live

    try:
        labeled, scope = ladder_live.live_phase_series(rows)
    except Exception as exc:  # noqa: BLE001 — classify/aggregate failure degrades
        return [], None, None, f"live_phase_failed: {exc}"

    cols = ["date", "phase", "max_consecutive", "ge2_count", "promo_rate", "seal_rate"]
    keep = [c for c in cols if c in labeled.columns]
    series = _records(labeled.sort_values("date").tail(window)[keep])
    return series, (series[-1] if series else None), scope, None


@router.get("/phase")
def get_phase_endpoint(
    request: Request,
    window: int = Query(_HISTORY_WINDOW, ge=1, le=400, description="返回最近 N 日"),
    live: bool = Query(
        True, description="盘中:用全市场快照合成今日暂定标签;源不可用时退回回填面板"
    ),
) -> APIResponse[Any]:
    """情绪周期六阶段(冰点/启动/主升/高潮/退潮/修复)。

    优先走**实时档**:全市场快照当日横截面接在近 N 日已收盘历史之后,由
    ``market_phase`` 标定。最后一行是**盘中暂定**值 —— ``label_scope`` 为
    ``intraday_provisional`` 时前端必须标"盘中暂定",不得当作收盘定版。

    实时档不可用(快照源未注册/为空、端点不可达、无标定)时退回**回填面板档**:
    ``as_of`` 是面板最后一日而非自然今日,前端须据此展示"截至 X 日"。
    ``live_reason`` 说明为何退回,便于排查 —— 降级不静默。
    """
    live_series: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    label_scope: str | None = None
    live_reason: str | None = None

    if live:
        live_series, current, label_scope, live_reason = _live_phase_rows(window)

    if live_series:
        return _respond(
            request,
            {
                "status": "ok",
                "source": "live_snapshot",
                "as_of": current.get("date") if current else None,
                "label_scope": label_scope or "closed_history",
                "current": current,
                "series": live_series,
            },
        )

    from selene_space_ashare import market_phase

    path = market_phase.PANEL_PATH
    if not path.exists():
        return _respond(
            request,
            {
                "status": "degraded",
                "reason": "panel_missing",
                "live_reason": live_reason,
                "hint": "未找到标定面板;先跑 scripts/backfill_ladder_panel.py",
                "current": None,
                "series": [],
            },
        )

    ok, reason = market_phase.calibration_status(path)
    panel = pd.read_parquet(path)
    try:
        labeled = market_phase.classify_phase_series(panel)
    except Exception as exc:  # noqa: BLE001 — fail-open; report instead of 5xx
        return _respond(
            request,
            {
                "status": "degraded",
                "reason": f"classify_failed: {exc}",
                "live_reason": live_reason,
                "current": None,
                "series": [],
            },
        )

    cols = ["date", "phase", "max_consecutive", "ge2_count", "promo_rate", "seal_rate"]
    keep = [c for c in cols if c in labeled.columns]
    series = _records(labeled.sort_values("date").tail(window)[keep])
    current = series[-1] if series else None
    return _respond(
        request,
        {
            "status": "ok" if series else "empty",
            "source": "backfill_panel",
            "as_of": current.get("date") if current else None,
            "calibrated": bool(ok),
            "calibration_reason": reason,
            "live_reason": live_reason,
            "current": current,
            "series": series,
        },
    )


@router.get("/boards")
def get_boards_endpoint(
    request: Request,
    kind: str = Query(
        "industry", pattern="^(industry|concept)$", description="industry|concept"
    ),
    top_n: int = Query(60, ge=1, le=200),
) -> APIResponse[Any]:
    """板块当日表现排名 — 行业(industry) 或 概念(concept)。"""
    from selene_space_ashare import data_fetcher

    if kind == "concept":
        ok, df = _safe(data_fetcher.get_concept_boards, top_n)
    else:
        ok, df = _safe(data_fetcher.get_industry_comparison, top_n)

    rows = _records(df) if ok else []
    return _respond(
        request,
        {
            "kind": kind,
            "boards": rows,
            "count": len(rows),
            "status": "ok" if rows else "empty",
        },
    )


def _ashare_favorites() -> list[str]:
    """当前自选里的 A股代码(偏离值只对 A股有定义),统一成带后缀形态。

    读自选而非全市场:偏离值是逐标的的监控项,用户自选即其关注面;
    全市场扫描既慢又无意义。

    自选存的是裸码(前端 ``toPair`` 口径),而本模块全部下游 —— KlineDB
    分区键、``bench_candidates_of`` 的板块判定、盘中源的 ``to_sina_symbol``
    —— 都要求 ``600519.SH`` 形态。此前直接拿裸码过 ``is_ashare_symbol``,
    它要求后缀 ⇒ 恒为 False ⇒ 本函数恒返回 ``[]``,/deviation 对真实用户
    永远是 ``no_ashare_favorites``。故先按路由域筛,再在边界补后缀。
    """
    from app.core.source_routing import market_domain, to_ashare_symbol

    svc = get_settings_service()
    if svc is None:
        return []
    return [
        to_ashare_symbol(s)
        for s in svc.get_favorite_symbols()
        if market_domain(s) == "ashare"
    ]


@router.get("/deviation")
async def get_deviation_endpoint(
    request: Request,
    live: bool = Query(
        True, description="盘中:叠加今日实时涨跌(源不可用时自动退回日频)"
    ),
) -> APIResponse[Any]:
    """交易所异动偏离/接近度 — 自选 A股 的 N 日累计偏离(3/10/30)。

    个股收盘取 KlineDB ``astock``/``spot``(raw 优先),基准指数取
    ``data_fetcher.get_index_daily``;``live=True`` 时尝试叠加今日实时涨跌,
    实时源不可用则自动退回日频(``rows[].intraday`` 标出实际口径)。
    fail-open:无自选/无日线/无基准都只降级,不 5xx。
    """
    from app.astock_deviation import collect_live_quotes, deviation_report
    from selene_space_ashare import data_fetcher
    from selene_space_ashare.abnormal import bench_candidates_of

    symbols = _ashare_favorites()
    if not symbols:
        return _respond(
            request,
            {
                "status": "empty",
                "reason": "no_ashare_favorites",
                "hint": "自选里加入 A股代码(如 600519.SH)后此表才有内容",
                "as_of": _now_cst().strftime("%Y%m%d"),
                "live": False,
                "counts": {"triggered": 0, "edge": 0, "watch": 0},
                "rows": [],
                "skipped": [],
            },
        )

    manager = get_kline_manager()
    if manager is None:
        return _respond(
            request,
            {
                "status": "degraded",
                "reason": "kline_manager_unavailable",
                "as_of": _now_cst().strftime("%Y%m%d"),
                "live": False,
                "counts": {"triggered": 0, "edge": 0, "watch": 0},
                "rows": [],
                "skipped": [{"symbol": s, "reason": "no_kline_store"} for s in symbols],
            },
        )

    today = _now_cst()
    live_quotes: dict[str, tuple[str, float]] = {}
    if live:
        # 基准指数代码也要一起取实时价;否则叠加只有个股侧,反而失真。
        fetch_syms = list(
            dict.fromkeys(
                [*symbols, *(c for s in symbols for c in bench_candidates_of(s))]
            )
        )
        live_quotes = await collect_live_quotes(get_market_service(), fetch_syms)

    report = deviation_report(
        symbols=symbols,
        kline_db=manager._db,
        index_daily_fn=data_fetcher.get_index_daily,
        today=today,
        live_quotes=live_quotes,
    )
    report["status"] = "ok" if report["rows"] else "empty"
    return _respond(request, report)


# -- 概念轮动 / 主线识别 -----------------------------------------------------
#
# Both read the whole-market daily base under ("astock","daily_all","1d") and
# the 同花顺 membership snapshot from ``concept_map``. Neither is a live
# upstream call in the usual sense: the base is already on disk, and the
# membership fetch is disk-cached for a day. That is why these two endpoints
# keep the fail-open contract but *degrade* rather than ``empty`` when the
# membership source is unreachable — the computation itself still works, it
# just has nothing to group by.
#
# ``days`` is validated by FastAPI (ge/le) so an out-of-range value is a 422
# before any work happens. The module clamps as well; the two bounds are the
# same constants and the clamp is the belt to FastAPI's braces.

_ROTATION_DAYS_MAX = 30
_ROTATION_DAYS_MIN = 7
_MAINLINE_DAYS_MAX = 30
_MAINLINE_DAYS_MIN = 7


def _membership_or_empty() -> tuple[pd.DataFrame, str | None]:
    """Fetch concept membership; return ``(frame, reason)`` and never raise."""
    from selene_space_ashare.concept_map import fetch_concept_membership

    ok, frame = _safe(fetch_concept_membership)
    if not ok or frame is None or getattr(frame, "empty", True):
        return pd.DataFrame(columns=["symbol", "name", "concept"]), (
            "concept_membership_unavailable" if not ok else "concept_membership_empty"
        )
    return frame, None


def _daily_db() -> Any:
    manager = get_kline_manager()
    return getattr(manager, "_db", None) if manager is not None else None


@router.get("/rotation")
def get_rotation_endpoint(
    request: Request,
    days: int = Query(12, ge=_ROTATION_DAYS_MIN, le=_ROTATION_DAYS_MAX),
) -> APIResponse[Any]:
    """概念轮动矩阵 — 近 ``days`` 个交易日各概念的等权平均涨幅。

    与 ``/boards?kind=concept`` 不是同一件事: 那个是东财当日的板块涨幅
    (板块自身的指数口径), 这个是按同花顺成分股逐日等权复算的个股口径,
    可以回看多日, 也可以横向比较"哪个概念在持续领先"。
    """
    from selene_space_ashare.concept_map import MEMBERSHIP_NOTE
    from selene_space_ashare.concept_rotation import build_rotation_matrix

    db = _daily_db()
    if db is None:
        return _respond(
            request,
            {
                "status": "degraded",
                "reason": "kline_manager_unavailable",
                "days": days,
                "membership_note": MEMBERSHIP_NOTE,
                "dates": [],
                "columns": {},
                "concept_count": 0,
                "as_of": None,
            },
        )

    membership, membership_reason = _membership_or_empty()
    try:
        result = build_rotation_matrix(db, membership, days=days)
    except Exception:  # noqa: BLE001 — fail-open by contract
        logger.exception("rotation computation failed")
        return _respond(
            request,
            {
                "status": "degraded",
                "reason": "computation_failed",
                "days": days,
                "membership_note": MEMBERSHIP_NOTE,
                "dates": [],
                "columns": {},
                "concept_count": 0,
                "as_of": None,
            },
        )

    rows = result.get("dates") or []
    # A missing membership source and an empty matrix are different states: the
    # first says "we could not group anything", the second "the grouping is
    # fine but nothing scored". Collapsing them would hide an upstream outage.
    if membership_reason is not None:
        status = "degraded"
        reason = membership_reason
    elif rows:
        status = "ok"
        reason = None
    else:
        status = "empty"
        reason = result.get("reason")

    return _respond(
        request,
        {
            "status": status,
            "reason": reason,
            "days": days,
            "membership_note": result.get("membership_note", MEMBERSHIP_NOTE),
            "dates": rows,
            "columns": result.get("columns") or {},
            "concept_count": result.get("concept_count", 0),
            "as_of": result.get("as_of"),
        },
    )


@router.get("/mainline")
def get_mainline_endpoint(
    request: Request,
    days: int = Query(20, ge=_MAINLINE_DAYS_MIN, le=_MAINLINE_DAYS_MAX),
    exclude_st: bool = Query(True, description="剔除 ST/退市整理类名称"),
) -> APIResponse[Any]:
    """主线识别 — 近 ``days`` 个交易日各概念的涨停梯队强度排名。

    ``streak_capped`` 恒为 true: 连板数只能看到窗口内, 窗口外的历史段
    不可见(日线底座没有 ``consecutive_limit_ups`` 富集列)。这是口径,
    不是错误, 前端必须照实展示。
    """
    from selene_space_ashare import mainline as ml

    db = _daily_db()
    if db is None:
        return _respond(
            request,
            {
                "status": "degraded",
                "reason": "kline_manager_unavailable",
                "days": days,
                "streak_capped": True,
                "membership_note": ml.MEMBERSHIP_NOTE,
                "dates": [],
                "columns": {},
                "as_of": None,
            },
        )

    membership, membership_reason = _membership_or_empty()
    try:
        result = ml.compute_mainline(
            db, membership, days=days, exclude_st_names=exclude_st
        )
    except Exception:  # noqa: BLE001 — fail-open by contract
        logger.exception("mainline computation failed")
        return _respond(
            request,
            {
                "status": "degraded",
                "reason": "computation_failed",
                "days": days,
                "streak_capped": True,
                "membership_note": ml.MEMBERSHIP_NOTE,
                "dates": [],
                "columns": {},
                "as_of": None,
            },
        )

    rows = result.get("dates") or []
    if membership_reason is not None:
        status = "degraded"
        reason = membership_reason
    elif rows:
        status = "ok"
        reason = None
    else:
        status = "empty"
        reason = result.get("reason")

    return _respond(
        request,
        {
            "status": status,
            "reason": reason,
            "days": days,
            "streak_capped": True,
            "membership_note": result.get("membership_note", ml.MEMBERSHIP_NOTE),
            "dates": rows,
            "columns": result.get("columns") or {},
            "as_of": result.get("as_of"),
        },
    )


def _now_cst() -> datetime:
    return datetime.now(_CST_TZ)


# -- 交易时段 ------------------------------------------------------------------
#
# The board page polls nine endpoints. Off-hours every one of those polls returns
# the same frozen cross-section, so a page left open on a Saturday re-fetches the
# whole board forever to no effect. The front end cannot decide this on its own:
# it would have to reproduce the XSHG calendar and the +08:00 conversion, and a
# clock-skewed or holiday-naive client would either poll after the close or stay
# dark through a session.
#
# ``market_phase`` here is ``selene_space_risk.trading_calendar``'s — the
# intraday session clock (PRE_OPEN / AUCTION / OPEN / LUNCH_BREAK /
# CLOSING_AUCTION / CLOSED). It is NOT ``selene_space_ashare.market_phase``,
# which despite the name classifies the *sentiment cycle* (冰点/启动/主升/…)
# and backs the ``/phase`` endpoint. Two different questions, same function name.
#
# ``polling`` is the coarse bit the UI needs; ``phase`` is the precise label, so
# a reader can tell "lunch break" from "holiday" without a second request.
_POLLING_PHASES = frozenset(
    {"PRE_OPEN", "AUCTION", "OPEN", "LUNCH_BREAK", "CLOSING_AUCTION"}
)


@router.get("/session")
def get_session_endpoint(
    request: Request,
    now: str = Query(
        "", description="ISO8601 覆盖当前时刻(仅测试/回放用,正常调用留空)"
    ),
) -> APIResponse[Any]:
    """当前 A 股交易时段判定 —— 前端据此决定是否轮询看盘台。

    ``polling`` 为 true 时表示「此刻的截面还会变」:盘前竞价、集合竞价、连续
    竞价、午休、收盘竞价都算(午休期间盘口冻结,但 13:00 一开盘就又活了,
    停掉轮询再冷启动比多轮询几次更贵)。``CLOSED`` 含非交易日 ——
    ``phase`` 只说 CLOSED,``is_trading_day`` 再区分「今天是休息日」还是
    「今天收了盘」,两者在前端的文案不同。

    ``as_of`` 是判定所用的北京时间(带 +08:00),前端拿它显示"截至 xx:xx",
    不要用浏览器本地时间 —— 那在非东八区机器上会差出整整一个交易时段。
    """
    from selene_space_risk.trading_calendar import is_trading_day, market_phase

    if now:
        try:
            moment = datetime.fromisoformat(now)
        except ValueError:
            # 显式 400 而不是静默回落到当前时刻:测试帧写错却"看起来正常"
            # 比直接报错更难查,这一路是给回放/测试用的。
            from fastapi import HTTPException

            raise HTTPException(
                status_code=400, detail=f"bad 'now' (want ISO8601): {now}"
            )
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=_CST_TZ)
    else:
        moment = _now_cst()

    beijing = moment.astimezone(_CST_TZ)
    naive = beijing.replace(tzinfo=None)
    phase = str(market_phase(naive))
    trading_day = bool(is_trading_day(naive))

    return _respond(
        request,
        {
            "status": "ok",
            "phase": phase,
            "polling": phase in _POLLING_PHASES,
            "is_trading_day": trading_day,
            "as_of": beijing.isoformat(timespec="seconds"),
        },
    )


# -- 概念 RPS -----------------------------------------------------------------
#
# ``lookback`` is sessions, not calendar days, and it is deliberately not called
# ``days``: the same page shows ``/rotation?days=12``, and two fields with the
# same name but different units is how a reader ends up comparing 12 with 120.
# The bounds mirror ``concept_rps.MIN_LOOKBACK`` / ``MAX_LOOKBACK``; a test pins
# the pairing because the package is imported lazily here and the numbers are
# necessarily duplicated.

_CONCEPT_RPS_LOOKBACK_MIN = 20
_CONCEPT_RPS_LOOKBACK_MAX = 250


@router.get("/concept-rps")
def get_concept_rps_endpoint(
    request: Request,
    lookback: int = Query(
        120, ge=_CONCEPT_RPS_LOOKBACK_MIN, le=_CONCEPT_RPS_LOOKBACK_MAX
    ),
) -> APIResponse[Any]:
    """概念强度 RPS — 近 ``lookback`` 个交易日各概念等权成员涨幅的截面百分位。

    与 ``/rotation`` 的区别是口径而不是窗口: 那个是逐日矩阵(看节奏),
    这个是单一截面(看排名)。``is_strong`` 由服务端算好 (rps >= 90 且成员数
    落在 solid 档), 前端不得自行用 ``rps >= 90`` 复算 —— 那会漏掉"成员太少,
    排名不稳"这半个条件。
    """
    from selene_space_ashare.concept_map import MEMBERSHIP_NOTE
    from selene_space_ashare.concept_rps import DRIFT_NOTE, build_concept_rps

    base: dict[str, Any] = {
        "lookback": lookback,
        "concepts": [],
        "concept_count": 0,
        "as_of": None,
        "membership_note": MEMBERSHIP_NOTE,
        "drift_note": DRIFT_NOTE,
    }

    db = _daily_db()
    if db is None:
        return _respond(
            request,
            {**base, "status": "degraded", "reason": "kline_manager_unavailable"},
        )

    membership, membership_reason = _membership_or_empty()
    try:
        result = build_concept_rps(db, membership, lookback=lookback)
    except Exception:  # noqa: BLE001 — fail-open by contract
        logger.exception("concept rps computation failed")
        return _respond(
            request, {**base, "status": "degraded", "reason": "computation_failed"}
        )

    rows = result.get("concepts") or []
    # Same three-way split as /rotation, and for the same reason: "we could not
    # group anything" (degraded), "nothing scored" (empty), and "a programming
    # error" (computation_failed) must not collapse into one another.
    if membership_reason is not None:
        status, reason = "degraded", membership_reason
    elif rows:
        status, reason = "ok", None
    else:
        status, reason = "empty", result.get("reason")

    return _respond(
        request,
        {
            "status": status,
            "reason": reason,
            "lookback": result.get("lookback", lookback),
            "concepts": rows,
            "concept_count": result.get("concept_count", 0),
            "as_of": result.get("as_of"),
            "membership_note": result.get("membership_note", MEMBERSHIP_NOTE),
            "drift_note": result.get("drift_note", DRIFT_NOTE),
        },
    )

# Pattern absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""B — 交互式选股器:结构化条件 → 一个交易日截面 → 命中列表。

## 与 TSP 的关系(读之前先知道)

TSP 的选股器(`backend/app/services/screener.py` + `frontend/src/lib/screener` /
`ScreenerFilter.tsx`)被当作**规格**读:条件集合、区间含端语义、「市值/量比/
涨跌幅/板块/排除 ST」这些字段怎么组合。**执行方式完全不同**:TSP 把
`kline_daily_enriched`(预计算 ma/量比/连板)交给 DuckDB 跑用户拼的 WHERE
字符串;selene 没有那张富集表,指标由 ``compute_baseline`` 现算,筛选就是
对 ~5k 行做布尔掩码(实测 66 ms)。所以这里**不引入任何新依赖**,也没有
「用户传 SQL」的能力 —— 那是另一条提案(方案 B),用户已明确否掉。

## 缺失值语义(本模块最容易被读错的一条)

数值下限/上限是**逐行判定**的:值为 ``None``/NaN/inf 的行,在设了下限或上限
时**一律不通过**。理由:量比 1.5 的下限问的是"今天的量是过去 5 日均量的
1.5 倍以上",一只没有 5 日基线的次新股**不是**"满足条件",而是"无法回答"。
把它算进去会给出一个看起来像信号的假阳性。未设上下限时该列不参与判定。
``exclude_st`` / ``board`` / ``limit_up`` 是分类条件,不适用这条。

## 成交额的两种口径(必须逐行区分)

快照横截面带交易所自己的 ``amount``(真值);日线回退只有 ``close × volume``
(估算,收盘价不是 VWAP)。``attach_turnover`` 逐行选口径并打 ``turnover_est``
标记 —— 两种口径在同一张表里混排是允许的,但**必须标出来**,否则读者会把
估算值当成成交额。

``amount`` 在日线底座里不存在,这是 ``market_daily`` 模块的既定决定(Sina
日线端点不发布成交额),不是本模块的省略。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

from selene_space_ashare.market_rules import board_of, is_st

# 板块代码 —— 与 ``market_rules.board_of`` 的返回值同一词表,不另起一套。
BOARDS: tuple[str, ...] = ("main", "kcb", "cyb", "bse")

# 均线站位的可选项。键是 ``build_quote_rows`` 产出的列名,值是展示名由调用方决定。
MA_WINDOWS: tuple[str, ...] = ("ma5", "ma20", "ma60")

# 可排序字段。白名单而非 ``row[key]`` 直取:排序键来自请求体,不能拼进任何
# 动态表达式(即便这里只是 Python 字典取值,白名单也让"拼错字段名"变成 422
# 而不是静默按 None 全排到末尾)。
SORT_KEYS: tuple[str, ...] = (
    "change_pct",
    "boards",
    "volume_ratio",
    "turnover",
    "float_mv",
    "total_mv",
    "last",
)

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


def _finite(value: Any) -> float | None:
    """Anything → finite float or ``None``. NaN/inf are "no data", never a value."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _check_range(label: str, low: float | None, high: float | None) -> None:
    for bound, value in (("下限", low), ("上限", high)):
        if value is not None and not math.isfinite(float(value)):
            raise ValueError(f"{label}{bound}必须是有限数值,收到 {value!r}")
    if low is not None and high is not None and low > high:
        raise ValueError(f"{label}区间倒挂: 下限 {low} 大于上限 {high}")


@dataclass(frozen=True)
class ScreenSpec:
    """一条筛选请求。所有区间含端点,``None`` 表示不限制。

    字段名称与单位是**对外契约**:``change_pct`` 是百分数(``1.5`` 表示 +1.5%),
    与 ``/ashare/overview`` 一致(不是 ``/ashare/deviation`` 的小数口径)。
    市值与成交额的单位是**元**,换算成"亿"是展示层的事。

    ``boards_min`` / ``boards_max`` 是**连板数**(行里的 ``boards`` 列);
    ``board_types`` 是**板块**(主板/科创/创业/北交)。两者一个叫板一个叫数,
    故意不共用 ``boards`` 这个名字 —— 行字典里的 ``boards`` 是连板数,
    spec 里再用 ``boards`` 表示板块会让两个含义在同一模块里撞车。
    """

    price_min: float | None = None
    price_max: float | None = None
    change_pct_min: float | None = None
    change_pct_max: float | None = None
    boards_min: int | None = None
    boards_max: int | None = None
    volume_ratio_min: float | None = None
    volume_ratio_max: float | None = None
    turnover_min: float | None = None
    float_mv_min: float | None = None
    float_mv_max: float | None = None
    total_mv_min: float | None = None
    total_mv_max: float | None = None
    board_types: tuple[str, ...] = ()
    exclude_st: bool = False
    above_ma: str | None = None
    new_high: bool = False
    new_low: bool = False
    limit_up: bool | None = None
    sort_by: str = "change_pct"
    sort_desc: bool = True
    limit: int = DEFAULT_LIMIT

    def __post_init__(self) -> None:
        _check_range("现价", self.price_min, self.price_max)
        _check_range("涨跌幅", self.change_pct_min, self.change_pct_max)
        _check_range("量比", self.volume_ratio_min, self.volume_ratio_max)
        _check_range("流通市值", self.float_mv_min, self.float_mv_max)
        _check_range("总市值", self.total_mv_min, self.total_mv_max)
        if self.boards_min is not None and self.boards_max is not None:
            if self.boards_min > self.boards_max:
                raise ValueError(
                    f"连板数区间倒挂: 下限 {self.boards_min} 大于上限 {self.boards_max}"
                )
        if self.boards_min is not None and self.boards_min < 0:
            raise ValueError("连板数下限不能为负")
        unknown = [b for b in self.board_types if b not in BOARDS]
        if unknown:
            raise ValueError(f"未知板块代码 {unknown};可选 {list(BOARDS)}")
        if self.above_ma is not None and self.above_ma not in MA_WINDOWS:
            raise ValueError(
                f"above_ma 必须是 {list(MA_WINDOWS)} 之一,收到 {self.above_ma!r}"
            )
        if self.sort_by not in SORT_KEYS:
            raise ValueError(
                f"sort_by 必须是 {list(SORT_KEYS)} 之一,收到 {self.sort_by!r}"
            )
        if not 1 <= int(self.limit) <= MAX_LIMIT:
            raise ValueError(f"limit 必须在 1..{MAX_LIMIT} 之间,收到 {self.limit!r}")

    @property
    def needs_change_pct(self) -> bool:
        return self.change_pct_min is not None or self.change_pct_max is not None

    @property
    def needs_market_cap(self) -> bool:
        """市值条件是否被用到 —— 调用方据此决定要不要去取市值快照(冷启动 23 s)。"""
        return any(
            value is not None
            for value in (
                self.float_mv_min,
                self.float_mv_max,
                self.total_mv_min,
                self.total_mv_max,
            )
        )


@dataclass(frozen=True)
class ScreenResult:
    """筛选结果。``matched`` 是截断前的命中数,``rows`` 是截断后要展示的行。"""

    rows: list[dict[str, Any]] = field(default_factory=list)
    matched: int = 0
    scanned: int = 0
    notes: tuple[str, ...] = ()

    @property
    def truncated(self) -> bool:
        return self.matched > len(self.rows)


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------


def _code_of(symbol: Any) -> str:
    return str(symbol or "").split(".", 1)[0]


def _board_of_row(row: Mapping[str, Any]) -> str:
    """板块代码。优先用行里已算好的 ``board``(调用方可选提供),否则按代码判定。

    ``build_quote_rows`` **不**产出 ``board`` —— 板块在这里按需算,避免为了
    一个可选条件给每一行都加一次判定。
    """
    value = row.get("board")
    if isinstance(value, str) and value in BOARDS:
        return value
    return board_of(_code_of(row.get("symbol")))


def attach_turnover(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """每行补 ``turnover``(元)与 ``turnover_est``(是否估算口径)。

    优先用快照的真实成交额;没有时退到 ``close × volume``,并**明确打标**。
    两者都没有 → ``turnover`` 为 ``None``(未知),绝不写 0 —— 0 读作"没成交",
    与"取不到"是两件事。
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        amount = _finite(row.get("amount"))
        if amount is not None and amount > 0:
            item["turnover"] = amount
            item["turnover_est"] = False
        else:
            close = _finite(row.get("last"))
            volume = _finite(row.get("volume"))
            if close is not None and volume is not None and close > 0 and volume > 0:
                item["turnover"] = close * volume
                item["turnover_est"] = True
            else:
                item["turnover"] = None
                item["turnover_est"] = False
        out.append(item)
    return out


def attach_market_cap(
    rows: Sequence[Mapping[str, Any]], market_cap: pd.DataFrame | None
) -> tuple[list[dict[str, Any]], int]:
    """按裸码 join 流通/总市值;返回 ``(rows, 缺市值的行数)``。

    缺市值不是错误:次新股、停牌太久的票可能不在东财当日估值表里。行保留,
    ``float_mv`` / ``total_mv`` 为 ``None`` —— 由调用方按缺失值语义处理
    (设了市值条件时这些行不通过,并在 ``notes`` 里说明有多少行被这一条挡掉)。
    """
    enriched = [dict(row) for row in rows]
    if market_cap is None or getattr(market_cap, "empty", True):
        for item in enriched:
            item.setdefault("float_mv", None)
            item.setdefault("total_mv", None)
        return enriched, len(enriched)

    frame = market_cap
    keep = [c for c in ("symbol", "date", "float_mv", "total_mv") if c in frame.columns]
    if "symbol" not in keep or "float_mv" not in keep:
        for item in enriched:
            item.setdefault("float_mv", None)
            item.setdefault("total_mv", None)
        return enriched, len(enriched)

    frame = frame[keep].copy()
    if "date" in frame.columns:
        # 同一代码可能有多日行(历史区间拉的);取最新一日,避免随机取到旧值。
        frame = frame.sort_values("date", kind="stable").drop_duplicates(
            "symbol", keep="last"
        )
    cap_map: dict[str, tuple[float | None, float | None]] = {}
    for record in frame.to_dict(orient="records"):
        code = _code_of(record.get("symbol"))
        if not code:
            continue
        cap_map[code] = (
            _finite(record.get("float_mv")),
            _finite(record.get("total_mv")),
        )

    missing = 0
    for item in enriched:
        float_mv, total_mv = cap_map.get(_code_of(item.get("symbol")), (None, None))
        item["float_mv"] = float_mv
        item["total_mv"] = total_mv
        if float_mv is None:
            missing += 1
    return enriched, missing


# ---------------------------------------------------------------------------
# Masks
# ---------------------------------------------------------------------------

Predicate = Callable[[Mapping[str, Any]], bool]


def _bounded(column: str, low: Any, high: Any) -> Predicate | None:
    """区间条件 → 谓词。两端都未设时返回 ``None``(该列不参与筛选)。

    缺失值一律不通过 —— 见模块 docstring「缺失值语义」。
    """
    if low is None and high is None:
        return None
    lo = None if low is None else float(low)
    hi = None if high is None else float(high)

    def _predicate(row: Mapping[str, Any]) -> bool:
        value = _finite(row.get(column))
        if value is None:
            return False
        if lo is not None and value < lo:
            return False
        if hi is not None and value > hi:
            return False
        return True

    return _predicate


def _above(column: str) -> Predicate:
    """现价 ≥ ``column``(MA/60 日高均走这条)。任一缺失 → 不通过。"""

    def _predicate(row: Mapping[str, Any]) -> bool:
        last = _finite(row.get("last"))
        level = _finite(row.get(column))
        return last is not None and level is not None and last >= level

    return _predicate


def _below(column: str) -> Predicate:
    def _predicate(row: Mapping[str, Any]) -> bool:
        last = _finite(row.get("last"))
        level = _finite(row.get(column))
        return last is not None and level is not None and last <= level

    return _predicate


def _in_boards(wanted: frozenset[str]) -> Predicate:
    def _predicate(row: Mapping[str, Any]) -> bool:
        return _board_of_row(row) in wanted

    return _predicate


def _not_st(row: Mapping[str, Any]) -> bool:
    return not is_st(str(row.get("name") or ""))


def _limit_is(wanted_up: bool) -> Predicate:
    def _predicate(row: Mapping[str, Any]) -> bool:
        return bool(row.get("limit_up")) == wanted_up

    return _predicate


def _predicates(spec: ScreenSpec) -> list[Predicate]:
    """把 spec 编译成谓词列表。空列表 = 不过滤(全市场截面)。"""
    out: list[Predicate] = []
    for predicate in (
        _bounded("last", spec.price_min, spec.price_max),
        _bounded("change_pct", spec.change_pct_min, spec.change_pct_max),
        _bounded("boards", spec.boards_min, spec.boards_max),
        _bounded("volume_ratio", spec.volume_ratio_min, spec.volume_ratio_max),
        _bounded("turnover", spec.turnover_min, None),
        _bounded("float_mv", spec.float_mv_min, spec.float_mv_max),
        _bounded("total_mv", spec.total_mv_min, spec.total_mv_max),
    ):
        if predicate is not None:
            out.append(predicate)

    if spec.board_types:
        out.append(_in_boards(frozenset(spec.board_types)))
    if spec.exclude_st:
        out.append(_not_st)
    if spec.above_ma is not None:
        out.append(_above(spec.above_ma))
    if spec.new_high:
        out.append(_above("high60"))
    if spec.new_low:
        out.append(_below("low60"))
    if spec.limit_up is not None:
        out.append(_limit_is(bool(spec.limit_up)))

    return out


def _sort_rows(
    rows: Sequence[Mapping[str, Any]], key: str, descending: bool
) -> list[dict[str, Any]]:
    """按 ``key`` 排序;缺值的行**恒排最后**(方向只影响有值的行)。

    次级键是代码,保证同一输入永远得到同一顺序 —— 前端翻页/对比不会被
    随机 tie-break 打乱。
    """

    def _key(row: Mapping[str, Any]) -> tuple[int, float, str]:
        value = _finite(row.get(key))
        if value is None:
            return (1, 0.0, str(row.get("symbol") or ""))
        signed = -value if descending else value
        return (0, signed, str(row.get("symbol") or ""))

    items = [dict(row) for row in rows]
    return sorted(items, key=_key)


def screen(
    rows: Sequence[Mapping[str, Any]],
    spec: ScreenSpec,
    *,
    market_cap: pd.DataFrame | None = None,
) -> ScreenResult:
    """对一帧候选行应用 ``spec``,返回截断后的结果与截断前的命中数。

    ``rows`` 必须是 ``market_overview.build_quote_rows`` 的产物(至少含
    ``symbol``/``name``/``last``/``change_pct``/``volume``/``boards``),否则
    依赖的列会全部按"未知"处理。纯函数:不读库、不联网、不改输入。
    """
    enriched, cap_missing = attach_market_cap(attach_turnover(rows), market_cap)
    predicates = _predicates(spec)
    kept = [row for row in enriched if all(predicate(row) for predicate in predicates)]
    matched = len(kept)
    ordered = _sort_rows(kept, spec.sort_by, spec.sort_desc)
    limited = ordered[: int(spec.limit)]

    notes: list[str] = []
    if spec.needs_market_cap and cap_missing:
        notes.append(f"有 {cap_missing} 行无市值数据,已按「不满足市值条件」排除")
    if spec.needs_change_pct:
        unscored = sum(1 for row in enriched if _finite(row.get("change_pct")) is None)
        if unscored:
            notes.append(f"有 {unscored} 行无昨收价,涨跌幅条件无法判定,已排除")

    return ScreenResult(
        rows=limited,
        matched=matched,
        scanned=len(enriched),
        notes=tuple(notes),
    )


def describe_spec(spec: ScreenSpec) -> dict[str, Any]:
    """把 spec 回显成 JSON —— 响应里带上"这次到底筛了什么"。

    回显的是**规范化后**的值,不是请求原文:前端据此渲染条件标签,读者也能
    在日志里复现同一条查询。
    """
    return {
        "price": [spec.price_min, spec.price_max],
        "change_pct": [spec.change_pct_min, spec.change_pct_max],
        "boards": [spec.boards_min, spec.boards_max],
        "volume_ratio": [spec.volume_ratio_min, spec.volume_ratio_max],
        "turnover_min": spec.turnover_min,
        "float_mv": [spec.float_mv_min, spec.float_mv_max],
        "total_mv": [spec.total_mv_min, spec.total_mv_max],
        "board_type": list(spec.board_types),
        "exclude_st": spec.exclude_st,
        "above_ma": spec.above_ma,
        "new_high": spec.new_high,
        "new_low": spec.new_low,
        "limit_up": spec.limit_up,
        "sort_by": spec.sort_by,
        "sort_desc": spec.sort_desc,
        "limit": spec.limit,
    }


__all__ = [
    "BOARDS",
    "DEFAULT_LIMIT",
    "MA_WINDOWS",
    "MAX_LIMIT",
    "SORT_KEYS",
    "ScreenResult",
    "ScreenSpec",
    "attach_market_cap",
    "attach_turnover",
    "describe_spec",
    "screen",
]

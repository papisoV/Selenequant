# Pattern absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""B2 — 交易所异动偏离监控(口径吸收自 TSP abnormal_moves.py)。

纯函数:输入个股与基准指数的日收盘序列(或实时涨跌幅),按板块阈值算
「偏离值 → 接近度」。不联网、不读库,底座由调用方喂。

口径(与 TSP backend/app/services/abnormal_moves.py 对齐,表达为 selene 重写):
- 偏离值 = 个股 N 日累计涨跌幅 − 对应指数同期涨跌幅
- 板块阈值 (正向, 负向):主板 3日±20%;创科 3日±30%;北交所 3日±40%;
  严重异动 10日 +100%/−50%、30日 +200%/−70%(负向更严)
- 接近度 = |偏离值| / 该方向阈值:≥1.0 已触发 / ≥0.7 边缘 / ≥0.5 观察
- 板块判定复用既有 market_rules.board_of() / is_st()
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from selene_space_ashare.market_rules import board_of, is_st

# ── 规则表(口径核心;数值同 TSP,表达为 selene 结构)──────────────────
# {窗口天数: (正向阈值, 负向阈值)}。3 日各板块对称;10/30 日严重异动各板块
# 一致且不对称(跌方向更早触发)。
_MAIN: Final[dict[int, tuple[float, float]]] = {
    3: (0.20, 0.20),
    10: (1.00, 0.50),
    30: (2.00, 0.70),
}
_GEM_STAR: Final[dict[int, tuple[float, float]]] = {
    3: (0.30, 0.30),
    10: (1.00, 0.50),
    30: (2.00, 0.70),
}
_BSE: Final[dict[int, tuple[float, float]]] = {
    3: (0.40, 0.40),
    10: (1.00, 0.50),
    30: (2.00, 0.70),
}

WINDOWS: Final[tuple[int, ...]] = (3, 10, 30)

STATUS_TRIGGERED: Final = "triggered"
STATUS_EDGE: Final = "edge"
STATUS_WATCH: Final = "watch"

# 接近度门限(与 TSP _status_of 同口径)。
CLOSENESS_TRIGGERED: Final = 1.0
CLOSENESS_EDGE: Final = 0.7
CLOSENESS_WATCH: Final = 0.5

# 板块 → 基准指数候选(按交易所官方口径:首选在前,缺失时回退)。
# 键与 TSP _bench_key_of 同口径(SH/STAR/SZ/GEM/BJ);候选表逐条照抄
# TSP pipeline._BENCHMARK_PREFERENCE(沪主板=上证A指、深主板=深证A指、
# 创业板=创业板综指),这是「偏离值」规则里「对应指数」的权威选择。
_BENCH_PREFERENCE: Final[dict[str, tuple[str, ...]]] = {
    "SH": ("000002.SH", "000001.SH"),  # 上证A指 → 上证指数
    "STAR": ("000688.SH", "000002.SH"),  # 科创50(交易规则6.12指定) → 上证A指
    "SZ": ("399107.SZ", "399001.SZ"),  # 深证A指 → 深证成指
    "GEM": ("399102.SZ", "399107.SZ"),  # 创业板综指 → 深证A指
    "BJ": ("899050.BJ", "000001.SH"),  # 北证50 → 上证指数
}


@dataclass(frozen=True)
class DeviationRule:
    """一个标的的异动规则:板块 + ST 标记 + 各窗口阈值。"""

    board: str
    st: bool
    thresholds: dict[int, tuple[float, float]]


@dataclass(frozen=True)
class DeviationWindow:
    """单窗口的偏离值与接近度。"""

    days: int
    deviation: float
    threshold: float
    closeness: float


def bench_key_of(symbol: str) -> str:
    """symbol → 基准键(SH/STAR/SZ/GEM/BJ),与 TSP _bench_key_of 同口径。

    未知/无后缀返回空串 —— 调用方据此跳过基准叠加。
    """
    code = symbol.strip().split(".")[0]
    suffix = symbol.strip().rsplit(".", 1)[-1].upper() if "." in symbol else ""
    if suffix == "BJ" or board_of(code) == "bse":
        return "BJ"
    if suffix == "SH":
        return "STAR" if code.startswith("68") else "SH"
    if suffix == "SZ":
        return "GEM" if code.startswith(("30", "301")) else "SZ"
    return ""


def bench_symbol_of(symbol: str) -> str | None:
    """symbol → 对应板块基准指数代码(首选,供 (a)/(b) 取指数行情)。"""
    candidates = bench_candidates_of(symbol)
    return candidates[0] if candidates else None


def bench_candidates_of(symbol: str) -> tuple[str, ...]:
    """symbol → 板块基准指数候选(首选在前;未知板块返回空元组)。

    消费方按序尝试:首选指数取不到行情时回退次选,任一可用即算基准成立 ——
    与 TSP pipeline 的 `_BENCHMARK_PREFERENCE` 回退语义一致。
    """
    return _BENCH_PREFERENCE.get(bench_key_of(symbol), ())


def rule_for(symbol: str, name: str = "") -> DeviationRule:
    """按板块 + ST 名选阈值集。ST 仅作展示标记(2026-07-06 起主板风险警示
    股票与普通股同标准,特别规定已废止 —— 与 TSP rule_for 一致)。"""
    board = board_of(symbol)
    st = is_st(name)
    if board == "bse":
        return DeviationRule(board, st, _BSE)
    if board in ("kcb", "cyb"):
        return DeviationRule(board, st, _GEM_STAR)
    return DeviationRule(board, st, _MAIN)


def status_of(closeness: float) -> str:
    """接近度 → 状态:≥1.0 已触发 / ≥0.7 边缘 / 否则观察。"""
    if closeness >= CLOSENESS_TRIGGERED:
        return STATUS_TRIGGERED
    if closeness >= CLOSENESS_EDGE:
        return STATUS_EDGE
    return STATUS_WATCH


def cumulative_pct(closes: Sequence[float], days: int) -> float | None:
    """N 日累计涨跌幅(小数),自 ``closes`` 末尾取。

    需要 ``days + 1`` 个收盘价(基准日在 ``closes[-days-1]``)。数据不足返回
    None —— 绝不把短序列当信号(缺基线 ≠ 信号)。
    """
    if days <= 0 or len(closes) < days + 1:
        return None
    base = float(closes[-(days + 1)])
    latest = float(closes[-1])
    if base <= 0:
        return None
    return (latest - base) / base


def deviation_pct(
    stock_closes: Sequence[float],
    index_closes: Sequence[float],
    days: int,
) -> float | None:
    """偏离值 = 个股 N 日累计涨跌幅 − 指数 N 日累计涨跌幅。任一侧不足 → None。"""
    stock = cumulative_pct(stock_closes, days)
    index = cumulative_pct(index_closes, days)
    if stock is None or index is None:
        return None
    return stock - index


def closeness_of(
    deviation: float, thresholds: dict[int, tuple[float, float]], days: int
) -> float | None:
    """偏离值 → 接近度(按方向选阈值:正用 up,负用 down)。"""
    pair = thresholds.get(days)
    if pair is None:
        return None
    up_t, down_t = pair
    threshold = up_t if deviation >= 0 else down_t
    if threshold <= 0:
        return None
    return abs(deviation) / threshold


def deviation_windows(
    stock_closes: Sequence[float],
    index_closes: Sequence[float],
    *,
    symbol: str,
    name: str = "",
    windows: Sequence[int] = WINDOWS,
) -> list[DeviationWindow]:
    """逐窗口算偏离 + 接近度;数据不足的窗口跳过(不产生假信号)。"""
    return _compute_windows(
        stock_closes,
        index_closes,
        rule=rule_for(symbol, name),
        windows=windows,
    )


def max_closeness(windows: Sequence[DeviationWindow]) -> float:
    """多窗口中取最大接近度(驱动排序与状态判定)。"""
    return max((w.closeness for w in windows), default=0.0)


def compose_live_deviation(
    historical_deviation: float,
    *,
    stock_rt_pct: float,
    index_rt_pct: float,
    includes_today: bool,
) -> float:
    """盘中实时偏离 = 历史偏离(已完成交易日) + (今日个股涨跌 − 今日指数涨跌)。

    ``includes_today=True`` 表示历史偏离已含今日(缓存日 ≥ 北京今天),不再叠加,
    否则会把最近交易日的涨跌算两遍(与 TSP build_overview 的 rt_delta 同口径)。
    """
    if includes_today:
        return historical_deviation
    return historical_deviation + (stock_rt_pct - index_rt_pct)


def _compute_windows(
    stock_closes: Sequence[float],
    index_closes: Sequence[float],
    *,
    rule: DeviationRule,
    windows: Sequence[int],
    stock_rt_pct: float | None = None,
    index_rt_pct: float | None = None,
) -> list[DeviationWindow]:
    """(a)/(b) 共用的逐窗口算法 —— 日线与盘中只有「是否叠加今日」一处差异。

    不叠实时(``stock_rt_pct`` 缺省)即 (a) 日频口径;传入今日实时涨跌即 (b)
    盘中口径。方向在叠加后可能翻转,故按**叠加后**偏离的符号重选阈值
    (与 TSP build_overview 逐窗口 up/down 同法)。数据不足的窗口跳过。
    """
    if stock_rt_pct is not None and index_rt_pct is not None:
        intraday = True
        rt_stock, rt_index = stock_rt_pct, index_rt_pct
    else:
        intraday = False
        rt_stock = rt_index = 0.0
    out: list[DeviationWindow] = []
    for n in windows:
        dev = deviation_pct(stock_closes, index_closes, n)
        if dev is None:
            continue
        if intraday:
            dev = compose_live_deviation(
                dev,
                stock_rt_pct=rt_stock,
                index_rt_pct=rt_index,
                includes_today=False,
            )
        close = closeness_of(dev, rule.thresholds, n)
        if close is None:
            continue
        up_t, down_t = rule.thresholds[n]
        out.append(
            DeviationWindow(
                days=n,
                deviation=dev,
                threshold=up_t if dev >= 0 else down_t,
                closeness=close,
            )
        )
    return out


# ── 消费入口:把上面的纯函数接上「日线(a)」与「盘中(b)」两条口径 ──────────


def align_by_date(
    stock: Sequence[tuple[str, float]],
    index: Sequence[tuple[str, float]],
) -> tuple[list[float], list[float]]:
    """按共同交易日对齐个股与指数的收盘序列。

    入参是 ``(date, close)`` 序列(date 为 ``YYYYMMDD``)。返回共同日的
    ``(stock_closes, index_closes)``,升序。个股停牌而指数仍开市的日期不在
    交集内 —— 偏离值只在两侧都有收盘时才有定义,缺失日不可拿最近价顶替
    (拿错日期对齐会让「N 日」变成「N 个个股交易日 vs 指数错位日」,产生假信号)。
    """
    index_map = {d: c for d, c in index}
    common = sorted(d for d, _ in stock if d in index_map)
    stock_map = {d: c for d, c in stock}
    stock_closes = [float(stock_map[d]) for d in common]
    index_closes = [float(index_map[d]) for d in common]
    return stock_closes, index_closes


@dataclass(frozen=True)
class SymbolDeviation:
    """单标的的偏离评估结果(供 API/前端消费)。"""

    symbol: str
    name: str
    board: str
    st: bool
    windows: list[DeviationWindow]
    max_closeness: float
    status: str
    intraday: bool


def evaluate_deviation(
    stock_closes: Sequence[float],
    index_closes: Sequence[float],
    *,
    symbol: str,
    name: str = "",
    windows: Sequence[int] = WINDOWS,
    stock_rt_pct: float | None = None,
    index_rt_pct: float | None = None,
) -> SymbolDeviation:
    """(a)/(b) 共用的偏离评估:逐窗口算偏离 + 接近度 + 状态。

    - **(a) 日频**:只传两段对齐后的收盘序列 → 用日线口径(最后一根即基准日)。
    - **(b) 盘中**:再传 ``stock_rt_pct`` / ``index_rt_pct``(今日实时涨跌,小数)
      → 在每个窗口的历史偏离上叠 ``compose_live_deviation`` 再算接近度。
      调用方须保证传入的历史收盘**严格早于今日**(否则今日被算两遍)——
      日线底物的「严格早于会话日」过滤即为此而设。

    方向在叠加后可能翻转,故每个窗口按**叠加后**偏离的符号重选阈值
    (与 TSP build_overview 逐窗口 up/down 同法)。数据不足的窗口跳过,
    绝不把短序列当信号。
    """
    rule = rule_for(symbol, name)
    windows_out = _compute_windows(
        stock_closes,
        index_closes,
        rule=rule,
        windows=windows,
        stock_rt_pct=stock_rt_pct,
        index_rt_pct=index_rt_pct,
    )
    mx = max_closeness(windows_out)
    return SymbolDeviation(
        symbol=symbol,
        name=name,
        board=rule.board,
        st=rule.st,
        windows=windows_out,
        max_closeness=mx,
        status=status_of(mx),
        intraday=stock_rt_pct is not None and index_rt_pct is not None,
    )

"""setup 回测：纯价格参数轻量前向模拟器。

回答 PA 交易者的核心问题："这个 entry/SL/TP 的 R:R，在历史上到底赚不赚？"

与策略回测（``engine.run(strategy, kline_df)`` 出权益曲线）不同，这是 **setup 采样统计**：
喂 K 线 + entry/stop_loss/take_profit（+direction）→ 扫所有 pin-bar 回踩确认（插针触及
entry 且收盘收回）的 bar → 每处模拟 SL/TP 谁先触发（bar 级 high/low 判定）→ 汇总
胜率/盈亏比/期望/最大回撤，按 R:R 分桶。

纯函数设计（无 I/O）：K 线 DataFrame 由调用方传入，便于离线回放与单测。
语义风格对齐 ``eval_next_bar.py``；所有 float 输出保证有限
（防 -inf 序列化成 -Infinity 导致前端 JSON.parse 500 的事故，见 memory project_alpha_inf_serialization）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

Direction = Literal["long", "short"]
Outcome = Literal["win", "loss"]

REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close")
DEFAULT_MIN_CANDLES = 20


class SetupBacktestError(ValueError):
    """setup 回测参数校验错误（SL==TP / 方向与价格矛盾 / 非有限价格）。"""


@dataclass(frozen=True)
class SetupBacktestResult:
    """一次 setup 回测的汇总结果。所有 float 输出保证有限。

    total_setups — 已收敛（SL/TP 触达）的 setup 数；数据末尾仍未收敛的计为 open_setups。
    profit_factor — 无亏损（gross_loss==0）时为 None（概念上无穷，避免 inf 序列化）。
    """

    total_setups: int
    win_rate: float
    avg_win: float
    avg_loss: float
    expectancy: float
    profit_factor: float | None
    max_drawdown: float
    avg_bars_held: float
    rr_buckets: dict[str, dict[str, float | int]]
    trades: list[dict[str, Any]]
    open_setups: int = 0
    no_trigger: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_setups": self.total_setups,
            "win_rate": self.win_rate,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "expectancy": self.expectancy,
            "profit_factor": self.profit_factor,
            "max_drawdown": self.max_drawdown,
            "avg_bars_held": self.avg_bars_held,
            "rr_buckets": self.rr_buckets,
            "trades": self.trades,
            "open_setups": self.open_setups,
            "no_trigger": self.no_trigger,
            "reason": self.reason,
        }


def _finite(value: float) -> float:
    """非有限 float 归零（防 -Infinity/NaN 进 JSON）。"""
    return value if math.isfinite(value) else 0.0


def _div(numerator: float, denominator: float) -> float:
    return _finite(numerator / denominator) if denominator else 0.0


def _require_finite(value: float, name: str) -> float:
    v = float(value)
    if not math.isfinite(v):
        raise SetupBacktestError(f"{name} 必须为有限数值，got {value!r}")
    return v


def _resolve_direction(
    entry: float,
    sl: float,
    tp: float,
    direction: Direction | None,
) -> Direction:
    """校验/推断方向。direction 为空时按 entry 与 SL 的相对位置推断。"""
    if direction is not None:
        if direction not in ("long", "short"):
            raise SetupBacktestError(
                f"direction 必须为 'long'/'short'，got {direction!r}"
            )
        if direction == "long":
            if not (sl < entry < tp):
                raise SetupBacktestError(
                    "long 价格顺序错误：需 stop_loss < entry < take_profit"
                    f"（sl={sl} entry={entry} tp={tp}）"
                )
        elif not (tp < entry < sl):
            raise SetupBacktestError(
                "short 价格顺序错误：需 take_profit < entry < stop_loss"
                f"（tp={tp} entry={entry} sl={sl}）"
            )
        return direction

    if entry > sl:
        inferred: Direction = "long"
    elif entry < sl:
        inferred = "short"
    else:
        raise SetupBacktestError(
            "entry_price 与 stop_loss_price 相同（风险=0，无法推断方向）"
        )
    if inferred == "long" and not (sl < entry < tp):
        raise SetupBacktestError(
            "long 价格顺序错误：需 stop_loss < entry < take_profit"
            f"（sl={sl} entry={entry} tp={tp}）"
        )
    if inferred == "short" and not (tp < entry < sl):
        raise SetupBacktestError(
            "short 价格顺序错误：需 take_profit < entry < stop_loss"
            f"（tp={tp} entry={entry} sl={sl}）"
        )
    return inferred


def _resolve_setup(
    highs: Any,
    lows: Any,
    start: int,
    sl: float,
    tp: float,
    direction: Direction,
) -> tuple[Outcome | None, int | None]:
    """从 start bar（含）向前扫描，返回首个触发的 (outcome, exit_idx)。

    同 bar 内 SL/TP 都触达时，entry 侧优先取保守结果：
    long 先查 low<=SL（止损先到 → 亏损），short 先查 high>=SL。未收敛返回 (None, None)。
    """
    n = len(highs)
    for j in range(start, n):
        if direction == "long":
            if lows[j] <= sl:
                return "loss", j
            if highs[j] >= tp:
                return "win", j
        else:
            if highs[j] >= sl:
                return "loss", j
            if lows[j] <= tp:
                return "win", j
    return None, None


def _rr_of(entry: float, sl: float, tp: float) -> float:
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    return reward / risk  # _resolve_direction 已保证 risk>0


def _pnl_r(outcome: Outcome, rr: float) -> float:
    """R 倍数盈亏：win=+RR，loss=-1。"""
    return rr if outcome == "win" else -1.0


def _empty_result(
    reason: str, *, no_trigger: bool, open_setups: int = 0
) -> SetupBacktestResult:
    return SetupBacktestResult(
        total_setups=0,
        win_rate=0.0,
        avg_win=0.0,
        avg_loss=0.0,
        expectancy=0.0,
        profit_factor=None,
        max_drawdown=0.0,
        avg_bars_held=0.0,
        rr_buckets={},
        trades=[],
        open_setups=open_setups,
        no_trigger=no_trigger,
        reason=reason,
    )


def _aggregate(trades: list[dict[str, Any]], open_setups: int) -> SetupBacktestResult:
    total = len(trades)
    if total == 0:
        reason = (
            "所有 setup 均未在数据末尾收敛（未触达 SL/TP）"
            if open_setups
            else "entry 价位在数据范围内未触发"
        )
        return _empty_result(
            reason, no_trigger=open_setups == 0, open_setups=open_setups
        )

    wins = [t for t in trades if t["outcome"] == "win"]
    losses = [t for t in trades if t["outcome"] == "loss"]
    n_wins = len(wins)
    n_losses = len(losses)
    rr = float(trades[0]["reward_risk_ratio"])  # 单次模拟 RR 恒定

    win_rate = _div(n_wins, total)
    avg_win = _finite(sum(t["pnl_r"] for t in wins) / n_wins) if n_wins else 0.0
    avg_loss = _finite(sum(t["pnl_r"] for t in losses) / n_losses) if n_losses else 0.0
    expectancy = _finite(sum(t["pnl_r"] for t in trades) / total)
    gross_win = sum(t["pnl_r"] for t in wins)
    gross_loss = sum(-t["pnl_r"] for t in losses)  # 取正
    profit_factor = _div(gross_win, gross_loss) if gross_loss > 0 else None
    avg_bars_held = _div(sum(t["bars_held"] for t in trades), total)

    # 最大回撤：累计 R 权益曲线的峰谷差
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cum += t["pnl_r"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    max_dd = _finite(max_dd)

    bucket_key = f"{round(rr, 2):g}"
    rr_buckets = {
        bucket_key: {
            "count": total,
            "wins": n_wins,
            "losses": n_losses,
            "win_rate": win_rate,
            "expectancy": expectancy,
        }
    }

    return SetupBacktestResult(
        total_setups=total,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
        profit_factor=profit_factor,
        max_drawdown=max_dd,
        avg_bars_held=avg_bars_held,
        rr_buckets=rr_buckets,
        trades=trades,
        open_setups=open_setups,
        no_trigger=False,
        reason=None,
    )


def run_setup_backtest(
    klines_df: pd.DataFrame,
    entry_price: float,
    stop_loss_price: float,
    take_profit_price: float,
    *,
    direction: Direction | None = None,
    min_candles: int = DEFAULT_MIN_CANDLES,
) -> SetupBacktestResult:
    """对给定 K 线 + entry/SL/TP 跑 setup 采样统计。

    K 线 DataFrame 必须含列：``timestamp, open, high, low, close``（整数时间戳）。
    纯函数，无 I/O。

    触发语义：long 在 ``low <= entry AND close >= entry`` 的 bar 触发（pin-bar 回踩：
    插针触及 entry 且收盘收回上方 = 支撑确认）；short 在 ``high >= entry AND close <= entry``
    触发。gap 穿过 entry 但收盘未收回的不触发。触发 bar 仅作为确认信号，**不参与
    SL/TP 解析**（成交发生在确认后，从下一根开始模拟，避免触发当根即被 SL 的退化统计）。
    同一时刻只允许一个 open setup：setup 未收敛期间后续触发 bar 跳过；收敛（SL/TP 触达）
    后下一个触发 bar 开始新 setup。同 bar 内 SL/TP 都触达取保守顺序（long 先查 SL、
    short 先查 SL → 记亏损）。

    参数校验失败（SL==TP / 方向与价格矛盾 / 非有限价格）抛 :class:`SetupBacktestError`；
    数据不足或无触发返回空结果（``total_setups=0`` + ``reason``）。
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in klines_df.columns]
    if missing:
        raise ValueError(f"K 线缺列 {missing}，需要 {list(REQUIRED_COLUMNS)}")
    if klines_df[list(REQUIRED_COLUMNS)].isna().any().any():
        raise ValueError("K 线包含 NaN，无法回测")

    entry = _require_finite(entry_price, "entry_price")
    sl = _require_finite(stop_loss_price, "stop_loss_price")
    tp = _require_finite(take_profit_price, "take_profit_price")
    direction = _resolve_direction(entry, sl, tp, direction)

    n_bars = len(klines_df)
    if n_bars < min_candles:
        return _empty_result(
            f"K 线不足 {min_candles} 根（实际 {n_bars}）", no_trigger=False
        )

    highs = klines_df["high"].astype(float).to_numpy()
    lows = klines_df["low"].astype(float).to_numpy()
    closes = klines_df["close"].astype(float).to_numpy()
    timestamps = klines_df["timestamp"].to_numpy()
    n = len(highs)

    trades: list[dict[str, Any]] = []
    open_setups = 0
    rr = _rr_of(entry, sl, tp)

    i = 0
    while i < n:
        if direction == "long":
            # pin-bar 回踩确认：插针到 entry 且收盘收回 entry 上方
            triggered = lows[i] <= entry and closes[i] >= entry
        else:
            # 反向 pin-bar：冲高到 entry 且收盘收回 entry 下方
            triggered = highs[i] >= entry and closes[i] <= entry
        if not triggered:
            i += 1
            continue

        # 触发 bar 是"确认信号"（收盘已收回），成交发生在确认后，SL/TP 从下一根开始解析，
        # 避免 4h bar 振幅>SL 时"触发当根即被 SL"的退化统计。
        outcome, exit_idx = _resolve_setup(highs, lows, i + 1, sl, tp, direction)
        if outcome is None or exit_idx is None:
            # 该 setup 未收敛且已扫到数据末尾，后续无新 bar 可触发
            open_setups += 1
            break

        held = exit_idx - i
        trades.append(
            {
                "setup_index": len(trades) + 1,
                "entry_timestamp": int(timestamps[i]),
                "exit_timestamp": int(timestamps[exit_idx]),
                "entry_index": i,
                "exit_index": exit_idx,
                "outcome": outcome,
                "pnl_r": _pnl_r(outcome, rr),
                "bars_held": held,
                "entry_price": entry,
                "stop_loss_price": sl,
                "take_profit_price": tp,
                "reward_risk_ratio": rr,
            }
        )
        i = exit_idx + 1

    return _aggregate(trades, open_setups)


__all__ = [
    "Direction",
    "Outcome",
    "SetupBacktestError",
    "SetupBacktestResult",
    "run_setup_backtest",
    # 心跳 outcome 回看(T3a)复用同 bar 保守序的收敛判定——导出以免
    # 两处各写一份 SL/TP 扫描逻辑后静默漂移。
    "_resolve_setup",
    "REQUIRED_COLUMNS",
    "DEFAULT_MIN_CANDLES",
]

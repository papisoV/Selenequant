"""next_bar_forecast 离线评估（US-012 C3）。

在开放预测字段前端展示之前先出数：把历史记录的 ``next_bar_forecast.direction``
与真实后续 K 线方向比对，并用动量基线（趋势延续假设）对照，输出命中率报告。

纯函数设计（无 I/O）：后续 K 线由调用方以 ``record_id → 收盘价`` 的 dict 提供，
便于离线回放与单测。CLI 见 ``scripts/eval_next_bar.py``。
"""
from __future__ import annotations

from typing import Any

_VALID_DIRECTIONS = {"up", "down", "flat"}


def direction_of(
    close_prev: float,
    close_next: float,
    *,
    tolerance: float = 0.0,
) -> str:
    """两根收盘价 → 方向（up/down/flat）。"""
    delta = close_next - close_prev
    if abs(delta) <= tolerance:
        return "flat"
    return "up" if delta > 0 else "down"


def momentum_predictor(
    closes: list[float],
    *,
    window: int = 3,
    tolerance: float = 0.0,
) -> str:
    """基线预测：最近 window 根的动量方向作为下一根方向（趋势延续假设）。"""
    if len(closes) < 2:
        return "flat"
    anchor = closes[max(0, len(closes) - 1 - window)]
    return direction_of(anchor, closes[-1], tolerance=tolerance)


def extract_forecast(record: dict[str, Any]) -> str | None:
    """取记录中 LLM 的 next_bar_forecast.direction；缺失/非法返回 None。"""
    forecast = ((record.get("stage2") or {}).get("next_bar_forecast") or {}).get(
        "direction"
    )
    return forecast if forecast in _VALID_DIRECTIONS else None


def _record_closes(record: dict[str, Any]) -> list[float]:
    return [k["close"] for k in (record.get("input_data") or {}).get("klines") or []]


def compute_subsequent_closes(records: list[dict[str, Any]], df: Any) -> dict[str, float]:
    """record_id → 记录最后一根 K 线之后那根的实际收盘价（无后续则缺省）。"""
    closes_by_ts: dict[int, float] = {}
    for _, row in df.iterrows():
        closes_by_ts[int(row["timestamp"])] = float(row["close"])
    if not closes_by_ts:
        return {}
    later_ts = sorted(closes_by_ts)

    out: dict[str, float] = {}
    for rec in records:
        klines = (rec.get("input_data") or {}).get("klines") or []
        last_ts = max((int(k["timestamp"]) for k in klines), default=None)
        if last_ts is None:
            continue
        for ts in later_ts:
            if ts > last_ts:
                out[rec["meta"]["record_id"]] = closes_by_ts[ts]
                break
    return out


def evaluate_next_bar(
    records: list[dict[str, Any]],
    subsequent_closes: dict[str, float],
    *,
    window: int = 3,
    tolerance: float = 0.0,
) -> dict[str, Any]:
    """对每个带后续 K 线的记录评估，返回报告 dict。

    ``subsequent_closes``：record_id → 该记录最后一根 K 线之后那根的实际收盘价。
    无后续数据（或记录无 klines）的记录跳过。
    """
    rows: list[dict[str, Any]] = []
    for rec in records:
        rid = rec["meta"]["record_id"]
        next_close = subsequent_closes.get(rid)
        if next_close is None:
            continue
        closes = _record_closes(rec)
        if not closes:
            continue
        actual = direction_of(closes[-1], next_close, tolerance=tolerance)
        forecast = extract_forecast(rec)
        baseline = momentum_predictor(closes, window=window, tolerance=tolerance)
        rows.append(
            {
                "record_id": rid,
                "forecast": forecast,
                "baseline": baseline,
                "actual": actual,
                "forecast_hit": (forecast == actual) if forecast is not None else None,
                "baseline_hit": baseline == actual,
            }
        )

    forecast_rows = [r for r in rows if r["forecast"] is not None]
    return {
        "samples_total": len(rows),
        "samples_with_forecast": len(forecast_rows),
        "hit_rate_forecast": _hit_rate(forecast_rows, "forecast_hit"),
        "hit_rate_baseline": _hit_rate(rows, "baseline_hit"),
        "rows": rows,
    }


def _hit_rate(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return sum(1 for r in rows if r[key]) / len(rows)


__all__ = [
    "direction_of",
    "momentum_predictor",
    "extract_forecast",
    "evaluate_next_bar",
    "compute_subsequent_closes",
]

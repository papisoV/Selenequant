# Cleanroom implementation: design intent derived from prior art; independent expression.
"""Plotly render layer for :class:`BacktestResult`.

Design-intent absorption of an external reference ``backtest_viz/chart.py``:
a multi-row static backtest report that lets a CLI/standalone script produce a
readable figure (or self-contained HTML) right after a backtest, without
launching the React frontend. The *layout intent* — a stacked subplot sheet
combining an equity curve, a per-trade PnL breakdown, and entry/exit markers —
is absorbed; the *implementation* is independent: the data contract is Selene's
``BacktestResult`` (equity / drawdown / buy-&-hold curves + a trades list +
``BacktestMetrics``), not the reference's ``SymbolResult`` (which carries
OHLCV / signal / position arrays Selene's result does not expose), and the
rendering stack is plotly (consistent with ``ic_panel_viz`` /
``return_report_viz``), not matplotlib.

Rows are emitted only when the underlying data is present, so an empty or
sparse result degrades to a bare figure instead of raising:

- **Equity curve vs Buy & Hold** — ``equity_curve`` (solid) and
  ``buy_hold_curve`` (dotted), both re-based to a common start so a strategy
  is directly comparable to its buy-and-hold benchmark. When ``trades`` carries
  ``entry_time`` / ``exit_time`` and the count is small (<= 50), ▲ entry / ▼
  exit markers are layered on this row (the reference's trade-marker intent);
  a large trade count skips the markers to avoid an unreadable chart.
- **Drawdown** — ``drawdown_curve`` as a red area from 0 down. Selene
  implements max-drawdown for real (the reference's ``SymbolResult.max_drawdown``
  is hardcoded 0.0), so this row is a Selene addition with no upstream
  counterpart.
- **Per-trade PnL** — one bar per trade (``trades[i]["pnl"]``), green for
  winners, red for losers.
- **Overfit / risk metrics** — a single-row heatmap of the ``BacktestMetrics``
  overfit and risk-adjusted fields (DSR / PBO / haircut / Sharpe / Sortino /
  Calmar). The reference has no independent overfit gate, so this row is a
  Selene addition visualizing data the reference does not produce.

This module imports no torch; it uses only plotly + numpy. plotly is an
optional dependency (``selene-space-backtest[viz]``) — the core engine never
imports this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

if TYPE_CHECKING:
    from selene_space_types.strategy import BacktestResult

__all__ = ["render_backtest_report"]

_ROW_TITLES = {
    "equity": "Equity Curve vs Buy & Hold",
    "drawdown": "Drawdown",
    "pertrade": "Per-Trade PnL",
    "metrics": "Overfit & Risk-Adjusted Metrics",
}

#: Trade count above which entry/exit markers are skipped (readability guard).
_MAX_MARKER_TRADES = 50


def _domain_axes(row: int) -> tuple[str, str]:
    return f"x{row if row > 1 else ''} domain", f"y{row if row > 1 else ''} domain"


def _x_labels(length: int) -> list[int]:
    return list(range(length))


def _float_or_nan(v: object) -> float:
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")
    return f if f == f else float("nan")  # NaN-stable


def _norm_to_start(curve: list[float]) -> np.ndarray:
    """Re-base a curve so its first finite value maps to 1.0 (common start)."""
    arr = np.array([_float_or_nan(v) for v in curve], dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0 or finite[0] == 0:
        return arr
    return arr / finite[0]


def render_backtest_report(
    result: "BacktestResult",
    *,
    title: str | None = None,
    return_fig: bool = True,
) -> go.Figure | None:
    """Render a :class:`BacktestResult` as a multi-row static backtest report.

    Returns the ``plotly.graph_objects.Figure`` when ``return_fig`` is True
    (the default), else ``None``. An empty result (no equity curve) produces a
    figure with a single "No backtest data available" annotation rather than
    raising.
    """
    eq = list(result.equity_curve or [])
    bh = list(result.buy_hold_curve or [])
    dd = list(result.drawdown_curve or [])
    trades = list(result.trades or [])
    metrics = result.metrics

    has_equity = len(eq) > 0
    has_drawdown = len(dd) > 0
    has_pertrade = any(
        isinstance(t, dict) and t.get("pnl") is not None for t in trades
    )
    # metrics row shows when any overfit/risk field is non-zero (defaults are 0).
    has_metrics = any(
        _float_or_nan(getattr(metrics, f, 0.0)) != 0.0
        for f in ("dsr", "pbo", "haircut_sharpe", "sharpe_ratio",
                  "sortino_ratio", "calmar_ratio")
    )

    rows: list[str] = []
    if has_equity:
        rows.append("equity")
    if has_drawdown:
        rows.append("drawdown")
    if has_pertrade:
        rows.append("pertrade")
    if has_metrics:
        rows.append("metrics")

    fig = go.Figure()
    if not rows:
        fig.add_annotation(text="No backtest data available", showarrow=False)
        fig.update_layout(title=title or "Backtest Report")
        return fig if return_fig else None

    specs = [[{"type": "heatmap"} if r == "metrics" else {"type": "xy"}] for r in rows]
    fig = make_subplots(
        rows=len(rows),
        cols=1,
        specs=specs,
        subplot_titles=[_ROW_TITLES[r] for r in rows],
        vertical_spacing=0.08,
    )

    row = 0
    for key in rows:
        row += 1
        if key == "equity":
            x = _x_labels(len(eq))
            fig.add_trace(
                go.Scatter(x=x, y=_norm_to_start(eq), mode="lines",
                           name="Equity", line=dict(color="darkgreen", width=2)),
                row=row, col=1,
            )
            if len(bh) > 0:
                bh_x = _x_labels(len(bh))
                fig.add_trace(
                    go.Scatter(x=bh_x, y=_norm_to_start(bh), mode="lines",
                               name="Buy & Hold", line=dict(color="#888", width=1.5, dash="dot")),
                    row=row, col=1,
                )
            # Entry/exit markers (reference trade-marker intent), small counts only.
            if len(trades) <= _MAX_MARKER_TRADES and trades:
                entries = [(t.get("entry_time"), t.get("entry_price"))
                           for t in trades if isinstance(t, dict)
                           and t.get("entry_time") is not None]
                exits = [(t.get("exit_time"), t.get("exit_price"))
                         for t in trades if isinstance(t, dict)
                         and t.get("exit_time") is not None]
                if entries:
                    fig.add_trace(
                        go.Scatter(
                            x=[e[0] for e in entries], y=[_float_or_nan(e[1]) for e in entries],
                            mode="markers", name="Entry",
                            marker=dict(symbol="triangle-up", color="green", size=9),
                        ),
                        row=row, col=1,
                    )
                if exits:
                    fig.add_trace(
                        go.Scatter(
                            x=[e[0] for e in exits], y=[_float_or_nan(e[1]) for e in exits],
                            mode="markers", name="Exit",
                            marker=dict(symbol="triangle-down", color="red", size=9),
                        ),
                        row=row, col=1,
                    )
            fig.update_xaxes(title_text="Bar", row=row, col=1, tickfont=dict(size=8))
            fig.update_yaxes(title_text="Equity (rebased)", row=row, col=1, tickfont=dict(size=8))
        elif key == "drawdown":
            x = _x_labels(len(dd))
            y = [_float_or_nan(v) for v in dd]
            fig.add_trace(
                go.Scatter(x=x, y=y, mode="lines", name="Drawdown",
                           line=dict(color="crimson", width=1),
                           fill="tozeroy", fillcolor="rgba(220,20,60,0.18)"),
                row=row, col=1,
            )
            fig.update_xaxes(title_text="Bar", row=row, col=1, tickfont=dict(size=8))
            fig.update_yaxes(title_text="Drawdown", row=row, col=1, tickfont=dict(size=8))
        elif key == "pertrade":
            pnls = [_float_or_nan(t.get("pnl")) for t in trades
                    if isinstance(t, dict) and t.get("pnl") is not None]
            x = list(range(len(pnls)))
            colors = ["green" if p > 0 else "crimson" for p in pnls]
            fig.add_trace(
                go.Bar(x=x, y=pnls, name="Per-Trade PnL",
                       marker=dict(color=colors), showlegend=False),
                row=row, col=1,
            )
            fig.update_xaxes(title_text="Trade #", row=row, col=1, tickfont=dict(size=8))
            fig.update_yaxes(title_text="PnL", row=row, col=1, tickfont=dict(size=8))
        elif key == "metrics":
            fields = ["dsr", "pbo", "haircut_sharpe", "sharpe_ratio",
                      "sortino_ratio", "calmar_ratio"]
            vals = [[_float_or_nan(getattr(metrics, f, 0.0)) for f in fields]]
            text = [[f"{v:.3f}" if v == v else "" for v in vals[0]]]
            fig.add_trace(
                go.Heatmap(z=vals, x=fields, y=["Metrics"], colorscale="RdYlGn",
                           showscale=True, text=text, texttemplate="%{text}",
                           zmin=0, zmax=2),
                row=row, col=1,
            )
            fig.update_xaxes(title_text="Metric", row=row, col=1, type="category",
                             tickfont=dict(size=8))
            fig.update_yaxes(title_text="", row=row, col=1, type="category",
                             tickfont=dict(size=8))

    fig.update_layout(
        title=title or f"Backtest Report — {result.strategy_id}",
        template="plotly_white",
        height=320 * len(rows),
        margin=dict(t=80, b=40, l=50, r=40),
        showlegend=True,
    )
    return fig if return_fig else None

# Ported from AlphaPurify (MIT)
"""Plotly render layer for :class:`WeightDriftResult`.

Design-intent port of AlphaPurify ``FactorAnalyzer.create_long_short_return_sheet``
(``FactorAnalyzer.py`` L2565-2905), simplified to Selene's single-config
``WeightDriftResult`` and a native ``make_subplots`` layout (the source's
pixel-precise canvas domain arithmetic and per-row legend matrix are not carried
over). The source iterates multiple rebalance periods; here one ``WeightDriftResult``
is one period, so each row renders once.

Rows are emitted only when the underlying data is present:

- **Cumulative long-short return** — gross (``cum_ret_ls``, dotted) and net
  (``cum_ret_net_ls``, solid) curves.
- **Industry attribution** — horizontal RdYlGn bar of ``df_indus[['industry',
  'ret_net_ls_i_mean']]``, rendered only when ``df_indus`` is populated
  (``group_by`` was set on the backtest).
- **Turnover** — per-rebalance ``df_agg['turnover_ls']`` markers plus the
  ``avg_turnover_ls`` mean line.
- **Statistics** — single-row heatmap of the ``stats_ls`` performance metrics.

The calendar heatmaps of the source are not a data-port surface (see
``weight_drift_result``) and are intentionally omitted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

if TYPE_CHECKING:
    from .weight_drift_result import WeightDriftResult

_ROW_TITLES = {
    "cum": "Cumulative Long-Short Return",
    "industry": "Average Industry Long-Short Return",
    "turnover": "Turnover Rate",
    "stats": "Long-Short Return Statistics",
}


def _date_labels(index) -> list[str]:
    return [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in index]


def _domain_axes(row: int) -> tuple[str, str]:
    return f"x{row if row > 1 else ''} domain", f"y{row if row > 1 else ''} domain"


def _result_date_col(result: "WeightDriftResult") -> str:
    cfg = getattr(result, "config", None)
    return getattr(cfg, "trade_date_col", None) or "trade_date"


def create_long_short_sheet(
    result: "WeightDriftResult",
    *,
    title: str | None = None,
    return_fig: bool = True,
) -> go.Figure | None:
    """Render a :class:`WeightDriftResult` as a multi-row long-short sheet.

    Returns the ``plotly.graph_objects.Figure`` when ``return_fig`` is True
    (the default), else ``None``. An empty result produces a figure with a
    single "No long-short data available" annotation rather than raising.
    """
    cum_ls = result.cum_ret_ls.dropna()
    has_cum = len(cum_ls) > 0
    has_indus = result.df_indus is not None and len(result.df_indus) > 0
    has_turnover = result.df_agg is not None and len(result.df_agg) > 0
    has_stats = bool(result.stats_ls)

    rows: list[str] = []
    if has_cum:
        rows.append("cum")
    if has_indus:
        rows.append("industry")
    if has_turnover:
        rows.append("turnover")
    if has_stats:
        rows.append("stats")

    fig = go.Figure()
    if not rows:
        fig.add_annotation(text="No long-short data available", showarrow=False)
        fig.update_layout(title=title or "Long-Short Return Sheet")
        return fig if return_fig else None

    specs = [[{"type": "heatmap"} if r == "stats" else {"type": "xy"}] for r in rows]
    fig = make_subplots(
        rows=len(rows),
        cols=1,
        specs=specs,
        subplot_titles=[_ROW_TITLES[r] for r in rows],
        vertical_spacing=0.06,
    )

    row = 0
    for key in rows:
        row += 1
        if key == "cum":
            dates = _date_labels(cum_ls.index)
            cum_net = result.cum_ret_net_ls.reindex(cum_ls.index)
            fig.add_trace(
                go.Scatter(x=dates, y=cum_ls.to_numpy(), mode="lines", name="Return Before Cost",
                           line=dict(color="darkgreen", width=2, dash="dot")),
                row=row, col=1,
            )
            fig.add_trace(
                go.Scatter(x=dates, y=cum_net.to_numpy(), mode="lines",
                           name="Cumulative LS Return", line=dict(color="darkgreen", width=2)),
                row=row, col=1,
            )
            fig.update_xaxes(type="category", categoryorder="array", categoryarray=dates,
                             title_text="Date", row=row, col=1, tickfont=dict(size=8))
            fig.update_yaxes(title_text="Cumulative Return", row=row, col=1, tickfont=dict(size=8))
        elif key == "industry":
            indus = result.df_indus
            if indus is None:
                continue
            df = indus[["industry", "ret_net_ls_i_mean"]].sort_values(
                "ret_net_ls_i_mean", ascending=True
            )
            fig.add_trace(
                go.Bar(x=df["ret_net_ls_i_mean"], y=df["industry"], orientation="h",
                       marker=dict(color=df["ret_net_ls_i_mean"], colorscale="RdYlGn", showscale=False)),
                row=row, col=1,
            )
            fig.update_xaxes(title_text="Mean Return", row=row, col=1, tickfont=dict(size=8))
            fig.update_yaxes(title_text="Industry", type="category", row=row, col=1, tickfont=dict(size=8))
        elif key == "turnover":
            valid = result.df_agg[result.df_agg["turnover_ls"].notna()]
            if len(valid):
                dates = _date_labels(valid[_result_date_col(result)])
                fig.add_trace(
                    go.Scatter(x=dates, y=valid["turnover_ls"].to_numpy(), mode="markers",
                               name="LS Portfolio Turnover", marker=dict(size=6, color="darkgreen")),
                    row=row, col=1,
                )
                m = float(result.avg_turnover_ls) if result.avg_turnover_ls is not None else float(valid["turnover_ls"].mean())
                fig.add_trace(
                    go.Scatter(x=dates, y=[m] * len(dates), mode="lines", name="Mean Turnover",
                               line=dict(color="red", width=3, dash="dot")),
                    row=row, col=1,
                )
                xref, yref = _domain_axes(row)
                fig.add_annotation(
                    x=0.99, y=0.99, xref=xref, yref=yref, text=f"Mean = {m:.4f}",
                    showarrow=False, font=dict(color="red", size=12), xanchor="right", yanchor="top",
                )
                fig.update_xaxes(type="category", categoryorder="array", categoryarray=dates,
                                 title_text="Date", row=row, col=1, tickfont=dict(size=8))
                fig.update_yaxes(title_text="Turnover Ratio", row=row, col=1, tickfont=dict(size=8))
        elif key == "stats":
            keys = list(result.stats_ls.keys())
            vals = [[float(result.stats_ls[k]) if result.stats_ls[k] is not None else np.nan
                     for k in keys]]
            text = [[f"{v:.4f}" if v == v else "" for v in vals[0]]]
            fig.add_trace(
                go.Heatmap(z=vals, x=keys, y=["LS"], colorscale="RdYlGn", showscale=True,
                           text=text, texttemplate="%{text}"),
                row=row, col=1,
            )
            fig.update_xaxes(title_text="Metric", row=row, col=1, type="category", tickfont=dict(size=8))
            fig.update_yaxes(title_text="", row=row, col=1, type="category", tickfont=dict(size=8))

    fig.update_layout(
        title=title or "Long-Short Return Sheet",
        template="plotly_white",
        height=300 * len(rows),
        margin=dict(t=80, b=40, l=40, r=40),
        showlegend=True,
    )
    return fig if return_fig else None

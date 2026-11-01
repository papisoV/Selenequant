# Ported from AlphaPurify (MIT)
"""Plotly render layer for :class:`ICPanelResult`.

Design-intent port of AlphaPurify ``FactorAnalyzer.create_single_fac_ic_sheet``
(``FactorAnalyzer.py`` L2905-3341), simplified to Selene's single-horizon
``ICPanelResult`` and a native ``make_subplots`` layout (the source's pixel-precise
canvas domain arithmetic is an orchestration detail and is not carried over).

Rows are emitted only when the underlying data is present, so an empty or sparse
panel degrades to a bare figure instead of raising:

- **IC trajectory** — per-date IC line plus the rolling IR (ICPanelResult stores
  ``rolling_ir`` = rolling mean / rolling std, which plays the role of the
  source's rolling-mean overlay) and a red dotted mean-IC benchmark.
- **Cumulative IC** — ``rolling_ic.cumsum()``.
- **Self-correlation** — ``autocorr`` scatter plus its mean line.
- **Q-Q normal plot** — theoretical vs standardized sample quantiles (needs >= 8
  finite IC points and non-zero spread, matching ``ICPanel._qq_stats``).
- **Group IC contribution** — horizontal RdYlGn bar when ``sector_ic`` is
  non-empty (``by_group`` was set).
- **Monthly heatmap** — year x month sum of daily IC.
- **Statistics** — single-row heatmap of ``ic_distribution`` metrics.

This module imports no torch/polars; the IC panel itself is produced by
``ic_panel.ICPanel.compute``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy import stats

if TYPE_CHECKING:
    from .ic_panel import ICPanelResult

_ROW_TITLES = {
    "ic": "IC Trajectory",
    "cumic": "Cumulative IC",
    "autocorr": "IC Self-Correlation",
    "qq": "IC Q-Q Plot (Normal)",
    "sector": "Group IC Contribution",
    "monthly": "Aggregated IC (Year x Month)",
    "stats": "IC Distribution Statistics",
}


def _date_labels(index) -> list[str]:
    return [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in index]


def _domain_axes(row: int) -> tuple[str, str]:
    return f"x{row if row > 1 else ''} domain", f"y{row if row > 1 else ''} domain"


def create_ic_sheet(
    panel: "ICPanelResult",
    *,
    title: str | None = None,
    return_fig: bool = True,
) -> go.Figure | None:
    """Render an :class:`ICPanelResult` as a multi-row diagnostic sheet.

    Returns the ``plotly.graph_objects.Figure`` when ``return_fig`` is True
    (the default), else ``None``. Empty panels produce a figure with a single
    "No IC data available" annotation rather than raising.
    """
    ic = panel.rolling_ic.dropna()
    autocorr = panel.autocorr.dropna()
    has_ic = len(ic) > 0
    has_autocorr = len(autocorr) > 0
    has_qq = has_ic and len(ic) >= 8 and bool(np.isfinite(ic.std(ddof=1))) and ic.std(ddof=1) != 0
    has_sector = panel.sector_ic is not None and len(panel.sector_ic) > 0
    has_monthly = panel.monthly_heatmap is not None and panel.monthly_heatmap.size > 0
    has_stats = bool(panel.ic_distribution)

    rows: list[str] = []
    if has_ic:
        rows += ["ic", "cumic"]
    if has_autocorr:
        rows.append("autocorr")
    if has_qq:
        rows.append("qq")
    if has_sector:
        rows.append("sector")
    if has_monthly:
        rows.append("monthly")
    if has_stats:
        rows.append("stats")

    fig = go.Figure()
    if not rows:
        fig.add_annotation(text="No IC data available", showarrow=False)
        fig.update_layout(title=title or "IC Diagnostic Sheet")
        return fig if return_fig else None

    specs = [[{"type": "heatmap"} if r in ("monthly", "stats") else {"type": "xy"}] for r in rows]
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
        if key == "ic":
            dates = _date_labels(ic.index)
            fig.add_trace(
                go.Scatter(x=dates, y=ic.to_numpy(), mode="lines", name="IC",
                           line=dict(color="lightgreen", width=2)),
                row=row, col=1,
            )
            ir = panel.rolling_ir.reindex(ic.index).dropna()
            if len(ir):
                fig.add_trace(
                    go.Scatter(x=_date_labels(ir.index), y=ir.to_numpy(), mode="lines",
                               name="Rolling IR", line=dict(color="darkgreen", width=2)),
                    row=row, col=1,
                )
            mean_ic = panel.ic_distribution.get("Mean IC", np.nan)
            if not np.isnan(mean_ic):
                fig.add_trace(
                    go.Scatter(x=dates, y=[mean_ic] * len(dates), mode="lines", name="Mean IC",
                               line=dict(color="red", width=3, dash="dot")),
                    row=row, col=1,
                )
                xref, yref = _domain_axes(row)
                fig.add_annotation(
                    x=0.99, y=0.99, xref=xref, yref=yref, text=f"Mean = {mean_ic:.4f}",
                    showarrow=False, font=dict(color="red", size=12), xanchor="right", yanchor="top",
                )
            fig.update_xaxes(type="category", categoryorder="array", categoryarray=dates,
                             title_text="Date", row=row, col=1, tickfont=dict(size=8))
            fig.update_yaxes(title_text="IC", row=row, col=1, tickfont=dict(size=8))
        elif key == "cumic":
            dates = _date_labels(ic.index)
            fig.add_trace(
                go.Scatter(x=dates, y=ic.cumsum().to_numpy(), mode="lines",
                           name="Cumulative IC", line=dict(color="darkgreen", width=2)),
                row=row, col=1,
            )
            fig.update_xaxes(type="category", categoryorder="array", categoryarray=dates,
                             title_text="Date", row=row, col=1, tickfont=dict(size=8))
            fig.update_yaxes(title_text="Cumulative IC", row=row, col=1, tickfont=dict(size=8))
        elif key == "autocorr":
            dates = _date_labels(autocorr.index)
            fig.add_trace(
                go.Scatter(x=dates, y=autocorr.to_numpy(), mode="markers",
                           name="Self-Correlation", marker=dict(size=6, color="darkgreen")),
                row=row, col=1,
            )
            m = float(autocorr.mean())
            fig.add_trace(
                go.Scatter(x=dates, y=[m] * len(dates), mode="lines", name="Mean Self-Corr",
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
            fig.update_yaxes(title_text="Self-Correlation", row=row, col=1, tickfont=dict(size=8))
        elif key == "qq":
            ic_sorted = np.sort(ic.to_numpy(dtype=float))
            n = len(ic_sorted)
            theoretical = stats.norm.ppf((np.arange(1, n + 1) - 0.5) / n)
            sample = (ic_sorted - ic_sorted.mean()) / ic_sorted.std(ddof=1)
            fig.add_trace(
                go.Scatter(x=theoretical, y=sample, mode="markers", name="IC",
                           marker=dict(color="darkgreen", size=6)),
                row=row, col=1,
            )
            lo = float(min(theoretical.min(), sample.min()))
            hi = float(max(theoretical.max(), sample.max()))
            fig.add_trace(
                go.Scatter(x=[lo, hi], y=[lo, hi], mode="lines", name="Base Line",
                           line=dict(color="red", dash="dot", width=3)),
                row=row, col=1,
            )
            fig.update_xaxes(title_text="Theoretical Quantiles", row=row, col=1, tickfont=dict(size=8))
            fig.update_yaxes(title_text="Sample Quantiles", row=row, col=1, tickfont=dict(size=8))
        elif key == "sector":
            df = panel.sector_ic.sort_values("contrib", ascending=True)
            fig.add_trace(
                go.Bar(x=df["contrib"], y=df["group"], orientation="h",
                       marker=dict(color=df["contrib"], colorscale="RdYlGn", showscale=False)),
                row=row, col=1,
            )
            fig.update_xaxes(title_text="IC Contribution", row=row, col=1, tickfont=dict(size=8))
            fig.update_yaxes(title_text="Group", type="category", row=row, col=1, tickfont=dict(size=8))
        elif key == "monthly":
            heat = panel.monthly_heatmap
            fig.add_trace(
                go.Heatmap(z=heat.to_numpy(), x=[str(c) for c in heat.columns],
                           y=[str(i) for i in heat.index], colorscale="RdYlGn", showscale=True),
                row=row, col=1,
            )
            fig.update_xaxes(title_text="Month", row=row, col=1, type="category", tickfont=dict(size=8))
            fig.update_yaxes(title_text="Year", row=row, col=1, type="category", tickfont=dict(size=8))
        elif key == "stats":
            keys = list(panel.ic_distribution.keys())
            vals = [[float(panel.ic_distribution[k]) if panel.ic_distribution[k] is not None else np.nan
                     for k in keys]]
            text = [[f"{v:.4f}" if v == v else "" for v in vals[0]]]
            fig.add_trace(
                go.Heatmap(z=vals, x=keys, y=["IC"], colorscale="RdYlGn", showscale=True,
                           text=text, texttemplate="%{text}"),
                row=row, col=1,
            )
            fig.update_xaxes(title_text="Metric", row=row, col=1, type="category", tickfont=dict(size=8))
            fig.update_yaxes(title_text="", row=row, col=1, type="category", tickfont=dict(size=8))

    fig.update_layout(
        title=title or "IC Diagnostic Sheet",
        template="plotly_white",
        height=300 * len(rows),
        margin=dict(t=80, b=40, l=40, r=40),
        showlegend=True,
    )
    return fig if return_fig else None

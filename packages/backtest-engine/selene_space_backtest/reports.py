"""Serialization helpers turning a BacktestResult into portable formats."""

from __future__ import annotations

import json
import pathlib

from selene_space_types.strategy import BacktestResult


def to_dict(result: BacktestResult) -> dict:
    """Flat dict view with metrics nested; safe for JSON round-trips."""
    return {
        "id": result.id,
        "strategy_id": result.strategy_id,
        "symbol": result.symbol,
        "interval": result.interval,
        "start_date": result.start_date,
        "end_date": result.end_date,
        "created_at": result.created_at,
        "metrics": result.metrics.model_dump(),
        "equity_curve": list(result.equity_curve),
        "buy_hold_curve": list(result.buy_hold_curve),
        "drawdown_curve": list(result.drawdown_curve),
        "trades": [dict(t) for t in result.trades],
        "trades_count": result.trades_count,
        "lookahead_warnings": list(result.lookahead_warnings),
    }


def to_json(result: BacktestResult, *, indent: int = 2) -> str:
    return json.dumps(to_dict(result), indent=indent, default=str)


def to_html_report(
    result: BacktestResult,
    output_path: str | pathlib.Path | None = None,
    include_plotlyjs: str = "cdn",
    title: str | None = None,
) -> str | pathlib.Path:
    """Serialize the viz figure to a self-contained HTML document.

    With ``output_path`` given, writes the document there and returns the
    path; otherwise returns the HTML string.
    """
    from selene_space_backtest.viz import render_backtest_report

    fig = render_backtest_report(result, title=title)
    if fig is None:
        html = "<html><body><p>No backtest data available</p></body></html>"
    else:
        html = fig.to_html(include_plotlyjs=include_plotlyjs)

    if output_path is None:
        return html
    path = pathlib.Path(output_path)
    path.write_text(html, encoding="utf-8")
    return path

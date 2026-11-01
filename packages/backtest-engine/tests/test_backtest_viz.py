"""Tests for selene_space_backtest.viz — static backtest report rendering.

Verifies :func:`render_backtest_report` produces a multi-row plotly figure from
a :class:`BacktestResult`, degrades gracefully on empty data, layers entry/exit
markers for small trade counts, and that the package viz layer stays torch-free.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import plotly.graph_objects as go

from selene_space_backtest.reports import to_html_report
from selene_space_backtest.viz import render_backtest_report
from selene_space_types.strategy import BacktestMetrics, BacktestResult

_PKG_DIRS = [
    str(pathlib.Path(__file__).resolve().parents[2] / "backtest-engine"),
    str(pathlib.Path(__file__).resolve().parents[2] / "shared-types"),
]


def _metrics(**over: float) -> BacktestMetrics:
    base = dict(
        total_return=0.15,
        annual_return=0.45,
        sharpe_ratio=1.8,
        max_drawdown=-0.08,
        win_rate=0.55,
        profit_factor=1.6,
        total_trades=3,
        dsr=0.97,
        pbo=0.20,
        haircut_sharpe=1.5,
        sortino_ratio=2.1,
        calmar_ratio=3.0,
    )
    base.update(over)
    return BacktestMetrics(**base)  # type: ignore[arg-type]


def _full_result() -> BacktestResult:
    return BacktestResult(
        id="bt-viz-001",
        strategy_id="strat-001",
        symbol="BTC/USDT",
        interval="1h",
        start_date="2025-01-01T00:00:00",
        end_date="2025-03-31T23:59:59",
        metrics=_metrics(),
        equity_curve=[10000.0, 10500.0, 11500.0, 11000.0, 12000.0],
        drawdown_curve=[0.0, 0.0, -0.02, -0.05, -0.01],
        buy_hold_curve=[10000.0, 10200.0, 10100.0, 10300.0, 10400.0],
        trades=[
            {"entry_time": 1, "entry_price": 100.0, "exit_time": 2,
             "exit_price": 105.0, "pnl": 5.0, "side": "long"},
            {"entry_time": 3, "entry_price": 110.0, "exit_time": 4,
             "exit_price": 108.0, "pnl": -2.0, "side": "long"},
        ],
        trades_count=2,
        created_at=1711000000000,
    )


def test_full_result_renders_multirow_figure() -> None:
    """A populated result yields a figure with equity, drawdown, per-trade, metrics rows."""
    fig = render_backtest_report(_full_result())
    assert isinstance(fig, go.Figure)
    # equity + buyhold + entry + exit markers + drawdown + per-trade bar + metrics heatmap
    assert len(fig.data) >= 4
    html = fig.to_html(full_html=True)
    assert "plotly" in html and "<html" in html


def test_empty_equity_degrades_to_annotation() -> None:
    """An empty equity curve produces a single-annotation figure, not an error."""
    result = BacktestResult(
        id="bt-empty",
        strategy_id="s",
        start_date="2025-01-01T00:00:00",
        end_date="2025-01-02T00:00:00",
        metrics=BacktestMetrics(
            total_return=0.0, annual_return=0.0, sharpe_ratio=0.0,
            max_drawdown=0.0, win_rate=0.0, profit_factor=0.0, total_trades=0,
        ),
        equity_curve=[],
        trades_count=0,
        created_at=1711000000000,
    )
    fig = render_backtest_report(result)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0
    # annotation present (no crash)
    assert any(
        getattr(t, "text", "") == "No backtest data available"
        for t in fig.layout.annotations
    )


def test_partial_data_skips_missing_rows() -> None:
    """Missing drawdown / trades skip those rows; the figure stays valid."""
    result = BacktestResult(
        id="bt-partial",
        strategy_id="s",
        start_date="2025-01-01T00:00:00",
        end_date="2025-01-31T00:00:00",
        metrics=_metrics(dsr=0.0, pbo=0.0, haircut_sharpe=0.0,
                         sortino_ratio=0.0, calmar_ratio=0.0),
        equity_curve=[10000.0, 11000.0, 10500.0],
        drawdown_curve=[],  # missing
        trades=[],          # missing
        trades_count=0,
        created_at=1711000000000,
    )
    fig = render_backtest_report(result)
    assert isinstance(fig, go.Figure)
    # only equity row traces (equity + buyhold if present; buyhold empty here → just equity)
    assert len(fig.data) >= 1


def test_to_html_report_produces_html() -> None:
    """to_html_report returns a self-contained HTML document."""
    html = to_html_report(_full_result())
    assert isinstance(html, str)
    assert "<html" in html
    assert "plotly" in html


def test_to_html_report_empty_result_is_safe() -> None:
    """Empty result yields a minimal HTML, not an exception."""
    result = BacktestResult(
        id="bt-empty2",
        strategy_id="s",
        start_date="2025-01-01T00:00:00",
        end_date="2025-01-02T00:00:00",
        metrics=BacktestMetrics(
            total_return=0.0, annual_return=0.0, sharpe_ratio=0.0,
            max_drawdown=0.0, win_rate=0.0, profit_factor=0.0, total_trades=0,
        ),
        equity_curve=[],
        trades_count=0,
        created_at=1711000000000,
    )
    # render_backtest_report returns a figure with an annotation (not None, since
    # return_fig defaults True); to_html_report serializes whatever it gets.
    html = to_html_report(result)
    assert "<html" in html


def test_entry_exit_markers_for_small_trade_count() -> None:
    """Trades <= 50 with entry_time/exit_time layer ▲/▼ markers on the equity row."""
    result = _full_result()  # 2 trades
    fig = render_backtest_report(result)
    names = [t.name for t in fig.data]
    assert "Entry" in names
    assert "Exit" in names


def test_markers_skipped_for_large_trade_count() -> None:
    """Trades > 50 skip the entry/exit markers (readability guard)."""
    many_trades = [
        {"entry_time": i, "entry_price": 100.0 + i, "exit_time": i + 1,
         "exit_price": 101.0 + i, "pnl": float(i % 3 - 1)}
        for i in range(60)
    ]
    result = BacktestResult(
        id="bt-many",
        strategy_id="s",
        symbol="BTC/USDT",
        interval="1h",
        start_date="2025-01-01T00:00:00",
        end_date="2025-06-30T00:00:00",
        metrics=_metrics(),
        equity_curve=[10000.0 + i for i in range(120)],
        drawdown_curve=[0.0] * 120,
        trades=many_trades,
        trades_count=60,
        created_at=1711000000000,
    )
    fig = render_backtest_report(result)
    names = [t.name for t in fig.data]
    assert "Entry" not in names
    assert "Exit" not in names
    # per-trade bar still present
    assert any("PnL" in (n or "") for n in names)


def test_viz_import_is_torch_free() -> None:
    """Importing the viz module in a fresh interpreter pulls no torch."""
    code = (
        "import sys\n"
        f"sys.path[:0] = {_PKG_DIRS!r}\n"
        "import selene_space_backtest.viz\n"
        "print('TORCH_PRESENT' if 'torch' in sys.modules else 'TORCH_FREE')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "TORCH_FREE" in proc.stdout

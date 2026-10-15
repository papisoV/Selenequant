"""A-share factor mining commands."""

from __future__ import annotations

import click


@click.group("ashare")
def ashare_group() -> None:
    """A-share factor mining, evaluation, and backtesting."""


@ashare_group.command("fetch")
@click.option("--symbols", required=True, help="Comma-separated A-share stock codes.")
@click.option("--start-date", default="20240101", help="Start date (YYYYMMDD).")
@click.option("--end-date", default=None, help="End date (YYYYMMDD).")
@click.option("--adjust", default="hfq", help="Price adjustment: hfq/qfq/''.")
def ashare_fetch(
    symbols: str, start_date: str, end_date: str | None, adjust: str
) -> None:
    """Fetch A-share daily kline data with Parquet caching."""
    from datetime import datetime

    from selene_space_ashare.data_fetcher import batch_daily_klines

    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    codes = [s.strip() for s in symbols.split(",")]
    click.echo(f"Fetching {len(codes)} stocks: {codes}")

    results = batch_daily_klines(
        codes, start_date=start_date, end_date=end_date, adjust=adjust
    )
    for code, df in results.items():
        click.echo(f"  {code}: {len(df)} rows cached")


@ashare_group.command("factors")
@click.option("--symbols", required=True, help="Comma-separated A-share stock codes.")
@click.option("--start-date", default="20240101", help="Start date (YYYYMMDD).")
@click.option("--end-date", default=None, help="End date (YYYYMMDD).")
@click.option("--output", default="factors.parquet", help="Output parquet path.")
def ashare_factors(
    symbols: str, start_date: str, end_date: str | None, output: str
) -> None:
    """Calculate technical factors for A-share stocks."""
    from datetime import datetime

    import pandas as pd

    from selene_space_ashare.data_fetcher import get_daily_klines
    from selene_space_ashare.factor_calc import calculate_technical_factors

    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    codes = [s.strip() for s in symbols.split(",")]
    all_factors: list[pd.DataFrame] = []

    for code in codes:
        klines = get_daily_klines(code, start_date=start_date, end_date=end_date)
        if klines.empty or len(klines) < 60:
            click.echo(f"  {code}: insufficient data ({len(klines)} rows)")
            continue
        factors = calculate_technical_factors(klines)
        factors["symbol"] = code
        all_factors.append(factors)
        click.echo(f"  {code}: {len(factors)} rows, {len(factors.columns)} factors")

    if all_factors:
        panel = pd.concat(all_factors, ignore_index=True)
        panel.to_parquet(output, index=False)
        click.echo(
            f"Factor panel saved to {output}: {len(panel)} rows, {len(panel.columns)} columns"
        )
    else:
        click.echo("No factors calculated.")


@ashare_group.command("evaluate")
@click.option(
    "--panel",
    required=True,
    type=click.Path(exists=True),
    help="Factor panel parquet file.",
)
@click.option(
    "--factors", default=None, help="Comma-separated factor names (default: all)."
)
@click.option("--forward-col", default="fwd_5d", help="Forward return column.")
def ashare_evaluate(panel: str, factors: str | None, forward_col: str) -> None:
    """Run IC/IR factor evaluation."""
    import pandas as pd

    from selene_space_ashare.factor_calc import TECHNICAL_CALCULATORS
    from selene_space_ashare.factor_eval import factor_evaluation_report

    df = pd.read_parquet(panel)
    factor_names = factors.split(",") if factors else list(TECHNICAL_CALCULATORS.keys())

    report = factor_evaluation_report(df, factor_names, forward_col)
    if report.empty:
        click.echo("No evaluation results.")
        return

    click.echo(
        report[["factor", "IC_mean", "IR", "IC_pos_rate", "t_stat"]].to_string(
            index=False
        )
    )


@ashare_group.command("backtest")
@click.option(
    "--panel", required=True, type=click.Path(exists=True), help="Factor panel parquet."
)
@click.option(
    "--strategy", default="trend_follow", help="Strategy: trend_follow/mean_reversion."
)
@click.option("--n-long", default=5, help="Number of long positions.")
def ashare_backtest(panel: str, strategy: str, n_long: int) -> None:
    """Run A-share factor-based strategy backtest."""
    import pandas as pd

    from selene_space_ashare.data_fetcher import get_index_daily
    from selene_space_ashare.regime import calc_index_regime
    from selene_space_ashare.strategy import (
        make_trend_follow_strategy,
        make_mean_reversion_strategy,
    )
    from selene_space_ashare.composite import build_composite_panel
    from selene_space_ashare.backtest import backtest_full_strategy

    df = pd.read_parquet(panel)

    index_daily = get_index_daily("000300")
    if index_daily.empty:
        click.echo("Failed to fetch index data for regime detection.")
        return
    regime_df = calc_index_regime(index_daily)

    if strategy == "mean_reversion":
        config, factor_names = make_mean_reversion_strategy(n_long=n_long)
        panel_c, _ = build_composite_panel(df, factor_names, weight_method="equal")
    else:
        config, factor_names = make_trend_follow_strategy(n_long=n_long)
        panel_c, _ = build_composite_panel(df, factor_names, weight_method="equal")

    forward_col = f"fwd_{config.holding_period}d"
    result = backtest_full_strategy(panel_c, regime_df, config, forward_col=forward_col)

    m = result["metrics"]
    click.echo(f"\nStrategy: {config.name}")
    click.echo(f"  Sharpe: {m.get('sharpe', 'N/A'):.2f}")
    click.echo(f"  Max DD: {m.get('max_drawdown', 'N/A'):.1%}")
    click.echo(f"  Win Rate: {m.get('invested_win_rate', 'N/A'):.1%}")
    click.echo(f"  Cumulative Return: {m.get('cumulative_return', 'N/A'):.1%}")
    click.echo(f"  Time in Market: {m.get('time_in_market', 'N/A'):.1%}")


@ashare_group.command("report")
@click.option("--symbols", required=True, help="Comma-separated A-share stock codes.")
@click.option("--start", default="20250101", help="Start date (YYYYMMDD).")
@click.option("--end", default=None, help="End date (YYYYMMDD).")
@click.option(
    "--fwd-col", default="fwd_5d", help="Forward return column (fwd_5d/fwd_21d)."
)
@click.option(
    "--factors", default=None, help="Comma-separated factor names (default: all)."
)
@click.option(
    "--output", default=None, help="Write JSON report to this path (default: stdout)."
)
def ashare_report(
    symbols: str,
    start: str,
    end: str | None,
    fwd_col: str,
    factors: str | None,
    output: str | None,
) -> None:
    """One-shot factor IC/quantile/correlation report on cached klines."""
    import json

    from selene_space_ashare.factor_report import (
        build_factor_panel,
        generate_factor_report,
    )

    codes = [s.strip() for s in symbols.split(",")]
    panel = build_factor_panel(codes, start_date=start, end_date=end or "")
    factor_names = factors.split(",") if factors else None
    report = generate_factor_report(panel, factors=factor_names, forward_col=fwd_col)

    blob = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(blob)
        click.echo(
            f"report written: {output} ({report['n_days']} days, {report['n_symbols']} symbols)"
        )
    else:
        click.echo(blob[:2000])
        ic_rows = report.get("ic_report", [])
        if ic_rows:
            click.echo("\nIC summary (top by |IR|):")
            for row in ic_rows[:10]:
                click.echo(
                    f"  {row['factor']:<22} IC={row['IC_mean']} IR={row['IR']} t={row['t_stat']}"
                )
        orth = report.get("orthogonal")
        if orth:
            kept_rows = orth.get("kept", [])
            click.echo(f"\nOrthogonal (greedy residual, kept {len(kept_rows)}):")
            for row in kept_rows:
                click.echo(f"  kept: {row['factor']} t={row['t_stat']}")
            for row in orth.get("dropped", []):
                reason = str(row.get("reason", ""))[:80]
                click.echo(f"  dropped: {row['factor']} — {reason}")


@ashare_group.command("style-report")
@click.option("--symbols", required=True, help="Comma-separated A-share stock codes.")
@click.option("--start", default="20250101", help="Start date (YYYYMMDD).")
@click.option("--end", default=None, help="End date (YYYYMMDD).")
@click.option(
    "--fwd-col", default="fwd_5d", help="Forward return column (fwd_5d/fwd_21d)."
)
@click.option("--lookback", default=20, help="Regime lookback days.")
@click.option(
    "--output", default=None, help="Write JSON to this path (default: stdout)."
)
def ashare_style_report(
    symbols: str,
    start: str,
    end: str | None,
    fwd_col: str,
    lookback: int,
    output: str | None,
) -> None:
    """SIZE/VALUE/GROWTH daily long-short spread + regime summary."""
    import json

    from selene_space_ashare.factor_report import build_factor_panel
    from selene_space_ashare.style_monitor import full_style_report

    codes = [s.strip() for s in symbols.split(",")]
    panel = build_factor_panel(codes, start_date=start, end_date=end or "")
    report = full_style_report(panel, forward_col=fwd_col, lookback=lookback)

    blob = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(blob)
        for style, info in report["styles"].items():
            click.echo(
                f"  {style}: {info.get('regime')} (cum={info.get('cum_spread')})"
            )
    else:
        click.echo(blob)


@ashare_group.command("regime")
@click.option("--index", default="000300", help="Index code for regime detection.")
def ashare_regime(index: str) -> None:
    """Detect current market regime from index data."""
    from selene_space_ashare.data_fetcher import get_index_daily
    from selene_space_ashare.regime import calc_index_regime, get_regime_on_date

    index_daily = get_index_daily(index)
    if index_daily.empty:
        click.echo(f"Failed to fetch index data for {index}")
        return

    regime_df = calc_index_regime(index_daily)
    latest = regime_df.iloc[-1]

    regime = get_regime_on_date(regime_df, str(latest["date"]))
    click.echo(f"Index: {index}")
    click.echo(f"Date: {latest['date']}")
    click.echo(f"Trend: {regime.trend.value}")
    click.echo(f"Volatility: {regime.vol.value}")
    click.echo(f"Close: {regime.index_close:.2f}")
    click.echo(f"MA20: {regime.index_ma20:.2f}")
    click.echo(f"MA60: {regime.index_ma60:.2f}")
    click.echo(f"Realized Vol 20D: {regime.realized_vol_20d:.2%}")


@ashare_group.command("portfolio")
@click.option(
    "--panel", required=True, type=click.Path(exists=True), help="Factor panel parquet."
)
@click.option("--benchmark", default="000300", help="Benchmark index code.")
@click.option("--rebalance-days", default=5, help="Holding period in trading days.")
@click.option("--n-long", default=20, help="Number of long positions.")
@click.option("--cost-bps", default=15.0, help="One-way transaction cost (bps).")
@click.option(
    "--fwd-col",
    default="fwd_5d",
    help="Forward return column (horizon = rebalance days).",
)
@click.option(
    "--factors", default=None, help="Comma-separated factor names (default: whitelist)."
)
def ashare_portfolio(
    panel: str,
    benchmark: str,
    rebalance_days: int,
    n_long: int,
    cost_bps: float,
    fwd_col: str,
    factors: str | None,
) -> None:
    """Run periodic-rebalance portfolio backtest vs benchmark with excess metrics."""
    import pandas as pd

    from selene_space_ashare.portfolio_rebal import run_portfolio_backtest

    df = pd.read_parquet(panel)
    factor_names = (
        [f.strip() for f in factors.split(",") if f.strip()] if factors else None
    )

    result = run_portfolio_backtest(
        df,
        factor_names=factor_names,
        benchmark_index=benchmark,
        n_long=n_long,
        rebalance_days=rebalance_days,
        forward_col=fwd_col,
        cost_bps=cost_bps,
    )

    p = result["portfolio_metrics"]
    b = result["benchmark_metrics"]
    e = result["excess_metrics"]
    cfg = result["config"]

    click.echo(
        f"\nPortfolio: {cfg['n_long']} names, rebalance {cfg['rebalance_days']}d"
    )
    click.echo(
        f"Factors detected: {cfg['n_factors_detected']} "
        f"({', '.join(cfg['factor_names'][:8])}"
        f"{'...' if cfg['n_factors_detected'] > 8 else ''})"
    )
    click.echo(f"Windows settled: {len(result['windows'])}")

    click.echo("\n  Portfolio    Cum        Ann        Sharpe     MDD")
    click.echo(
        f"  {'long-only':<12} {p['cumulative_return']:>8.1%}   "
        f"{p['annual_return']:>8.1%}   {p['sharpe']:>6.2f}   {p['max_drawdown']:>8.1%}"
    )
    click.echo(
        f"  {'benchmark':<12} {b['cumulative_return']:>8.1%}   "
        f"{b['annual_return']:>8.1%}   {b['sharpe']:>6.2f}   {b['max_drawdown']:>8.1%}"
    )

    click.echo("\n  Excess vs benchmark")
    click.echo(f"  Cumulative:   {e['excess_cumulative']:.1%}")
    click.echo(f"  Annualized:   {e['excess_annual']:.1%}")
    click.echo(f"  TE:           {e['te']:.2%}")
    click.echo(f"  IR:           {e['ir']:.2f}")
    click.echo(f"  Excess MDD:   {e['excess_max_drawdown']:.1%}")
    click.echo(f"  Win Rate:     {e['excess_win_rate']:.1%}")


@ashare_group.command("paramlab")
@click.option(
    "--panel", required=True, type=click.Path(exists=True), help="Factor panel parquet."
)
@click.option("--benchmark", default="000300", help="Benchmark index code.")
@click.option("--rebalances", default="5,21", help="Comma-separated rebalance days.")
@click.option(
    "--n-longs", default="20,30,50", help="Comma-separated long-position counts."
)
@click.option(
    "--stock-weights",
    default="equal,slsqp",
    help="Comma-separated weighting schemes (equal, slsqp, inv_vol).",
)
@click.option(
    "--weight-methods",
    default="ir_walkforward",
    help="Comma-separated composite weighting methods.",
)
@click.option("--cost-bps", default=15.0, help="One-way transaction cost (bps).")
@click.option(
    "--factors", default=None, help="Comma-separated factor names (default: whitelist)."
)
@click.option(
    "--output",
    default=None,
    type=click.Path(),
    help="Optional CSV path (default: print to stdout).",
)
def ashare_paramlab(
    panel: str,
    benchmark: str,
    rebalances: str,
    n_longs: str,
    stock_weights: str,
    weight_methods: str,
    cost_bps: float,
    factors: str | None,
    output: str | None,
) -> None:
    """Parameter sensitivity grid over the periodic-rebalance engine.

    Read the full grid, do not cherry-pick a cell: this is a sensitivity
    check, not optimization.
    """
    import pandas as pd

    from selene_space_ashare.portfolio_rebal import run_param_lab

    df = pd.read_parquet(panel)
    factor_names = (
        [f.strip() for f in factors.split(",") if f.strip()] if factors else None
    )

    def _csv_ints(raw: str) -> tuple[int, ...]:
        return tuple(int(x.strip()) for x in raw.split(",") if x.strip())

    result = run_param_lab(
        df,
        factor_names=factor_names,
        benchmark_index=benchmark,
        rebalances=_csv_ints(rebalances),
        n_longs=_csv_ints(n_longs),
        stock_weights=tuple(s.strip() for s in stock_weights.split(",") if s.strip()),
        weight_methods=tuple(s.strip() for s in weight_methods.split(",") if s.strip()),
        cost_bps=cost_bps,
    )

    if output:
        result.to_csv(output, index=False)
        click.echo(f"Saved {len(result)} rows to {output}")
        return

    with pd.option_context("display.width", 200, "display.max_columns", 20):
        click.echo(result.to_string(index=False))

    positive = result["excess_annual"] > 0
    if not positive.any():
        click.echo(
            "\n[Read] All cells show negative excess annual return: the strategy "
            "underperforms across the whole grid - the weak result is not specific "
            "to one parameter point."
        )
    else:
        click.echo(
            "\n[Read] Some cells show positive excess annual return: a better "
            "parameter region exists, but beware overfitting - treat any single "
            "cell with suspicion."
        )

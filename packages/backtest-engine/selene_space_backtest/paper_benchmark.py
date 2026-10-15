"""Paper benchmark registry — external academic Sharpe ratios for the
"paper -> reproduce -> benchmark" research loop (roadmap C1).

Sources: the 60+ strategy table in the awesome-systematic-trading README
(https://github.com/academic-strategy-catalog), which lists
each academic strategy's paper Sharpe ratio, volatility, rebalancing cadence
and originating paper. These are *paper* values (from the original studies),
not values reproduced by any backtest engine, so they serve as an external
acceptance baseline: every selene strategy backtest can compare its local
Sharpe against the published number the recipe is supposed to deliver.

Each entry is a standalone academic recipe. ``SELENE_ALIASES`` maps the selene
template keys already landed on this platform to their paper of origin, so a
backtest of e.g. ``tsmom_vol_target`` can be checked against the Moskowitz-Ooi-
Pedersen 0.576 Sharpe automatically.
"""

from __future__ import annotations

from dataclasses import dataclass

from .overfit.report import OverfitReport


@dataclass(frozen=True)
class PaperBenchmark:
    """An academic strategy and its published performance numbers."""

    title: str
    sharpe: float | None  # annualized paper Sharpe (None when not reported)
    volatility: float | None  # annualized paper vol as decimal (None = N/A)
    rebalancing: str  # "Monthly" / "Daily" / "Weekly" / "Quarterly" / ...
    asset_class: str  # README asset-class group
    implementation: str | None  # QuantConnect implementation filename
    paper_url: str


def _bm(
    title: str,
    sharpe: float | None,
    volatility: float | None,
    rebalancing: str,
    asset_class: str,
    implementation: str | None,
    paper_url: str,
) -> PaperBenchmark:
    return PaperBenchmark(title, sharpe, volatility, rebalancing, asset_class, implementation, paper_url)


PAPER_BENCHMARKS: dict[str, PaperBenchmark] = {
    # Bonds, commodities, currencies, equities
    "ts_momentum": _bm("Time Series Momentum Effect", 0.576, 0.205, "Monthly", "Bonds, commodities, currencies, equities", "time-series-momentum-effect.py", "https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf"),
    "short_term_reversal_futures": _bm("Short Term Reversal with Futures", -0.05, 0.123, "Weekly", "Bonds, commodities, currencies, equities", "asset-class-momentum-rotational-system.py", "https://ideas.repec.org/a/eee/jbfina/v28y2004i6p1337-1361.html"),
    # Bonds, commodities, equities, REITs
    "asset_class_trend": _bm("Asset Class Trend-Following", 0.502, 0.104, "Monthly", "Bonds, commodities, equities, REITs", "asset-class-trend-following.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=962461"),
    "momentum_asset_allocation": _bm("Momentum Asset Allocation Strategy", 0.321, 0.11, "Monthly", "Bonds, commodities, equities, REITs", "asset-class-trend-following.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1585517"),
    # Bonds, equities
    "paired_switching": _bm("Paired Switching", 0.691, 0.095, "Quarterly", "Bonds, equities", "paired-switching.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1917044"),
    "fed_model": _bm("FED Model", 0.369, 0.143, "Monthly", "Bonds, equities", "fed-model.py", "https://www.researchgate.net/publication/228267011_The_FED_Model_and_Expected_Asset_Returns"),
    # Bonds, equities, REITs
    "value_momentum_across_assets": _bm("Value and Momentum Factors across Asset Classes", 0.155, 0.098, "Monthly", "Bonds, equities, REITs", "value-and-momentum-factors-across-asset-classes.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1079975"),
    # Commodities
    "skewness_commodities": _bm("Skewness Effect in Commodities", 0.482, 0.177, "Monthly", "Commodities", "skewness-effect-in-commodities.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2671165"),
    "return_asymmetry_commodity_futures": _bm("Return Asymmetry Effect in Commodity Futures", 0.239, 0.134, "Monthly", "Commodities", "return-asymmetry-effect-in-commodity-futures.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3918896"),
    "momentum_commodities": _bm("Momentum Effect in Commodities", 0.14, 0.203, "Monthly", "Commodities", "momentum-effect-in-commodities.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=702281"),
    "term_structure_commodities": _bm("Term Structure Effect in Commodities", 0.128, 0.231, "Monthly", "Commodities", "term-structure-effect-in-commodities.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1127213"),
    "wti_brent_spread": _bm("Trading WTI/BRENT Spread", -0.199, 0.116, "Daily", "Commodities", "trading-wti-brent-spread.py", "https://link.springer.com/article/10.1057/jdhf.2009.24"),
    # Cryptos
    "overnight_bitcoin": _bm("Overnight Seasonality in Bitcoin", 0.892, 0.208, "Intraday", "Cryptos", "intraday-seasonality-in-bitcoin.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4081000"),
    "rebalancing_premium": _bm("Rebalancing Premium in Cryptocurrencies", 0.698, 0.275, "Daily", "Cryptos", "rebalancing-premium-in-cryptocurrencies.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3982120"),
    # Currencies
    "fx_carry": _bm("FX Carry Trade", 0.254, 0.078, "Monthly", "Currencies", "fx-carry-trade.py", "http://globalmarkets.db.com/new/docs/dbCurrencyReturns_March2009.pdf"),
    "dollar_carry": _bm("Dollar Carry Trade", 0.113, 0.058, "Monthly", "Currencies", "dollar-carry-trade.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1541230"),
    "currency_momentum": _bm("Currency Momentum Factor", -0.01, 0.067, "Monthly", "Currencies", "currency-momentum-factor.py", "http://globalmarkets.db.com/new/docs/dbCurrencyReturns_March2009.pdf"),
    "currency_ppp": _bm("Currency Value Factor - PPP Strategy", -0.103, 0.05, "Quarterly", "Currencies", "currency-value-factor-ppp-strategy.py", "http://globalmarkets.db.com/new/docs/dbCurrencyReturns_March2009.pdf"),
    # Equities
    "asset_growth": _bm("Asset Growth Effect", 0.835, 0.102, "Yearly", "Equities", "asset-growth-effect.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1335524"),
    "short_term_reversal_stocks": _bm("Short Term Reversal Effect in Stocks", 0.816, 0.214, "Weekly", "Equities", "short-term-reversal-in-stocks.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1605049"),
    "earnings_reversal": _bm("Reversal During Earnings-Announcements", 0.785, 0.257, "Daily", "Equities", "reversal-during-earnings-announcements.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2275982"),
    "size_small_cap": _bm("Size Factor - Small Capitalization Stocks Premium", 0.747, 0.111, "Yearly", "Equities", "small-capitalization-stocks-premium-anomaly.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3177539"),
    "low_vol_stocks": _bm("Low Volatility Factor Effect in Stocks", 0.717, 0.115, "Monthly", "Equities", "low-volatility-factor-effect-in-stocks.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=980865"),
    "lexical_density": _bm("How to Use Lexical Density of Company Filings", 0.688, 0.104, "Monthly", "Equities", "how-to-use-lexical-density-of-company-filings.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3921091"),
    "vol_risk_premium_stocks": _bm("Volatility Risk Premium Effect", 0.637, 0.132, "Monthly", "Equities", "volatility-risk-premium-effect.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=189840"),
    "pairs_trading_stocks": _bm("Pairs Trading with Stocks", 0.634, 0.085, "Daily", "Equities", "pairs-trading-with-stocks.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=141615"),
    "crude_oil_predicts": _bm("Crude Oil Predicts Equity Returns", 0.599, 0.115, "Monthly", "Equities", "crude-oil-predicts-equity-returns.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=460500"),
    "bab_stocks": _bm("Betting Against Beta Factor in Stocks", 0.594, 0.189, "Monthly", "Equities", "betting-against-beta-factor-in-stocks.py", "https://pages.stern.nyu.edu/~lpederse/papers/BettingAgainstBeta.pdf"),
    "trend_stocks": _bm("Trend-following Effect in Stocks", 0.569, 0.152, "Daily", "Equities", "trend-following-effect-in-stocks.py", "https://www.cis.upenn.edu/~mkearns/finread/trend.pdf"),
    "esg_momentum": _bm("ESG Factor Momentum Strategy", 0.559, 0.218, "Monthly", "Equities", "esg-factor-momentum-strategy.py", "https://www.semanticscholar.org/paper/Can-ESG-Add-Alpha-An-Analysis-of-ESG-Tilt-and-Nagy-Kassam/64f77da4f8ce5906a73ffe4e9eec7c49c0960acc"),
    "value_book_to_market": _bm("Value (Book-to-Market) Factor", 0.526, 0.119, "Monthly", "Equities", "value-book-to-market-factor.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2595747"),
    "soccer_clubs": _bm("Soccer Clubs' Stocks Arbitrage", 0.515, 0.142, "Daily", "Equities", "soccer-clubs-stocks-arbitrage.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1343685"),
    "synthetic_lending": _bm("Synthetic Lending Rates Predict Subsequent Market Return", 0.494, 0.137, "Daily", "Equities", "synthetic-lending-rates-predict-subsequent-market-return.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3976307"),
    "option_expiration_week": _bm("Option-Expiration Week Effect", 0.452, 0.05, "Weekly", "Equities", "option-expiration-week-effect.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1571786"),
    "dispersion_trading": _bm("Dispersion Trading", 0.432, 0.081, "Monthly", "Equities", "dispersion-trading.py", "https://www.academia.edu/16327015/EQUILIBRIUM_INDEX_AND_SINGLE_STOCK_VOLATILITY_RISK_PREMIA"),
    "mutual_fund_momentum": _bm("Momentum in Mutual Fund Returns", 0.414, 0.136, "Quarterly", "Equities", "momentum-in-mutual-fund-returns.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1462408"),
    "sector_momentum": _bm("Sector Momentum - Rotational System", 0.401, 0.141, "Monthly", "Equities", "sector-momentum-rotational-system.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1585517"),
    "smart_factors_combining": _bm("Combining Smart Factors Momentum and Market Portfolio", 0.388, 0.082, "Monthly", "Equities", "combining-smart-factors-momentum-and-market-portfolio.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3745517"),
    "momentum_reversal_vol_stocks": _bm("Momentum and Reversal Combined with Volatility Effect in Stocks", 0.375, 0.17, "Monthly", "Equities", "momentum-and-reversal-combined-with-volatility-effect-in-stocks.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1679464"),
    "overnight_anomaly": _bm("Market Sentiment and an Overnight Anomaly", 0.369, 0.036, "Daily", "Equities", "market-sentiment-and-an-overnight-anomaly.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3829582"),
    "january_barometer": _bm("January Barometer", 0.365, 0.074, "Monthly", "Equities", "january-barometer.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1436516"),
    "rd_expenditures": _bm("R&D Expenditures and Stock Returns", 0.354, 0.081, "Yearly", "Equities", "rd-expenditures-and-stock-returns.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=227564"),
    "cape_within_countries": _bm("Value Factor - CAPE Effect within Countries", 0.351, 0.202, "Yearly", "Equities", "value-factor-effect-within-countries.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2129474"),
    "twelve_month_cycle": _bm("12 Month Cycle in Cross-Section of Stocks Returns", 0.34, 0.437, "Monthly", "Equities", "12-month-cycle-in-cross-section-of-stocks-returns.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=687022"),
    "turn_of_month": _bm("Turn of the Month in Equity Indexes", 0.305, 0.072, "Daily", "Equities", "turn-of-the-month-in-equity-indexes.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=917884"),
    "payday_anomaly": _bm("Payday Anomaly", 0.269, 0.038, "Daily", "Equities", "payday-anomaly.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3257064"),
    "pairs_country_etfs": _bm("Pairs Trading with Country ETFs", 0.257, 0.057, "Daily", "Equities", "pairs-trading-with-country-etfs.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1958546"),
    "residual_momentum": _bm("Residual Momentum Factor", 0.24, 0.097, "Monthly", "Equities", "residual-momentum-factor.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2319861"),
    "earnings_premium": _bm("Earnings Announcement Premium", 0.192, 0.037, "Monthly", "Equities", "earnings-announcement-premium.py", "https://www.nber.org/system/files/working_papers/w13090/w13090.pdf"),
    "roa_effect": _bm("ROA Effect within Stocks", 0.155, 0.087, "Monthly", "Equities", "roa-effect-within-stocks.py", "https://static1.squarespace.com/static/5e6033a4ea02d801f37e15bb/t/5f61583e88f43b7d5b7196b5/1600215105801/Chen_Zhang_JF.pdf"),
    "weeks_high_stocks": _bm("52-Weeks High Effect in Stocks", 0.153, 0.19, "Monthly", "Equities", "52-weeks-high-effect-in-stocks.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1787378"),
    "fscore_reversal": _bm("Combining Fundamental FSCORE and Equity Short-Term Reversals", 0.153, 0.176, "Monthly", "Equities", "combining-fundamental-fscore-and-equity-short-term-reversals.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3097420"),
    "bab_international": _bm("Betting Against Beta Factor in International Equities", 0.142, 0.091, "Monthly", "Equities", "betting-against-beta-factor-in-country-equity-indexes.py", "https://pages.stern.nyu.edu/~lpederse/papers/BettingAgainstBeta.pdf"),
    "consistent_momentum": _bm("Consistent Momentum Strategy", 0.128, 0.288, "6 Months", "Equities", "consistent-momentum-strategy.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2652592"),
    "short_interest_ls": _bm("Short Interest Effect - Long-Short Version", 0.079, 0.066, "Monthly", "Equities", "short-interest-effect-long-short-version.py", "https://www.semanticscholar.org/paper/Why-Do-Short-Interest-Levels-Predict-Stock-Returns-Boehmer-Erturk/06418ef437dc7156229532a97d0f8392373eb297"),
    "momentum_asset_growth": _bm("Momentum Factor Combined with Asset Growth Effect", 0.058, 0.251, "Monthly", "Equities", "momentum-factor-combined-with-asset-growth-effect.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1684767"),
    "momentum_factor_stocks": _bm("Momentum Factor Effect in Stocks", -0.008, 0.218, "Monthly", "Equities", "momentum-factor-effect-in-stocks.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2435323"),
    "momentum_style_rotation": _bm("Momentum Factor and Style Rotation Effect", -0.056, 0.1, "Monthly", "Equities", "momentum-factor-and-style-rotation-effect.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1276815"),
    "earnings_repurchases": _bm("Earnings Announcements Combined with Stock Repurchases", -0.16, 0.001, "Daily", "Equities", "earnings-announcements-combined-with-stock-repurchases.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2589966"),
    "earnings_quality": _bm("Earnings Quality Factor", -0.18, 0.287, "Yearly", "Equities", "earnings-quality-factor.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2179247"),
    "accrual_anomaly": _bm("Accrual Anomaly", -0.272, 0.137, "Yearly", "Equities", "accrual-anomaly.py", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=546108"),
    "esg_stochastic_optimization": _bm("ESG, Price Momentum and Stochastic Optimization", None, None, "Monthly", "Equities", None, "https://quantpedia.com/strategies/esg-price-momentum-and-stochastic-optimization/"),
    "similarity_filings": _bm("The Positive Similarity of Company Filings and Stock Returns", None, None, "Monthly", "Equities", None, "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3690461"),
}

#: selene template key -> paper benchmark key (strategies already landed on this
#: platform that have a directly comparable academic recipe).
SELENE_ALIASES: dict[str, str] = {
    "tsmom_vol_target": "ts_momentum",  # B1: Moskowitz-Ooi-Pedersen 0.576
    "intraday_seasonality": "overnight_bitcoin",  # A1: BTC overnight 0.892
    "rebalance_premium": "rebalancing_premium",  # A2: crypto rebalancing 0.698
    "january_barometer": "january_barometer",  # B3: January Barometer 0.365
}


def get_benchmark(key: str) -> PaperBenchmark | None:
    """Look up a paper benchmark by its registry key."""
    return PAPER_BENCHMARKS.get(key)


def get_by_selene(selene_key: str) -> PaperBenchmark | None:
    """Look up the paper benchmark a selene template key maps to."""
    paper_key = SELENE_ALIASES.get(selene_key)
    return PAPER_BENCHMARKS.get(paper_key) if paper_key else None


def all_benchmarks() -> dict[str, PaperBenchmark]:
    """Return a copy of the full registry."""
    return dict(PAPER_BENCHMARKS)


# --- paper-vs-local comparison report ----------------------------------------


@dataclass(frozen=True)
class BenchmarkComparison:
    """A local backtest Sharpe compared against its paper benchmark."""

    selene_key: str
    paper_key: str
    paper_title: str
    paper_sharpe: float | None
    local_sharpe_annual: float
    sharpe_gap: float | None  # local - paper (None when paper not reported)
    meets_benchmark: bool | None  # local >= paper (None when paper not reported)
    overfit_passed: bool | None
    verdict: str

    def to_dict(self) -> dict:
        return {
            "selene_key": self.selene_key,
            "paper_key": self.paper_key,
            "paper_title": self.paper_title,
            "paper_sharpe": self.paper_sharpe,
            "local_sharpe_annual": self.local_sharpe_annual,
            "sharpe_gap": self.sharpe_gap,
            "meets_benchmark": self.meets_benchmark,
            "overfit_passed": self.overfit_passed,
            "verdict": self.verdict,
        }


def compare_to_paper(
    selene_key: str,
    local_sharpe_annual: float,
    overfit: OverfitReport | None = None,
    tolerance: float = 0.0,
) -> BenchmarkComparison:
    """Compare a local backtest Sharpe against the paper benchmark.

    ``tolerance`` lets a local Sharpe within ``tolerance`` of the paper value
    count as meeting the benchmark (e.g. 0.05 to absorb estimation noise).
    """
    paper = get_by_selene(selene_key)
    if paper is None or paper.sharpe is None:
        return BenchmarkComparison(
            selene_key=selene_key,
            paper_key="",
            paper_title="(no paper benchmark registered)",
            paper_sharpe=None,
            local_sharpe_annual=local_sharpe_annual,
            sharpe_gap=None,
            meets_benchmark=None,
            overfit_passed=overfit.passed if overfit else None,
            verdict="INFO - no paper benchmark registered for this strategy",
        )

    meets = local_sharpe_annual >= paper.sharpe - tolerance
    of_passed = overfit.passed if overfit else None

    if of_passed is False:
        verdict = (
            f"FAIL - local {local_sharpe_annual:.2f} vs paper {paper.sharpe:.2f} "
            f"({'MEETS' if meets else 'BELOW'}); overfit report FAIL"
        )
    elif meets:
        verdict = (
            f"PASS - local {local_sharpe_annual:.2f} vs paper {paper.sharpe:.2f}: MEETS benchmark"
        )
    else:
        verdict = (
            f"WARN - local {local_sharpe_annual:.2f} vs paper {paper.sharpe:.2f}: BELOW benchmark"
        )

    return BenchmarkComparison(
        selene_key=selene_key,
        paper_key=next((k for k, v in PAPER_BENCHMARKS.items() if v is paper), ""),
        paper_title=paper.title,
        paper_sharpe=paper.sharpe,
        local_sharpe_annual=local_sharpe_annual,
        sharpe_gap=round(local_sharpe_annual - paper.sharpe, 4),
        meets_benchmark=meets,
        overfit_passed=of_passed,
        verdict=verdict,
    )


def render_benchmark_report(
    comparison: BenchmarkComparison,
    overfit: OverfitReport | None = None,
) -> str:
    """Render a paper-vs-local benchmark comparison as a text block."""
    lines = [
        "=" * 64,
        " PAPER BENCHMARK REPORT",
        "=" * 64,
        f" Strategy    : {comparison.selene_key}",
        f" Paper       : {comparison.paper_title}",
    ]
    if comparison.paper_sharpe is not None:
        lines.append(
            f" Paper Sharpe: {comparison.paper_sharpe} (annual)"
        )
    lines.append(f" Local Sharpe: {comparison.local_sharpe_annual:.4f} (annual)")
    if comparison.sharpe_gap is not None:
        lines.append(f" Gap         : {comparison.sharpe_gap:+.4f}")
        lines.append(f" Meets paper : {comparison.meets_benchmark}")
    if overfit is not None:
        lines.append(f" Overfit     : {'PASS' if overfit.passed else 'FAIL'} ({overfit.verdict})")
    lines.append("-" * 64)
    lines.append(f" Verdict     : {comparison.verdict}")
    lines.append("=" * 64)
    return "\n".join(lines)

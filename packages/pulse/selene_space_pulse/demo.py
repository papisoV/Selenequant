from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .dashboard import write_dashboard
from .history import attach_history, records_from_report
from .models import (
    Asset,
    AssetAnalysis,
    BenchmarkComparison,
    BenchmarkSnapshot,
    DailyReport,
    DataFreshness,
    HistoryPoint,
    NewsItem,
    PriceSnapshot,
    SignalScore,
)
from .portfolio import enrich_portfolio
from .reporting import write_reports
from .site import SiteBuildResult, build_site


@dataclass(frozen=True)
class DemoBuildResult:
    root: Path
    history_path: Path
    report_paths: dict[str, Path]
    dashboard_path: Path
    site: SiteBuildResult


def build_demo(output_dir: str | Path, title: str = "AI Market Pulse Demo") -> DemoBuildResult:
    root = Path(output_dir)
    reports_dir = root / "reports"
    data_dir = root / "data"
    site_dir = root / "site"
    data_dir.mkdir(parents=True, exist_ok=True)

    generated_at = datetime(2026, 7, 8, 9, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    analyses, portfolio = enrich_portfolio(_demo_analyses())
    report = DailyReport(
        title=title,
        generated_at=generated_at,
        timezone="America/Los_Angeles",
        language="zh-CN",
        analyses=analyses,
        market_brief="5 assets analyzed. Benchmark context, relative strength, and data freshness are enabled in demo mode.",
        portfolio=portfolio,
        benchmarks=_demo_benchmarks(),
        insights=_demo_insights(analyses),
        portfolio_ai_summary=(
            "Demo brief: US mega-cap exposure remains concentrated in AI and platform names. "
            "AAPL is outperforming QQQ over 20/60 days, while NVDA and MSFT lag the benchmark. "
            "600519.SS is underperforming CSI 300 and should be reviewed with data freshness in mind."
        ),
        config_path="demo/watchlist.yaml",
    )
    history = _demo_history(records_from_report(report), generated_at)
    _write_history(data_dir / "history.jsonl", history)
    report = attach_history(report, history)

    report_paths = write_reports(report, reports_dir)
    dashboard_path = write_dashboard(history, reports_dir / "dashboard.html")
    site = build_site(reports_dir, site_dir, title=title)
    _write_demo_config(root / "watchlist.yaml")
    return DemoBuildResult(
        root=root,
        history_path=data_dir / "history.jsonl",
        report_paths=report_paths,
        dashboard_path=dashboard_path,
        site=site,
    )


def _demo_analyses() -> list[AssetAnalysis]:
    return [
        _analysis(
            asset=Asset(symbol="AAPL", name="Apple", market="US", quantity=10, cost_basis=185, tags=["mega-cap", "consumer-tech"]),
            close=310.66,
            previous=309.42,
            currency="USD",
            score=78,
            stance="constructive",
            risk="medium",
            metrics={"return_20d": 0.0303, "return_60d": 0.1926, "rsi14": 58.4, "atr_pct": 0.021, "drawdown_60d": -0.08},
            reasons=["Price remains above its 50-day trend.", "Relative strength versus QQQ is positive.", "Position P/L remains constructive."],
            benchmark=_comparison("QQQ", "Nasdaq 100 ETF", 0.0303, -0.0174, 0.1926, 0.1514),
            freshness=_freshness("2026-07-08", "yfinance", 220, 0, "fresh"),
        ),
        _analysis(
            asset=Asset(symbol="NVDA", name="NVIDIA", market="US", quantity=6, cost_basis=140, tags=["ai", "semiconductor"]),
            close=197.71,
            previous=196.93,
            currency="USD",
            score=21,
            stance="high risk",
            risk="high",
            metrics={"return_20d": -0.0523, "return_60d": 0.0482, "rsi14": 39.2, "atr_pct": 0.046, "drawdown_60d": -0.18},
            reasons=["Recent drawdown is large.", "20-day relative strength is negative.", "Volatility is elevated."],
            benchmark=_comparison("QQQ", "Nasdaq 100 ETF", -0.0523, -0.0174, 0.0482, 0.1514),
            freshness=_freshness("2026-07-08", "yfinance", 220, 0, "fresh"),
        ),
        _analysis(
            asset=Asset(symbol="MSFT", name="Microsoft", market="US", tags=["cloud", "ai"]),
            close=382.74,
            previous=388.82,
            currency="USD",
            score=36,
            stance="defensive",
            risk="high",
            metrics={"return_20d": -0.0704, "return_60d": 0.032, "rsi14": 42.6, "atr_pct": 0.026, "drawdown_60d": -0.14},
            reasons=["Price is below the 200-day moving average.", "Relative 20-day performance is weak.", "Momentum needs confirmation."],
            benchmark=_comparison("QQQ", "Nasdaq 100 ETF", -0.0704, -0.0174, 0.032, 0.1514),
            freshness=_freshness("2026-07-08", "yfinance", 220, 0, "fresh"),
        ),
        _analysis(
            asset=Asset(symbol="SPY", name="S&P 500 ETF", market="US", tags=["index"]),
            close=741.38,
            previous=747.75,
            currency="USD",
            score=72,
            stance="watch bullish",
            risk="low",
            metrics={"return_20d": 0.0029, "return_60d": 0.0911, "rsi14": 53.1, "atr_pct": 0.012, "drawdown_60d": -0.03},
            reasons=["Index trend remains broadly constructive.", "Risk is lower than single-name holdings.", "Short-term move is softer than recent trend."],
            benchmark=_comparison("QQQ", "Nasdaq 100 ETF", 0.0029, -0.0174, 0.0911, 0.1514),
            freshness=_freshness("2026-07-08", "yfinance", 220, 0, "fresh"),
        ),
        _analysis(
            asset=Asset(symbol="600519.SS", name="Kweichow Moutai", market="CN", quantity=1, cost_basis=1500, tags=["a-share", "consumer"]),
            close=1188.8,
            previous=1206.91,
            currency="CNY",
            score=12,
            stance="high risk",
            risk="high",
            metrics={"return_20d": -0.0588, "return_60d": -0.1885, "rsi14": 31.5, "atr_pct": 0.025, "drawdown_60d": -0.22},
            reasons=["Price is below the 200-day moving average.", "Relative strength versus CSI 300 is negative.", "Position is below cost basis."],
            benchmark=_comparison("000300.SS", "CSI 300", -0.0588, -0.0128, -0.1885, 0.0904),
            freshness=_freshness("2026-07-07", "yfinance", 220, 1, "fresh"),
        ),
    ]


def _analysis(
    asset: Asset,
    close: float,
    previous: float,
    currency: str,
    score: int,
    stance: str,
    risk: str,
    metrics: dict[str, float],
    reasons: list[str],
    benchmark: BenchmarkComparison,
    freshness: DataFreshness,
) -> AssetAnalysis:
    snapshot = PriceSnapshot(
        symbol=asset.symbol,
        name=asset.name or asset.symbol,
        currency=currency,
        last_close=close,
        previous_close=previous,
        change_pct=round(close / previous - 1, 6),
        start_date="2025-12-01",
        end_date=freshness.latest_date,
        rows=freshness.rows,
        source=freshness.source,
    )
    return AssetAnalysis(
        asset=asset,
        snapshot=snapshot,
        metrics=metrics,
        signal=SignalScore(score=score, stance=stance, risk_level=risk, reasons=reasons),
        news=[
            NewsItem(title=f"{asset.symbol} demo headline: market context and risk review", link="https://example.com/demo-news", source="Demo News"),
            NewsItem(title=f"{asset.symbol} demo headline: earnings and benchmark watch", link="https://example.com/demo-earnings", source="Demo Wire"),
        ],
        benchmark=benchmark,
        freshness=freshness,
    )


def _demo_benchmarks() -> list[BenchmarkSnapshot]:
    return [
        BenchmarkSnapshot(symbol="SPY", name="S&P 500 ETF", market="US", currency="USD", last_close=741.38, change_pct=-0.0085, return_20d=0.0029, return_60d=0.0911, source="yfinance", freshness=_freshness("2026-07-08", "yfinance", 220, 0, "fresh")),
        BenchmarkSnapshot(symbol="QQQ", name="Nasdaq 100 ETF", market="US", currency="USD", last_close=703.59, change_pct=-0.0082, return_20d=-0.0174, return_60d=0.1514, source="yfinance", freshness=_freshness("2026-07-08", "yfinance", 220, 0, "fresh")),
        BenchmarkSnapshot(symbol="000300.SS", name="CSI 300", market="CN", currency="CNY", last_close=4842.17, change_pct=0.0062, return_20d=-0.0128, return_60d=0.0904, source="yfinance", freshness=_freshness("2026-07-03", "yfinance", 220, 5, "stale")),
        BenchmarkSnapshot(symbol="^HSI", name="Hang Seng Index", market="HK", currency="HKD", last_close=23496.89, change_pct=-0.0051, return_20d=-0.0587, return_60d=-0.0925, source="yfinance", freshness=_freshness("2026-07-07", "yfinance", 220, 1, "fresh")),
    ]


def _comparison(symbol: str, name: str, asset_20d: float, bench_20d: float, asset_60d: float, bench_60d: float) -> BenchmarkComparison:
    rel20 = round(asset_20d - bench_20d, 6)
    rel60 = round(asset_60d - bench_60d, 6)
    if rel20 >= 0.02 and rel60 >= 0.02:
        verdict = "outperforming"
    elif rel20 <= -0.02 and rel60 <= -0.02:
        verdict = "underperforming"
    elif abs(rel20) < 0.02 and abs(rel60) < 0.02:
        verdict = "tracking"
    else:
        verdict = "mixed"
    return BenchmarkComparison(
        symbol=symbol,
        name=name,
        market="US" if symbol in {"SPY", "QQQ"} else "CN",
        source="demo",
        latest_date="2026-07-08",
        asset_return_20d=asset_20d,
        benchmark_return_20d=bench_20d,
        relative_return_20d=rel20,
        asset_return_60d=asset_60d,
        benchmark_return_60d=bench_60d,
        relative_return_60d=rel60,
        verdict=verdict,
    )


def _freshness(latest_date: str, source: str, rows: int, age_days: int, status: str) -> DataFreshness:
    message = (
        f"Latest trading day {latest_date}; source {source}."
        if status == "fresh"
        else f"Latest trading day {latest_date}, {age_days} calendar days behind; verify data freshness."
    )
    return DataFreshness(
        latest_date=latest_date,
        age_days=age_days,
        source=source,
        rows=rows,
        status=status,
        message=message,
    )


def _demo_history(latest: list[HistoryPoint], generated_at: datetime) -> list[HistoryPoint]:
    base = {
        "AAPL": {"close": 292.4, "score": 64, "risk": "medium", "currency": "USD", "market_value": 2924.0, "benchmark": "QQQ", "rel20": 0.002},
        "NVDA": {"close": 224.2, "score": 68, "risk": "medium", "currency": "USD", "market_value": 1345.2, "benchmark": "QQQ", "rel20": 0.041},
        "MSFT": {"close": 411.5, "score": 59, "risk": "medium", "currency": "USD", "market_value": None, "benchmark": "QQQ", "rel20": 0.008},
        "SPY": {"close": 719.0, "score": 70, "risk": "low", "currency": "USD", "market_value": None, "benchmark": "QQQ", "rel20": -0.012},
        "600519.SS": {"close": 1320.0, "score": 40, "risk": "high", "currency": "CNY", "market_value": 1320.0, "benchmark": "000300.SS", "rel20": -0.012},
    }
    records: list[HistoryPoint] = []
    start = generated_at.date() - timedelta(days=18)
    for day_index in range(18):
        day = start + timedelta(days=day_index)
        if day.weekday() >= 5:
            continue
        progress = day_index / 17
        for symbol, item in base.items():
            score = int(item["score"] + (day_index % 4) - (7 if symbol in {"NVDA", "MSFT", "600519.SS"} and progress > 0.55 else 0))
            close = float(item["close"]) * (1 + progress * (0.065 if symbol in {"AAPL", "SPY"} else -0.055))
            market_value = None if item["market_value"] is None else float(item["market_value"]) * (close / float(item["close"]))
            records.append(
                HistoryPoint(
                    date=day.isoformat(),
                    symbol=symbol,
                    close=round(close, 4),
                    score=max(min(score, 100), 0),
                    stance="constructive" if score >= 60 else "defensive",
                    risk_level=str(item["risk"]),
                    currency=str(item["currency"]),
                    change_pct=0.004 - progress * 0.006,
                    market_value=round(market_value, 4) if market_value is not None else None,
                    day_pnl=round((market_value or 0) * (0.004 - progress * 0.006), 4) if market_value is not None else None,
                    unrealized_pnl=round((market_value or 0) - (1850 if symbol == "AAPL" else 840 if symbol == "NVDA" else 1500), 4) if market_value is not None else None,
                    unrealized_pnl_pct=0.1 - progress * 0.18 if market_value is not None else None,
                    benchmark_symbol=str(item["benchmark"]),
                    relative_return_20d=round(float(item["rel20"]) + progress * (0.046 if symbol == "AAPL" else -0.036), 6),
                    relative_return_60d=round(float(item["rel20"]) + progress * (0.032 if symbol == "AAPL" else -0.06), 6),
                    latest_data_date=day.isoformat(),
                    data_age_days=0,
                    freshness_status="fresh",
                )
            )
    by_key = {(record.symbol, record.date): record for record in records}
    for record in latest:
        by_key[(record.symbol, record.date)] = record
    return sorted(by_key.values(), key=lambda item: (item.date, item.symbol))


def _write_history(path: Path, history: list[HistoryPoint]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in history:
            handle.write(json.dumps(record.model_dump(), ensure_ascii=False, sort_keys=True) + "\n")


def _demo_insights(analyses: list[AssetAnalysis]):
    from .insights import build_insights

    return build_insights([item.model_copy(update={"news": []}) for item in analyses])


def _write_demo_config(path: Path) -> None:
    path.write_text(
        """title: "AI Market Pulse Demo"
timezone: "America/Los_Angeles"

analysis:
  language: "zh-CN"
  lookback_days: 220
  min_history_rows: 45

data:
  providers: ["yfinance"]

benchmarks:
  enabled: true
  symbols: ["SPY", "QQQ", "000300.SS", "^HSI"]
  default_by_market:
    US: "SPY"
    CN: "000300.SS"
    HK: "^HSI"
  compare:
    AAPL: "QQQ"
    NVDA: "QQQ"
    MSFT: "QQQ"

assets:
  - symbol: "AAPL"
    name: "Apple"
    market: "US"
    quantity: 10
    cost_basis: 185
  - symbol: "NVDA"
    name: "NVIDIA"
    market: "US"
    quantity: 6
    cost_basis: 140
  - symbol: "600519.SS"
    name: "Kweichow Moutai"
    market: "CN"
    quantity: 1
    cost_basis: 1500
""",
        encoding="utf-8",
    )

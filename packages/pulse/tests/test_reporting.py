from __future__ import annotations

import re
from datetime import datetime

from ai_market_pulse.models import (
    Asset,
    AssetAnalysis,
    AttentionItem,
    BenchmarkComparison,
    DailyReport,
    InsightSummary,
    NewsItem,
    PriceSnapshot,
    SignalScore,
)
from ai_market_pulse.reporting import render_html, render_markdown


def test_render_markdown_includes_ai_portfolio_brief_and_source() -> None:
    report = DailyReport(
        title="Pulse",
        generated_at=datetime(2026, 7, 8, 8, 0),
        timezone="UTC",
        language="en-US",
        market_brief="brief",
        portfolio_ai_summary="Portfolio state: calm.",
        analyses=[
            AssetAnalysis(
                asset=Asset(symbol="AAA"),
                snapshot=PriceSnapshot(
                    symbol="AAA",
                    name="AAA Corp",
                    currency="USD",
                    last_close=10,
                    previous_close=9,
                    change_pct=0.111111,
                    start_date="2026-01-01",
                    end_date="2026-07-08",
                    rows=2,
                    source="unit",
                ),
                metrics={"return_20d": 0.1, "return_60d": 0.2, "rsi14": 55, "atr_pct": 0.02},
                signal=SignalScore(score=70, stance="watch bullish", risk_level="low", reasons=["reason"]),
                news=[NewsItem(title="news", link="https://example.com")],
            )
        ],
    )

    markdown = render_markdown(report)

    assert "## AI Portfolio Brief" in markdown
    assert "Portfolio state: calm." in markdown
    assert "unit" in markdown


def test_render_html_includes_language_switch() -> None:
    report = DailyReport(
        title="Pulse",
        generated_at=datetime(2026, 7, 8, 8, 0),
        timezone="UTC",
        language="zh-CN",
        market_brief="brief",
        analyses=[
            AssetAnalysis(
                asset=Asset(symbol="AAA"),
                snapshot=PriceSnapshot(
                    symbol="AAA",
                    name="AAA Corp",
                    currency="USD",
                    last_close=10,
                    previous_close=9,
                    change_pct=0.111111,
                    start_date="2026-01-01",
                    end_date="2026-07-08",
                    rows=2,
                    source="unit",
                ),
                metrics={"return_20d": 0.1, "rsi14": 55},
                signal=SignalScore(score=70, stance="watch bullish", risk_level="low", reasons=["reason"]),
                news=[],
            )
        ],
    )

    html = render_html(report)

    assert 'data-lang="zh"' in html
    assert "data-lang-choice" in html
    assert "每日量化研究简报" in html


def _analysis_with_news(news: list[NewsItem]) -> AssetAnalysis:
    return AssetAnalysis(
        asset=Asset(symbol="AAA"),
        snapshot=PriceSnapshot(
            symbol="AAA",
            name="AAA Corp",
            currency="USD",
            last_close=10,
            previous_close=9,
            change_pct=0.111111,
            start_date="2026-01-01",
            end_date="2026-07-08",
            rows=2,
            source="unit",
        ),
        metrics={"return_20d": 0.1, "rsi14": 55},
        signal=SignalScore(score=70, stance="watch bullish", risk_level="low", reasons=["reason"]),
        news=news,
    )


def test_malicious_news_link_is_neutralized_in_html_and_markdown() -> None:
    report = DailyReport(
        title="Pulse",
        generated_at=datetime(2026, 7, 8, 8, 0),
        timezone="UTC",
        language="en-US",
        market_brief="brief",
        analyses=[
            _analysis_with_news(
                [
                    NewsItem(title="Bad news", link="javascript:alert(1)"),
                    NewsItem(title="Good news", link="https://example.com/story"),
                ]
            )
        ],
    )

    html = render_html(report)
    markdown = render_markdown(report)

    assert 'href="javascript:alert(1)"' not in html
    assert 'href="#"' in html
    assert "javascript:" not in markdown
    assert 'href="https://example.com/story"' in html
    assert "(https://example.com/story)" in markdown


def test_news_link_that_could_escape_markdown_parens_is_neutralized() -> None:
    report = DailyReport(
        title="Pulse",
        generated_at=datetime(2026, 7, 8, 8, 0),
        timezone="UTC",
        language="en-US",
        market_brief="brief",
        analyses=[
            _analysis_with_news(
                [NewsItem(title="Bad news", link="https://good.test/) [x](javascript:alert(1))")]
            )
        ],
    )

    markdown = render_markdown(report)
    html = render_html(report)

    assert "javascript:" not in markdown
    assert "javascript:" not in html
    assert "(#)" in markdown


def test_insight_reason_with_pipe_does_not_corrupt_markdown_table() -> None:
    report = DailyReport(
        title="Pulse",
        generated_at=datetime(2026, 7, 8, 8, 0),
        timezone="UTC",
        language="en-US",
        market_brief="brief",
        analyses=[],
        insights=InsightSummary(
            attention=[
                AttentionItem(
                    symbol="AAA",
                    priority=1,
                    reason="RSI | overbought",
                    has_position=False,
                    risk_level="high",
                )
            ]
        ),
    )

    markdown = render_markdown(report)

    attention_row = next(line for line in markdown.splitlines() if line.startswith("| AAA "))
    cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", attention_row.strip("|"))]
    assert len(cells) == 6
    assert cells == ["AAA", "1", "high", "RSI \\| overbought", "n/a", "n/a"]


def _analysis(
    symbol: str,
    score: int,
    change_pct: float,
    risk_level: str = "low",
    verdict: str | None = None,
) -> AssetAnalysis:
    benchmark = (
        BenchmarkComparison(
            symbol="SPY",
            name="S&P 500",
            market="US",
            source="unit",
            latest_date="2026-07-08",
            asset_return_20d=0.05,
            benchmark_return_20d=0.02,
            relative_return_20d=0.03,
            asset_return_60d=0.1,
            benchmark_return_60d=0.04,
            relative_return_60d=0.06,
            verdict=verdict,
        )
        if verdict
        else None
    )
    return AssetAnalysis(
        asset=Asset(symbol=symbol),
        snapshot=PriceSnapshot(
            symbol=symbol,
            name=f"{symbol} Corp",
            currency="USD",
            last_close=10,
            previous_close=9,
            change_pct=change_pct,
            start_date="2026-01-01",
            end_date="2026-07-08",
            rows=2,
            source="unit",
        ),
        metrics={"return_20d": 0.1, "rsi14": 55},
        signal=SignalScore(score=score, stance="watch bullish", risk_level=risk_level, reasons=["reason"]),
        news=[],
        benchmark=benchmark,
    )


def test_render_html_summary_strip_shows_score_change_verdict_and_risk() -> None:
    report = DailyReport(
        title="Pulse",
        generated_at=datetime(2026, 7, 8, 8, 0),
        timezone="UTC",
        language="en-US",
        market_brief="brief",
        analyses=[
            _analysis("AAA", score=85, change_pct=0.05, risk_level="low", verdict="outperforming"),
            _analysis("BBB", score=55, change_pct=-0.02, risk_level="medium", verdict="underperforming"),
            _analysis("CCC", score=25, change_pct=0.0, risk_level="high", verdict="mixed"),
        ],
    )

    html = render_html(report)

    for symbol in ("AAA", "BBB", "CCC"):
        strip_row = re.search(
            rf'<div class="strip-row"[^>]*data-symbol="{symbol}"[^>]*>.*?</div>\s*(?=<div class="strip-row"|</section>)',
            html,
            re.DOTALL,
        )
        assert strip_row, f"expected strip-row for {symbol}"

    assert html.count('class="strip-row"') == 3
    assert "outperforming" in html
    assert "underperforming" in html
    assert "mixed" in html

    aaa_row = re.search(r'<div class="strip-row"[^>]*data-symbol="AAA".*?</div>\s*(?=<div class="strip-row"|</section>)', html, re.DOTALL).group(0)
    bbb_row = re.search(r'<div class="strip-row"[^>]*data-symbol="BBB".*?</div>\s*(?=<div class="strip-row"|</section>)', html, re.DOTALL).group(0)
    ccc_row = re.search(r'<div class="strip-row"[^>]*data-symbol="CCC".*?</div>\s*(?=<div class="strip-row"|</section>)', html, re.DOTALL).group(0)

    assert 'class="score gain"' in aaa_row or 'gain">85' in aaa_row
    assert 'risk-medium">55' in bbb_row
    assert 'loss">25' in ccc_row


def test_insight_reason_with_newline_does_not_split_markdown_row() -> None:
    report = DailyReport(
        title="Pulse",
        generated_at=datetime(2026, 7, 8, 8, 0),
        timezone="UTC",
        language="en-US",
        market_brief="brief",
        analyses=[],
        insights=InsightSummary(
            attention=[
                AttentionItem(
                    symbol="BBB",
                    priority=1,
                    reason="line one\nline two",
                    has_position=False,
                    risk_level="high",
                )
            ]
        ),
    )

    markdown = render_markdown(report)

    matching_lines = [line for line in markdown.splitlines() if line.startswith("| BBB ")]
    assert len(matching_lines) == 1
    assert "line one line two" in matching_lines[0]

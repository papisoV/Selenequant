from __future__ import annotations

from pathlib import Path

from ai_market_pulse.site import build_site, render_site_index


def test_build_site_copies_dashboard_and_reports(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "dashboard.html").write_text("<h1>Dashboard</h1>", encoding="utf-8")
    (reports / "market-pulse-20260707-1200.html").write_text("<h1>Report 1</h1>", encoding="utf-8")
    (reports / "market-pulse-20260708-1200.html").write_text("<h1>Report 2</h1>", encoding="utf-8")

    result = build_site(reports, tmp_path / "site", title="Test Pulse")

    assert result.index_path.exists()
    assert result.dashboard_path is not None
    assert result.dashboard_path.exists()
    assert len(result.reports) == 2
    assert result.reports[0].title == "2026-07-08 12:00"
    assert (tmp_path / "site" / "reports" / "market-pulse-20260708-1200.html").exists()
    assert "Test Pulse" in result.index_path.read_text(encoding="utf-8")


def test_render_site_index_handles_empty_reports() -> None:
    html = render_site_index("Empty Pulse", reports=[], dashboard_path=None)

    assert "Empty Pulse" in html
    assert "No reports found" in html
    assert "Dashboard" in html
    assert "data-lang-choice" in html
    assert "量化研究站点" in html

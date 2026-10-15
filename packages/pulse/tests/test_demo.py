from __future__ import annotations

from pathlib import Path

from ai_market_pulse.demo import build_demo


def test_build_demo_generates_complete_static_product(tmp_path: Path) -> None:
    result = build_demo(tmp_path / "demo")

    assert result.history_path.exists()
    assert result.dashboard_path.exists()
    assert result.site.index_path.exists()
    assert result.report_paths["html"].exists()

    report_html = result.report_paths["html"].read_text(encoding="utf-8")
    dashboard_html = result.dashboard_path.read_text(encoding="utf-8")
    site_html = result.site.index_path.read_text(encoding="utf-8")

    assert "Benchmark Context" in report_html
    assert "Relative strength" in report_html
    assert "data-lang-choice" in report_html
    assert "Rel 20D" in dashboard_html
    assert "AI Market Pulse Demo" in site_html

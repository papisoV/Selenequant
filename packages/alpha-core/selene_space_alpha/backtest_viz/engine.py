"""VizEngine — entry point that bundles all backtest_viz outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from selene_space_alpha.backtest_viz.report import generate_report


class VizEngine:
    """Run all visualizations for a mining run and return the HTML report path.

    Generates ``training_curve.png``, ``formula_tree.png`` and ``report.html``
    inside ``output_dir``. Returns the path to ``report.html``.
    """

    def run(
        self,
        history: dict,
        formula_tokens: list[int],
        vocab: Any,
        output_dir: Path,
    ) -> Path:
        """Generate the full report bundle and return the HTML path."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        return generate_report(history, formula_tokens, vocab, output_dir)

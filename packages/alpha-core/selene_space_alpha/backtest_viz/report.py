"""HTML report generator — embeds chart + tree as base64 PNGs in one HTML file."""

from __future__ import annotations

import base64
import html
from pathlib import Path
from typing import Any

from selene_space_alpha.backtest_viz.chart import (
    plot_formula_tree,
    plot_training_curve,
)


def generate_report(
    history: dict,
    formula_tokens: list[int],
    vocab: Any,
    output_dir: Path,
) -> Path:
    """Generate training_curve.png + formula_tree.png + report.html in output_dir.

    Returns the path to ``report.html``.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    curve_path = output_dir / "training_curve.png"
    tree_path = output_dir / "formula_tree.png"
    report_path = output_dir / "report.html"

    plot_training_curve(history, curve_path)
    plot_formula_tree(formula_tokens, vocab, tree_path)

    curve_b64 = base64.b64encode(curve_path.read_bytes()).decode("ascii")
    tree_b64 = base64.b64encode(tree_path.read_bytes()).decode("ascii")

    decoded = vocab.decode(list(formula_tokens)) if hasattr(vocab, "decode") else ""
    formula_text = html.escape(decoded)

    steps = list(history.get("step", []))
    best_vals = list(history.get("best_reward", []))
    mean_vals = list(history.get("mean_reward", []))
    summary_cells = "".join(
        f"<tr><td>{s}</td><td>{b:.6f}</td><td>{m:.6f}</td></tr>"
        for s, b, m in zip(steps, best_vals, mean_vals)
    )

    document = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Backtest Report</title>
<style>
  body {{ font-family: sans-serif; margin: 2rem; }}
  h1, h2 {{ color: #222; }}
  img {{ max-width: 100%; border: 1px solid #ccc; margin: 1rem 0; }}
  table {{ border-collapse: collapse; margin: 1rem 0; }}
  th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.8rem; }}
  th {{ background: #f0f0f0; }}
  pre {{ background: #f6f8fa; padding: 0.8rem; overflow-x: auto; }}
</style>
</head>
<body>
<h1>Backtest Report</h1>

<h2>Training Curve</h2>
<img src="data:image/png;base64,{curve_b64}" alt="training curve">

<h2>Formula Tree</h2>
<img src="data:image/png;base64,{tree_b64}" alt="formula tree">

<h2>Formula Tokens</h2>
<pre>{formula_text}</pre>

<h2>History</h2>
<table>
<thead><tr><th>step</th><th>best_reward</th><th>mean_reward</th></tr></thead>
<tbody>
{summary_cells}
</tbody>
</table>
</body>
</html>
"""

    report_path.write_text(document, encoding="utf-8")
    return report_path

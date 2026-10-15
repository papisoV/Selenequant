# backtest_viz — Backtest Visualization (Clean-Room)

> Clean-room implementation. No code referenced from any AGPL-licensed project.
> Built from standard industry libraries (matplotlib, networkx) using public API
> documentation and standard plotting patterns.

## Purpose

Visualization utilities for alpha mining / backtest results:

1. **Training curves** — plot step vs best_reward / mean_reward over a mining run.
2. **Formula tree** — render a postfix token sequence as a binary expression tree.
3. **HTML report** — bundle the chart and tree into a single self-contained HTML file.

## Public API

### `chart.plot_training_curve(history, output_path) -> None`

- **Signature:** `plot_training_curve(history: dict, output_path: Path) -> None`
- **Input shape:**
  - `history` is a dict with keys:
    - `"step"`: `list[int]` — step indices, length `S`.
    - `"best_reward"`: `list[float]` — best reward seen so far, length `S`.
    - `"mean_reward"`: `list[float]` — mean reward across the batch, length `S`.
  - `output_path`: `Path` to a `.png` file (parent dir must exist).
- **Output:** Writes a PNG file at `output_path`. Returns `None`.
- **Exception behavior:**
  - Raises `KeyError` if required keys are missing.
  - Raises `ValueError` if lists have mismatched lengths.
  - Matplotlib I/O errors propagate.

### `chart.plot_formula_tree(formula_tokens, vocab, output_path) -> None`

- **Signature:** `plot_formula_tree(formula_tokens: list[int], vocab, output_path: Path) -> None`
- **Input shape:**
  - `formula_tokens`: postfix token list (ints). Operators consume arity operands
    off the top of the stack; features are leaves.
  - `vocab`: any object exposing `token_to_name(token: int) -> str` (the
    `FactorVocabulary` from `selene_space_alpha.vocab` satisfies this).
  - `output_path`: `Path` to a `.png` file.
- **Output:** Writes a PNG file at `output_path`. Returns `None`.
- **Exception behavior:**
  - Raises `ValueError` if the token sequence is not a valid postfix expression
    (stack underflow / leftover operands).
  - Networkx/matplotlib I/O errors propagate.

### `engine.VizEngine`

- **Class:** `VizEngine`
- **Method:** `run(history: dict, formula_tokens: list[int], vocab, output_dir: Path) -> Path`
- **Behavior:**
  - Creates `output_dir` if it does not exist.
  - Generates `training_curve.png` and `formula_tree.png` inside `output_dir`.
  - Generates `report.html` bundling both images.
  - Returns the `Path` to `report.html`.
- **Exception behavior:** Propagates errors from `chart` / `report` modules.

### `report.generate_report(history, formula_tokens, vocab, output_dir) -> Path`

- **Signature:** `generate_report(history: dict, formula_tokens: list[int], vocab, output_dir: Path) -> Path`
- **Behavior:**
  - Generates `training_curve.png` and `formula_tree.png` in `output_dir`.
  - Embeds both as base64 PNGs in a single `report.html` file.
  - Returns the `Path` to `report.html`.
- **Exception behavior:** Propagates errors from `chart` module.

## Dependencies

- `matplotlib` (industry-standard plotting library, BSD-licensed)
- `networkx` (graph library, BSD-licensed)
- Python 3.10+ standard library (`pathlib`, `base64`, `html`)

## Clean-Room Note

This package was designed and implemented from scratch using only the public
APIs of matplotlib and networkx. No source code from any AGPL-licensed project
was consulted. AST similarity to any third-party backtest visualization package
is coincidental and arises from standard library calling patterns (matplotlib
`subplots` / `savefig`, networkx `draw_networkx`) rather than implementation logic.

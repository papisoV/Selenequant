"""Loader for the pinned PA statistics JSON (results/pa_stats_*.json).

Turns the committed experiment output into a global-rules line with a
calibration preamble. Three states:

- committed : JSON exists, parses, has an `experiments` key -> inject
  calibration wording + the survival/MM baseline lines
- missing   : no JSON file -> static fallback line (no numbers)
- corrupt   : JSON unparseable or missing `experiments` -> fallback +
  a `corrupt` source marker so the run context can record the degradation

The calibration wording is the load-bearing guard (design review §1):
unconditioned historical frequencies must never be read as conditional
probabilities for the current bar. Sample size and CI travel with each
statistic block (there is no single global N).

STATS_VERSION bump rule (pinned in pa_stats_experiments.py): any change
that rewrites next_period_reserve from a calibration placeholder to real
backfilled numbers MUST bump STATS_VERSION (e.g. 2026-09-10v1 -> v2) —
prompt lines derived from the JSON embed the version string, so a silent
refill would mix calibrations across runs. Pure recomputes of the same
experiments on new data may keep the version; schema/semantic changes
(including the reserve refill) require the bump. See PROVENANCE §13.

Package-home note: this module moved from scripts/pa_research/ into the
decision-workflow package so the local-api service and the CLI share one
implementation. scripts/pa_research/pa_stats_loader.py re-exports it for
backward compatibility (tests + run_pa_diagnosis.py import path).
"""

from __future__ import annotations

import json
from pathlib import Path

CALIBRATION_SENTENCE = (
    "以下历史实证基率为无条件概率;"
    "当前诊断必须结合本窗口结构条件化使用,"
    "禁止将基率当作当前条件概率"
)


def _fmt_rate(blk: dict) -> str:
    rate = blk.get("rate")
    ci = blk.get("ci95") or [None, None]
    n = blk.get("total", 0)
    if rate is None:
        return "无样本"
    return f"命中率{rate:.1%}(n={n},CI95 {ci[0]:.2f}..{ci[1]:.2f})"


def load_pa_stats(
    results_dir: Path,
    symbol: str = "BTC/USDT",
    market_type: str = "futures",
    interval: str = "1h",
) -> tuple[str | None, dict]:
    """Read the pinned stats JSON.

    Returns (global_rules_line, meta). The line is None when unusable
    (missing/corrupt); meta describes the state for run context.
    """
    tag = f"{symbol.replace('/', '')}_{market_type}_{interval}"
    json_path = results_dir / f"pa_stats_{tag}.json"
    meta: dict = {"source": "missing", "version": None, "path": str(json_path)}

    if not json_path.is_file():
        return None, meta
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        experiments = data["experiments"]
        version = data["version"]
    except (json.JSONDecodeError, KeyError, OSError, UnicodeDecodeError):
        meta["source"] = "corrupt"
        return None, meta

    meta["source"] = "committed"
    meta["version"] = version

    surv3 = experiments["swing_breakout"]["survival"].get("window_3", {})
    mm = experiments["measured_move"]["hits"]
    spikes = experiments["spike_reversal"]
    de = experiments["direction_edge"]
    # Champion row (the shipped formula's own calls) is primary. Older JSON
    # predates the block — fall back to the naive row so the line never
    # silently drops the direction figure.
    dch = experiments.get("direction_edge_champion")

    degraded = experiments.get("_degraded", False)
    degraded_note = "(样本不足,仅作参考)" if degraded else ""
    if dch is not None:
        direction_fig = (
            f"上线方向公式1根K线优势(费后,即 compute_direction_signal)"
            f"{dch.get('edge_after_cost', 0):.4f}"
            f"(朴素last-bar基线 {de.get('edge_after_cost', 0):.4f} 供对照)"
        )
    else:
        direction_fig = (
            f"朴素1根K线方向预测优势(费后){de.get('edge_after_cost', 0):.4f}"
        )

    line = (
        f"{CALIBRATION_SENTENCE}。"
        f"本品种{market_type} {interval}历史统计({version}{degraded_note}):"
        f"突破后3根K线存活 {_fmt_rate(surv3)};"
        f"突破后8根K线MM投影命中 {_fmt_rate(mm)};"
        f"尖峰后8根K线反向1ATR {spikes.get('reversal', {}).get('rate', 0):.1%}"
        f"(n={spikes.get('reversal', {}).get('total', 0)});"
        f"{direction_fig}。"
        "这些数字描述历史频率,不是对当前K线的预测。"
    )
    return line, meta


def stats_rule_or_fallback(
    results_dir: Path,
    symbol: str = "BTC/USDT",
    market_type: str = "futures",
    interval: str = "1h",
) -> tuple[str, dict]:
    """Loader + static fallback for missing/corrupt states. The calibration
    sentence appears verbatim in every state; fallback carries no numbers."""
    line, meta = load_pa_stats(results_dir, symbol, market_type, interval)
    if line is None:
        fallback = (
            f"{CALIBRATION_SENTENCE}。(committed 统计不可用,本次运行不携带基率数值)"
        )
        return fallback, meta
    return line, meta

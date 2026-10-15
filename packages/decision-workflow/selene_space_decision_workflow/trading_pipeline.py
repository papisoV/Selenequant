"""Shared trading-pipeline assembly for the PA diagnosis CLI and the
local-api decision-workflow service.

Extracted verbatim from scripts/run_pa_diagnosis.py so that CLI and
service produce the same record for the same input. The package stays
LLM-provider-agnostic (no llm-adapter import) — the caller builds the
provider and passes it in (llm_compat.AsyncBackendToSyncAdapter over
OpenAICompatProvider in both frontends).

Cleanroom: all logic below is selene-authored (run_pa_diagnosis lineage);
no PA Agent source was read. Al Brooks public-methodology parameters are
selene-chosen (see PROVENANCE §9/§11).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from .experience import load_experiences
from .multi_scale import four_windows
from .overlays import detect_overlays
from .orchestrator import TwoStageWorkflow
from .parser import parse_decision_tree
from .pa_stats_loader import stats_rule_or_fallback
from .program_nodes import ProgramNodeRegistry
from .routing import TRADING_ROUTING_TABLE
from .validator import ValidatorConfig

# Decision-tree package data + pinned stats results dir.
# PKG_DIR = <repo>/packages/decision-workflow/selene_space_decision_workflow
# parents[2] = <repo>; the CLI reads the same <repo>/results directory.
PKG_DIR = Path(__file__).resolve().parent
TRADING_TREE_PATH = PKG_DIR / "decision_trees" / "trading_decision.txt"
RESULTS_DIR = PKG_DIR.parents[2] / "results"

# Anchored to branch-prefixed lines only. Matches both "是：终止 → X" and
# "否：终止 → X" style lines while avoiding false matches in comments or
# free-text questions. Verified equivalent to the CLI's earlier looser
# pattern on trading_decision.txt (both extract {trade, wait, reject}).
TERMINAL_RE = re.compile(
    r"^[是否中性是\[否\]][：:]\s*终止\s*→\s*(\S+)",
    re.M,
)

# Projection keys for experience cases injected into input_data (P3).
_EXP_METRICS = ("total_return", "sharpe_ratio", "total_trades")

# four_windows raises below 50 bars; the CLI default of 120 keeps this met.
MIN_PRIMARY_BARS = 50


def load_bars(
    db_path: Path,
    symbol: str,
    interval: str,
    bars: int,
    market_type: str = "futures",
    *,
    min_bars: int = 50,
    after_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Read the last `bars` klines from a klines.db (read-only connection).

    Returns oldest-first bar dicts. Raises RuntimeError when the series is
    thinner than min_bars (primary-window contract); min_bars=0 disables the
    check so an optional HTF series degrades instead of aborting.

    ``after_ms`` switches the read direction: instead of the N bars *ending*
    at the newest bar, it returns the N bars *starting after* the anchor
    (``timestamp >= after_ms``, ascending). This is the forward-looking read
    the counterfactual scorer needs — ``judge_outcome`` consumes bars
    strictly after the decision, and a window ending at the decision holds
    zero of them. Default None keeps the historical read byte-identical.
    """
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if after_ms is not None:
            rows = db.execute(
                """
                SELECT timestamp, open, high, low, close, volume
                FROM klines
                WHERE symbol = ? AND interval = ? AND exchange = 'binance'
                  AND market_type = ? AND timestamp >= ?
                ORDER BY timestamp ASC LIMIT ?
                """,
                (symbol, interval, market_type, after_ms, bars),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT timestamp, open, high, low, close, volume
                FROM klines
                WHERE symbol = ? AND interval = ? AND exchange = 'binance'
                  AND market_type = ?
                ORDER BY timestamp DESC LIMIT ?
                """,
                (symbol, interval, market_type, bars),
            ).fetchall()
    finally:
        db.close()
    if len(rows) < min_bars:
        raise RuntimeError(f"insufficient bars in {db_path}: {len(rows)}")
    if after_ms is None:
        # DESC read needs flipping to oldest-first; the forward branch already
        # reads ASC, which IS oldest-first.
        rows.reverse()
    return [
        {
            "ts": ts,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
        }
        for ts, open_, high, low, close, vol in rows
    ]


def _atr_and_range(
    highs: list[float], lows: list[float], closes: list[float]
) -> tuple[float, float, float]:
    """Shared TR/ATR14 + 20-bar range block for bar_summary / htf_summary."""
    trs = [
        max(high - low, abs(high - pc), abs(low - pc))
        for high, low, pc in zip(highs[1:], lows[1:], closes[:-1])
    ]
    atr = sum(trs[-14:]) / min(14, len(trs)) if trs else 0.0
    return atr, max(highs[-20:]), min(lows[-20:])


def bar_summary(
    bars: list[dict[str, Any]], symbol: str = "BTC/USDT", interval: str = "1h"
) -> dict[str, Any]:
    """Deterministic bar aggregation the LLM reasons over (no fabricated data).

    Passes the full window as a compact array — the tree's data-integrity
    gate (§1.1/1.2) is only verifiable if the model can see every bar.
    K1..KN oldest-first: the K-numbering the trace's bar_range cites.
    """
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    atr, range_high_20, range_low_20 = _atr_and_range(highs, lows, closes)
    last = bars[-1]
    return {
        "symbol": symbol,
        "interval": interval,
        "bars_total": len(bars),
        "window_start_iso": bars[0]["ts"],
        "window_end_iso": last["ts"],
        "last_close": last["close"],
        "atr14": round(atr, 2),
        "bars": [
            {
                "k": i + 1,
                "o": b["open"],
                "h": b["high"],
                "l": b["low"],
                "c": b["close"],
                "v": b["volume"],
            }
            for i, b in enumerate(bars)
        ],
        "range_high_20": max(highs[-20:]),
        "range_low_20": min(lows[-20:]),
    }


def htf_summary(
    bars: list[dict[str, Any]], symbol: str, interval: str
) -> dict[str, Any]:
    """Aggregate higher-timeframe context (B2): structure, not every bar.

    Deliberately compact — the Stage 2 decision needs the HTF regime anchor
    (last close vs 20-bar range, ATR), not bar-by-bar data it can't cite.
    """
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    atr, range_high_20, range_low_20 = _atr_and_range(highs, lows, closes)
    return {
        "symbol": symbol,
        "interval": interval,
        "bars_total": len(bars),
        "window_start_iso": bars[0]["ts"],
        "window_end_iso": bars[-1]["ts"],
        "last_close": bars[-1]["close"],
        "atr14": round(atr, 2),
        "range_high_20": range_high_20,
        "range_low_20": range_low_20,
    }


def incremental_input(
    prior_record: dict[str, Any],
    all_bars: list[dict[str, Any]],
    symbol: str,
    interval: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    """Build an incremental diagnosis input: prior Stage1 + bars arrived since.

    K-numbering continues the global sequence: the prior window ended at
    K<prior_bars_total>, so new bars start at K<prior_bars_total+1>. The tree's
    bar_range validator sees one continuous window, no re-anchoring.
    """
    prior_summary = prior_record["input_data"]["summary"]
    prior_end = prior_summary["window_end_iso"]
    prior_total = int(prior_summary.get("bars_total", 0))
    new_bars = [b for b in all_bars if b["ts"] > prior_end]
    if not new_bars:
        raise RuntimeError(f"no new bars after {prior_end} — nothing incremental")
    prior_diag = prior_record["stage1"]["diagnosis_or_decision"]
    prior_stage2 = (prior_record.get("stage2") or {}).get("diagnosis_or_decision")
    summary = {
        "symbol": symbol,
        "interval": interval,
        "mode": "incremental",
        "prior_bars_total": prior_total,
        "window_end_iso": new_bars[-1]["ts"],
        "new_bars_count": len(new_bars),
        "prior_diagnosis": prior_diag,
        "bars": [
            {
                "k": prior_total + i + 1,
                "o": b["open"],
                "h": b["high"],
                "l": b["low"],
                "c": b["close"],
                "v": b["volume"],
            }
            for i, b in enumerate(new_bars)
        ],
    }
    last_decision = prior_stage2 if prior_stage2 else None
    input_data = {
        "symbol": symbol,
        "interval": interval,
        "summary": summary,
        "prior_window": {
            "end_iso": prior_end,
            "bars_total": prior_total,
        },
    }
    return input_data, new_bars, last_decision


def build_global_rules(
    symbol: str,
    interval: str,
    results_dir: Path | None = None,
) -> tuple[list[str], dict]:
    """Global rules shared by CLI and service (verbatim from the CLI).

    Appends the A2 empirical baseline line (pinned stats JSON with the
    calibration wording; missing/corrupt degrades to a static no-numbers
    line, never an abort). Returns (rules, pa_stats_meta).
    """
    global_rules = [
        "所有诊断结论必须基于提供的K线数据,禁止编造数字",
        (
            "输入含 experiences(历史经验)时,将其作为同品种同周期的先例参考:"
            "只引用实际给出的案例(verdict 与 IS/OOS 数值),禁止编造不存在的案例数或数值。"
        ),
        (
            "输出契约:只输出一个JSON对象,不要markdown代码块或解释文字。"
            "trace 必须是对象数组,每个元素形如 "
            '{"node_id":"1.1","question":"<树中该节点的问题原文>","answer":"<是/否/是[...]>",'
            '"bar_range":"K1-K120","reason":"<引用具体K线编号与数值的结构化理由>",'
            '"branch":"<所走分支标签>","program_prefill_nodes":[]}。'
            "bar_range 格式必须为 K<起始>-K<结束> (如 K1-K120),K编号从1开始对应输入数组第一根K线;"
            "每个 trace 元素的 bar_range 必须覆盖该节点判断实际查看的K线窗口,"
            "且 reason 中引用的每一个K编号都必须落在该元素的 bar_range 之内。"
            "若 reason 提到整体结构,bar_range 就写整个已见窗口 K1-K<N>(N=K线总数),不要只写最近几根;"
            "state 取值: trend_up / trend_down / range / breakout / unknown;"
            "gate_result 取值: proceed / wait / unknown;"
            "diagnosis_summary 必须包含 state 字段。"
            "Stage 2 输出契约: decision / terminal_outcome(trade|wait|reject|proceed) / order / "
            "trace(同上对象数组) / decision_summary(必须原样携带 Stage1 的 diagnosis_summary;"
            "terminal_outcome=trade 时 order 必须非空,decision=reject 时 decision_summary 必须含 reject_reason)。"
            "decision_summary 可选携带 next_period 对象 {direction: long|short|neutral, "
            "watch_levels: 数值数组, invalidate_if: 字符串},"
            "direction 是对下一根K线的方向判断,watch_levels 是值得观察的价位,"
            "invalidate_if 写明何种走势会推翻本次判断;"
            "禁止在 next_period 中给出概率数字或把历史基率当作本窗口的条件概率。"
            "decision_summary 可选携带 confidence 数值(0-1 浮点)表示本次判断的置信度,"
            "confidence 必须是 decision_summary 的顶层键,"
            "禁止嵌入 diagnosis_summary 或其他子对象,"
            "也不要作为 Stage 2 JSON 的顶层键输出(会被丢弃)。"
        ),
    ]
    pa_stats_line, pa_stats_meta = stats_rule_or_fallback(
        results_dir if results_dir is not None else RESULTS_DIR,
        symbol,
        "futures",
        interval,
    )
    global_rules.append(pa_stats_line)
    return global_rules, pa_stats_meta


def project_experiences(
    symbol: str,
    interval: str,
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Load prior REVALIDATE outcomes as recallable precedent cases (P3).

    Empty list when no cases exist (caller omits the key).
    """
    return [
        {
            "id": c.id,
            "verdict": c.verdict,
            "in_sample": {k: c.in_sample[k] for k in _EXP_METRICS if k in c.in_sample},
            "oos": {k: c.oos[k] for k in _EXP_METRICS if k in c.oos},
            "method": c.method,
            **({"market_regime": c.market_regime} if c.market_regime else {}),
        }
        for c in load_experiences(symbol, interval, limit=limit)
    ]


def enrich_input_data(
    input_data: dict[str, Any],
    all_bars: list[dict[str, Any]],
    symbol: str,
    interval: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Append the shared material blocks to input_data (both CLI branches).

    B3 four windows + B4 overlay hits + P3 experiences — the original CLI
    applies these over the full bar list regardless of full/incremental
    mode. Returns (enriched_input_data, overlays_hit); the input dict is
    not mutated.
    """
    enriched = {
        **input_data,
        # B3: four-scale reading over the full window — Stage 2 material
        # context, appended so the multiscale strategy file's k_range
        # citations stay inside the actual window.
        "windows": four_windows(all_bars),
    }
    # B4: deterministic overlay detection on the primary window. Only
    # the pattern hits become prompt material; misses stay absent (the
    # overlay files are injected only when their pattern is present).
    overlays = detect_overlays(all_bars)
    overlays_hit = {k: v for k, v in overlays.items() if v}
    if overlays_hit:
        enriched["overlay_patterns"] = overlays_hit
    # Prior REVALIDATE outcomes as recallable precedent (P3-002).
    # Regime-filter when the caller knows the market state (B1); empty
    # regime on legacy cases keeps them visible under any filter.
    cases = project_experiences(symbol, interval, limit=3)
    if cases:
        enriched["experiences"] = cases
    return enriched, overlays_hit


def build_full_input_data(
    all_bars: list[dict[str, Any]],
    symbol: str,
    interval: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Assemble the full-window input_data (CLI non-incremental branch).

    bar_summary + the shared enrich blocks. Returns (input_data,
    overlays_hit).
    """
    base: dict[str, Any] = {
        "symbol": symbol,
        "interval": interval,
        "summary": bar_summary(all_bars, symbol, interval),
    }
    return enrich_input_data(base, all_bars, symbol, interval)


def build_trading_workflow(
    *,
    llm: Any,
    persistence: Any,
    symbol: str,
    interval: str,
    tree_path: Path | None = None,
    results_dir: Path | None = None,
    trading_stance: str | None = None,
    htf_summary_data: dict[str, Any] | None = None,
    overlay_patterns: dict[str, Any] | None = None,
    max_retry: int = 4,
) -> tuple[TwoStageWorkflow, dict]:
    """Full-parameter trading workflow assembly (CLI parity).

    Parses the trading_decision tree, builds global rules (incl. the A2
    stats line), and returns the workflow plus pa_stats_meta so callers
    can record provenance in mission context.

    htf_summary_data must be pre-aggregated (htf_summary over fetched HTF
    bars) — this builder stays free of klines-db access so it works from
    any input source.
    """
    tree_text = (tree_path or TRADING_TREE_PATH).read_text(encoding="utf-8")
    terminals = frozenset(TERMINAL_RE.findall(tree_text))
    graph = parse_decision_tree(tree_text, allowed_terminals=terminals)
    global_rules, pa_stats_meta = build_global_rules(symbol, interval, results_dir)
    wf = TwoStageWorkflow(
        llm=llm,
        parser_graph=graph,
        validator_config=ValidatorConfig(),
        routing_table=TRADING_ROUTING_TABLE,
        program_registry=ProgramNodeRegistry(),
        persistence=persistence,
        tree_text=tree_text,
        global_rules=global_rules,
        max_retry=max_retry,
        trading_stance=trading_stance,
        htf_summary=htf_summary_data,
        overlay_patterns=overlay_patterns,
    )
    return wf, pa_stats_meta

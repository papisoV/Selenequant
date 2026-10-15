"""Daily market digest — aggregates signal_factors into a human-readable summary.

Reads is_signal=1 records from the last N hours, groups by direction/event_type/
symbols, then calls the active LLM provider (reusing SignalLLMClient) to write
a concise Chinese market summary. Output goes to stdout.

Usage:
    python daily_digest.py                  # last 6 hours
    python daily_digest.py --hours 12       # last 12 hours
    python daily_digest.py --hours 24       # last 24 hours
"""
import argparse
import json
import logging
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

from tgwatcher.signal_llm import LLMConfig, SignalLLMClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("digest")

CONFIG_PATH = Path.cwd() / "config.yaml"
DB_PATH = Path.cwd() / "data" / "tgwatcher.db"

DIGEST_PROMPT_TEMPLATE = """你是一个加密货币市场分析师。基于以下结构化信号数据，写一份简洁的中文市场摘要。

数据时间窗口：过去 {hours} 小时
信号总数：{total}
有效信号（is_signal=1）：{signal_count}

聚合统计：
- 净方向分：{net_direction:+.2f}（-1=全空，0=中性，+1=全多）
- 平均置信度：{avg_confidence:.2f}
- 平均幅度：{avg_magnitude:.2f}

按事件类型分组：
{event_type_breakdown}

按标的分组（top 5）：
{symbols_breakdown}

高置信事件（confidence >= 0.8，按时间倒序）：
{high_confidence_events}

请输出以下格式（纯文本，不要 markdown 代码块）：

【市场方向】
一句话总结净方向 + 驱动因素

【重点事件】
2-4 条最值得关注的事件，每条包含：时间、标的、方向、一句话原因

【风险提示】
1-2 条仍未消化的风险（halflife_min 较长的利空事件）

【一句话展望】
对接下来 {hours} 小时的简短判断
"""


def load_llm_config() -> LLMConfig:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return LLMConfig.from_dict(cfg.get("signal", {}).get("llm", {}))


def fetch_signals(hours: int) -> list[dict]:
    """Fetch is_signal=1 records from the last N hours (by message time, not
    processing time — so batch-processed old messages don't contaminate the
    window)."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(
        """
        SELECT sf.message_id, sf.direction, sf.magnitude, sf.urgency,
               sf.confidence, sf.halflife_min, sf.symbols, sf.event_type,
               sf.reasoning, sf.created_at, m.date AS message_date
        FROM signal_factors sf
        JOIN messages m ON sf.message_id = m.message_id
                       AND sf.chat_id = m.chat_id
        WHERE sf.is_signal = 1 AND sf.llm_status = 'completed'
          AND m.date > datetime('now', ?)
        ORDER BY m.date DESC
        """,
        (f"-{hours} hours",),
    )
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def aggregate(signals: list[dict], hours: int) -> dict:
    """Compute aggregate stats from signal list."""
    if not signals:
        return {"empty": True}

    total = len(signals)
    directions = [s["direction"] for s in signals]
    confidences = [s["confidence"] for s in signals]
    magnitudes = [s["magnitude"] for s in signals]

    net_direction = sum(directions) / total
    avg_conf = sum(confidences) / total
    avg_mag = sum(magnitudes) / total

    # By event_type
    by_type: dict[str, list[dict]] = defaultdict(list)
    for s in signals:
        by_type[s["event_type"]].append(s)

    event_type_lines = []
    for et, items in sorted(by_type.items(), key=lambda x: -len(x[1])):
        avg_dir = sum(i["direction"] for i in items) / len(items)
        event_type_lines.append(
            f"  - {et}: {len(items)} 条, 平均方向 {avg_dir:+.2f}"
        )

    # By symbols
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for s in signals:
        try:
            syms = json.loads(s["symbols"]) if s["symbols"] else ["*"]
        except (json.JSONDecodeError, TypeError):
            syms = ["*"]
        for sym in syms:
            by_symbol[sym].append(s)

    symbol_lines = []
    for sym, items in sorted(by_symbol.items(), key=lambda x: -len(x[1]))[:5]:
        avg_dir = sum(i["direction"] for i in items) / len(items)
        symbol_lines.append(
            f"  - {sym}: {len(items)} 条, 平均方向 {avg_dir:+.2f}"
        )

    # High-confidence events (top 8, confidence >= 0.8)
    high_conf = sorted(
        [s for s in signals if s["confidence"] >= 0.8],
        key=lambda s: s["message_date"],
        reverse=True,
    )[:8]
    high_conf_lines = []
    for s in high_conf:
        time_str = (s["message_date"] or s["created_at"])[5:16].replace("T", " ")  # MM-DD HH:MM
        try:
            syms = json.loads(s["symbols"]) if s["symbols"] else ["*"]
            sym_str = "/".join(syms)
        except (json.JSONDecodeError, TypeError):
            sym_str = "*"
        reasoning = (s["reasoning"] or "")[:100]
        high_conf_lines.append(
            f"  - {time_str} [{sym_str}] dir={s['direction']:+.1f} "
            f"conf={s['confidence']:.2f} ({s['event_type']}): {reasoning}"
        )

    return {
        "empty": False,
        "hours": hours,
        "total": total,
        "signal_count": total,
        "net_direction": net_direction,
        "avg_confidence": avg_conf,
        "avg_magnitude": avg_mag,
        "event_type_breakdown": "\n".join(event_type_lines) or "  (无)",
        "symbols_breakdown": "\n".join(symbol_lines) or "  (无)",
        "high_confidence_events": "\n".join(high_conf_lines) or "  (无)",
    }


def build_digest_prompt(agg: dict) -> str | None:
    if agg.get("empty"):
        return None
    return DIGEST_PROMPT_TEMPLATE.format(**agg)


def main() -> int:
    parser = argparse.ArgumentParser(description="Market digest from signal_factors")
    parser.add_argument("--hours", type=int, default=6, help="Time window in hours")
    args = parser.parse_args()

    print(f"=== Market Digest: last {args.hours} hours ===")
    print(f"DB: {DB_PATH}")
    print()

    signals = fetch_signals(args.hours)
    if not signals:
        print(f"[INFO] No is_signal=1 records in the last {args.hours} hours.")
        return 0

    print(f"Fetched {len(signals)} signals. Aggregating...")

    agg = aggregate(signals, args.hours)
    prompt = build_digest_prompt(agg)
    if prompt is None:
        print("[INFO] Aggregation empty.")
        return 0

    print(f"Net direction: {agg['net_direction']:+.2f}")
    print(f"Avg confidence: {agg['avg_confidence']:.2f}")
    print()

    print("=== Calling LLM for summary ===")
    try:
        llm_config = load_llm_config()
        client = SignalLLMClient(llm_config)
    except ValueError as e:
        print(f"[FAIL] LLM config error: {e}")
        return 2

    # Use single-call _call_llm with a larger token budget — digest output is
    # free-form Chinese prose (not JSON), typically 400-800 tokens.
    # Free-form Chinese prose, not JSON — disable json_mode so provider doesn't
    # force response_format={"type":"json_object"} (causes near-empty output).
    try:
        raw = client._call_llm(prompt, max_tokens_override=1024, json_mode=False)
    except Exception as e:
        print(f"[FAIL] LLM call failed: {e}")
        return 1

    print()
    print("═" * 60)
    print(f"  市场摘要 — 过去 {args.hours} 小时")
    print(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("═" * 60)
    print(raw.strip())
    print("═" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Market digest generation — turns signal_factors rows into a Chinese summary.

Computes a time window (cold start = 36h, otherwise last_digest_at → now, capped
at 168h), aggregates signals, calls LLM with json_mode=False (free-form prose),
persists result to the `digests` table.

Ports tgwatcher/tgwatcher/digest.py (2026-08-10) — imports adapted to the
selene_space_tg_ingest package; codename enforcement via codename_map.

Public API:
    generate_digest(storage, llm, lookback_hours=None) -> DigestResult
    get_latest_digest(storage) -> DigestResult | None
    list_digests(storage, limit) -> list[DigestResult]
"""
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from selene_space_tg_ingest.codename_map import codename_map
from selene_space_tg_ingest.models import Digest
from selene_space_tg_ingest.signal_llm import SignalLLMClient
from selene_space_tg_ingest.storage import Storage
from selene_space_tg_ingest.tz_utils import utc_now, utc_to_local

logger = logging.getLogger(__name__)

COLD_START_HOURS = 36        # first-ever digest: 36h (no need for full 7d)
WINDOW_HOURS = 168           # 7 days — half-life weighting handles aging beyond 36h
MAX_WINDOW_HOURS = 168      # cap for incremental digest (last_digest.to_at → now)
MIN_SIGNALS_FOR_SUMMARY = 5    # below this, skip LLM and return "no change"
MAX_LOOKBACK_HOURS = 720      # 30 days — hard cap for user-requested lookback
AUTO_LOOKBACK_HOURS = 168     # auto-fallback window when incremental has <5 signals
DEFAULT_HALFLIFE_MIN = 60    # fallback when signal's halflife_min is None/0

DIGEST_PROMPT_TEMPLATE = """你是一个加密货币市场分析师。基于以下结构化信号数据，写一份简洁的中文市场摘要。

【合规约束 — 必须严格遵守】
所有币种/标的名称必须使用代号替代（如"标的A"、"标的B"），绝对禁止输出真实币种名称（如BTC、ETH、SOL等）。这是法律合规要求，违反即视为无效输出。

数据时间窗口：{from_at} 到 {to_at}（本地时间，7 天窗口，半衰期加权）
信号总数：{total}
总权重（市场活跃度指标）：{total_weight:.2f}

聚合统计（半衰期加权 — 越新越重要，老消息按 0.5^(age/halflife) 衰退）：
- 净方向分：{net_direction:+.2f}（-1=全空，0=中性，+1=全多）
- 加权平均置信度：{avg_confidence:.2f}
- 加权平均幅度：{avg_magnitude:.2f}

按事件类型分组（按总权重降序）：
{event_type_breakdown}

按标的分组（top 5，按总权重降序）：
{symbols_breakdown}

高权重事件（weight × confidence 排序，含半衰期权重 w）：
{high_confidence_events}

请输出以下格式（纯文本，不要 markdown 代码块）：

【市场方向】
一句话总结净方向 + 驱动因素（只用代号，禁止真实币名）

【重点事件】
2-4 条最值得关注的事件，每条包含：时间、标的代号、方向、一句话原因。优先选择 w 较高的近期事件。禁止使用真实币名

【风险提示】
1-2 条仍未消化的风险（halflife_min 较长的利空事件，即使发生时间较早 w 仍较高）。只用代号，禁止真实币名

【一句话展望】
对接下来 12 小时的简短判断。只用代号，禁止真实币名
"""


@dataclass
class DigestResult:
    id: int | None
    from_at: datetime          # UTC
    to_at: datetime            # UTC
    signal_count: int
    summary: str | None        # None when skipped (min-signals threshold)
    created_at: datetime | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "from_at": utc_to_local(self.from_at).isoformat() if self.from_at else None,
            "to_at": utc_to_local(self.to_at).isoformat() if self.to_at else None,
            "signal_count": self.signal_count,
            "summary": self.summary,
            "created_at": utc_to_local(self.created_at).isoformat() if self.created_at else None,
        }


def _compute_window(storage: Storage, lookback_hours: int | None = None) -> tuple[datetime, datetime]:
    """Determine [from_at, to_at] for next digest. Both UTC, naive.

    Args:
        lookback_hours: If set (user-requested), force window to
            (now - lookback_hours, now). Overrides incremental logic.
            Capped to MAX_LOOKBACK_HOURS (720h = 30d).
    """
    now = utc_now().replace(tzinfo=None)
    if lookback_hours is not None:
        hours = max(1, min(int(lookback_hours), MAX_LOOKBACK_HOURS))
        return now - timedelta(hours=hours), now
    with storage.get_session() as sess:
        latest = sess.execute(
            select(Digest).order_by(Digest.to_at.desc()).limit(1)
        ).scalar_one_or_none()
    if latest is None:
        return now - timedelta(hours=COLD_START_HOURS), now
    from_at = latest.to_at
    # Cap: if user hasn't run digest in days, don't pull huge window.
    if (now - from_at) > timedelta(hours=MAX_WINDOW_HOURS):
        from_at = now - timedelta(hours=MAX_WINDOW_HOURS)
    # Floor: if last digest was moments ago, still allow tiny window — caller
    # will skip if signal_count < MIN_SIGNALS_FOR_SUMMARY.
    return from_at, now


def _fetch_signals(storage: Storage, from_at: datetime, to_at: datetime) -> list[dict]:
    """Fetch is_signal=1 rows in [from_at, to_at] (UTC, naive).

    JOINs messages to filter by message time (not LLM processing time) —
    a message crawled today but originally sent 2 days ago must NOT
    contaminate the "last 36h" window just because LLM ran today.
    """
    import sqlite3
    db_path = _resolve_db_path(storage)
    # Use a short-lived connection with 30s busy_timeout to coexist with
    # concurrent writers (auto-poll crawl, auto-llm batch). Without this,
    # the default busy_timeout=0 means immediate "database is locked" on
    # any concurrent write transaction.
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    # Set busy_timeout on this connection too — defense in depth. timeout
    # arg sets it for the connection, but PRAGMA makes it explicit.
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute(
        """
        SELECT sf.message_id, sf.direction, sf.magnitude, sf.urgency,
               sf.confidence, sf.halflife_min, sf.symbols, sf.event_type,
               sf.reasoning, sf.created_at,
               m.date AS message_date
        FROM signal_factors sf
        JOIN messages m ON sf.message_id = m.message_id
                       AND sf.chat_id = m.chat_id
        WHERE sf.is_signal = 1 AND sf.llm_status = 'completed'
          AND m.date > ? AND m.date <= ?
        ORDER BY m.date DESC
        """,
        (_sqlite_ts(from_at), _sqlite_ts(to_at)),
    )
    out = [dict(r) for r in cur.fetchall()]
    con.close()
    return out


def _resolve_db_path(storage: Storage) -> str:
    """Extract db path from storage engine URL."""
    url = str(storage.engine.url)
    # sqlite:///<path>
    return url.replace("sqlite:///", "", 1)


def _sqlite_ts(dt: datetime) -> str:
    """Format datetime for SQLAlchemy's SQLite DATETIME storage (space, micro).

    Port fix vs tgwatcher original: it bound `isoformat()` ('T' separator),
    which never range-matches SQLAlchemy's space-separated stored values.
    """
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


def _signal_weight(signal: dict, now: datetime) -> float:
    """计算信号权重 = 0.5^(age_minutes / halflife_min)。

    age=0 → 1.0, age=halflife → 0.5, age=2*halflife → 0.25, etc.
    Falls back to created_at when message_date missing (defensive —
    _fetch_signals always JOINs messages, but legacy data may lack it).
    """
    msg_date = signal.get("message_date") or signal.get("created_at")
    if isinstance(msg_date, str):
        msg_date = datetime.fromisoformat(msg_date)
    age_minutes = (now - msg_date).total_seconds() / 60.0
    halflife = signal.get("halflife_min") or DEFAULT_HALFLIFE_MIN
    if halflife <= 0:
        halflife = DEFAULT_HALFLIFE_MIN
    return 0.5 ** (age_minutes / halflife)


def _signed_direction_average(weights: list[float], signals: list[dict]) -> float:
    """Half-life weighted average of direction over SIGNED events only (US-007).

    Neutral events (direction = 0) are event-sensor observations with no
    directional meaning — summing them into net_direction would dilute the
    direction statistic toward zero. Count/weights still include all signals;
    only the direction average is restricted to direction!=0 rows. Returns 0.0
    when no signed event exists in the set.
    """
    signed = [(w, s) for w, s in zip(weights, signals) if s["direction"]]
    signed_weight = sum(w for w, _ in signed)
    if signed_weight <= 0:
        return 0.0
    return sum(w * s["direction"] for w, s in signed) / signed_weight


def _aggregate(signals: list[dict], from_at: datetime, to_at: datetime) -> dict:
    """Compute half-life weighted aggregate stats from signal list.

    Weighting: each signal contributes 0.5^(age/halflife) to averages —
    newer signals and longer-half-life signals (regulatory, macro) carry
    more weight than stale or short-half-life ones (market noise).
    """
    if not signals:
        return {"empty": True}

    total = len(signals)
    # Weight against the window's end, not the wall clock. Using utc_now() here
    # ignored the to_at argument entirely: any digest over a historical window
    # (a backfill, a replay, or a test with fixed timestamps) computed ages of
    # "today minus the message date", which for old windows drives every weight
    # to ~0 and collapses net_direction to 0.0.
    now = to_at.replace(tzinfo=None) if to_at.tzinfo else to_at
    weights = [_signal_weight(s, now) for s in signals]
    total_weight = sum(weights)
    if total_weight <= 0:
        total_weight = 1.0  # defensive — shouldn't happen

    w_dir = _signed_direction_average(weights, signals)
    w_conf = sum(w * s["confidence"] for w, s in zip(weights, signals)) / total_weight
    w_mag = sum(w * s["magnitude"] for w, s in zip(weights, signals)) / total_weight

    by_type: dict[str, list[tuple[float, dict]]] = defaultdict(list)
    for s, w in zip(signals, weights):
        by_type[s["event_type"]].append((w, s))

    event_type_lines = []
    for et, items in sorted(by_type.items(), key=lambda x: -sum(w for w, _ in x[1])):
        type_total_w = sum(w for w, _ in items)
        type_w_dir = _signed_direction_average([w for w, _ in items], [s for _, s in items])
        event_type_lines.append(
            f"  - {et}: {len(items)} 条, 加权方向 {type_w_dir:+.2f}, 总权重 {type_total_w:.2f}"
        )

    by_symbol: dict[str, list[tuple[float, dict]]] = defaultdict(list)
    for s, w in zip(signals, weights):
        try:
            syms = json.loads(s["symbols"]) if s["symbols"] else ["*"]
        except (json.JSONDecodeError, TypeError):
            syms = ["*"]
        for sym in syms:
            by_symbol[sym].append((w, s))

    symbol_lines = []
    for sym, items in sorted(by_symbol.items(), key=lambda x: -sum(w for w, _ in x[1]))[:5]:
        sym_total_w = sum(w for w, _ in items)
        sym_w_dir = _signed_direction_average([w for w, _ in items], [s for _, s in items])
        symbol_lines.append(
            f"  - {sym}: {len(items)} 条, 加权方向 {sym_w_dir:+.2f}, 总权重 {sym_total_w:.2f}"
        )

    # 高权重事件 — 按 weight × confidence 排序（时效性 × 重要性）
    ranked = sorted(
        signals,
        key=lambda s: _signal_weight(s, now) * s["confidence"],
        reverse=True,
    )[:8]
    high_conf_lines = []
    for s in ranked:
        w = _signal_weight(s, now)
        msg_date = s.get("message_date") or s.get("created_at")
        time_str = str(msg_date)[5:16].replace("T", " ")
        try:
            syms = json.loads(s["symbols"]) if s["symbols"] else ["*"]
            sym_str = "/".join(syms)
        except (json.JSONDecodeError, TypeError):
            sym_str = "*"
        reasoning = (s["reasoning"] or "")[:100]
        high_conf_lines.append(
            f"  - {time_str} [{sym_str}] dir={s['direction']:+.1f} "
            f"conf={s['confidence']:.2f} w={w:.2f} ({s['event_type']}): {reasoning}"
        )

    from_local = utc_to_local(from_at).strftime("%Y-%m-%d %H:%M")
    to_local = utc_to_local(to_at).strftime("%Y-%m-%d %H:%M")

    return {
        "empty": False,
        "from_at": from_local,
        "to_at": to_local,
        "total": total,
        "total_weight": total_weight,
        "net_direction": w_dir,
        "avg_confidence": w_conf,
        "avg_magnitude": w_mag,
        "event_type_breakdown": "\n".join(event_type_lines) or "  (无)",
        "symbols_breakdown": "\n".join(symbol_lines) or "  (无)",
        "high_confidence_events": "\n".join(high_conf_lines) or "  (无)",
    }


def _build_prompt(agg: dict) -> str | None:
    if agg.get("empty"):
        return None
    return DIGEST_PROMPT_TEMPLATE.format(**agg)


def generate_digest(storage: Storage, llm: SignalLLMClient,
                    lookback_hours: int | None = None) -> DigestResult:
    """Compute window, fetch signals, call LLM, persist. Returns DigestResult.

    Args:
        lookback_hours: If set, force window to (now - lookback_hours, now)
            instead of using incremental last-digest logic. Capped to
            MAX_LOOKBACK_HOURS (720h). None = incremental (default).
            When the incremental window yields <MIN_SIGNALS_FOR_SUMMARY
            signals, auto-falls back to AUTO_LOOKBACK_HOURS (168h = 7d)
            before giving up and writing the "no change" stub.
    """
    from_at, to_at = _compute_window(storage, lookback_hours=lookback_hours)
    signals = _fetch_signals(storage, from_at, to_at)
    count = len(signals)

    # Auto-fallback: incremental window had <5 signals, expand to 7d lookback
    # (only when user didn't explicitly request a window — preserves user intent).
    if count < MIN_SIGNALS_FOR_SUMMARY and lookback_hours is None:
        fallback_from, fallback_to = _compute_window(storage, lookback_hours=AUTO_LOOKBACK_HOURS)
        fallback_signals = _fetch_signals(storage, fallback_from, fallback_to)
        if len(fallback_signals) >= MIN_SIGNALS_FOR_SUMMARY:
            fallback_count = len(fallback_signals)
            logger.info(
                "Digest auto-fallback: incremental had %d signals, expanded to 7d window (%d signals)",
                count, fallback_count,
            )
            from_at, to_at, signals, count = (
                fallback_from, fallback_to, fallback_signals, fallback_count,
            )

    if count < MIN_SIGNALS_FOR_SUMMARY:
        logger.info("Digest skipped: only %d signals in window (min=%d)",
                    count, MIN_SIGNALS_FOR_SUMMARY)
        summary = f"最近无显著变化（{count} 条信号，少于 {MIN_SIGNALS_FOR_SUMMARY} 条阈值）"
        digest = Digest(from_at=from_at, to_at=to_at, signal_count=count,
                        summary=summary)
        with storage.get_session() as sess:
            sess.add(digest)
            sess.commit()
            sess.refresh(digest)
        return DigestResult(id=digest.id, from_at=digest.from_at, to_at=digest.to_at,
                           signal_count=digest.signal_count,
                           summary=digest.summary, created_at=digest.created_at)

    agg = _aggregate(signals, from_at, to_at)
    prompt = _build_prompt(agg)

    # json_mode=False: free-form Chinese prose, not structured JSON.
    raw = llm._call_llm(prompt, max_tokens_override=1024, json_mode=False)

    # Codename enforcement: replace any leaked real coin names with 标的X codes
    safe_summary = codename_map.replace_names(raw.strip())

    digest = Digest(from_at=from_at, to_at=to_at, signal_count=count,
                    summary=safe_summary)
    with storage.get_session() as sess:
        sess.add(digest)
        sess.commit()
        sess.refresh(digest)
    return DigestResult(id=digest.id, from_at=digest.from_at, to_at=digest.to_at,
                       signal_count=digest.signal_count,
                       summary=digest.summary, created_at=digest.created_at)


def get_latest_digest(storage: Storage) -> DigestResult | None:
    with storage.get_session() as sess:
        d = sess.execute(
            select(Digest).order_by(Digest.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        if d is None:
            return None
        return DigestResult(id=d.id, from_at=d.from_at, to_at=d.to_at,
                           signal_count=d.signal_count, summary=d.summary,
                           created_at=d.created_at)


def list_digests(storage: Storage, limit: int = 20) -> list[DigestResult]:
    with storage.get_session() as sess:
        rows = sess.execute(
            select(Digest).order_by(Digest.created_at.desc()).limit(limit)
        ).scalars().all()
        return [DigestResult(id=d.id, from_at=d.from_at, to_at=d.to_at,
                            signal_count=d.signal_count, summary=d.summary,
                            created_at=d.created_at) for d in rows]

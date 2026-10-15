"""Signal routes for TGWatcher API.

Phase 2B batch 3: moved verbatim from tgwatcher/web/api/_legacy.py.
Sub-blueprint registered under the parent `api` blueprint.

Routes moved (13 total):
  - POST   /signal/process
  - GET    /signal/process/status
  - POST   /signal/process/stop
  - GET    /signal/factors
  - GET    /signal/stats
  - GET    /signal/trend
  - GET    /signal/config
  - PUT    /signal/config
  - POST   /signal/reprocess/<int:message_id>
  - POST   /signals/<int:message_id>/outcome
  - GET    /signals/source-quality
  - GET    /signals/<int:message_id>/outcomes
  - GET    /signals/export
"""
import json
import logging
import os
import threading
from datetime import datetime

from flask import Blueprint, jsonify, request, Response

from tgwatcher.tz_utils import local_to_utc

from ._legacy import _app_state, _iso_z, require_auth

logger = logging.getLogger(__name__)

bp = Blueprint("signals", __name__, url_prefix="")

_signal_lock = threading.Lock()


@bp.route("/signal/process", methods=["POST"])
@require_auth
def signal_process():
    """Start batch signal processing.

    Returns 409 with {"error": "Signal processing already running"} if the
    SignalService reports it is already running, OR 409 with
    {"error": "already running", "lock": "signal"} if another caller holds
    the per-route _signal_lock (e.g. concurrent HTTP request), OR 409 with
    {"error": "AutoLlmDaemon is running"} if the daemon is mid-batch.

    Body params (all optional):
        chat_id: int — restrict to a single chat
        overwrite: bool — re-process already-analyzed messages
        date_from: ISO str — local datetime lower bound (inclusive)
        date_to:   ISO str — local datetime upper bound (inclusive, end-of-day
                   if no time component)
    """
    if not _app_state.signal_service:
        return jsonify({"error": "Signal processing not enabled"}), 400
    # Daemon互斥: daemon 正在跑 batch 时拒绝手动触发,避免并发 LLM 调用
    # 注意: 用 batch_running 而非 running — running 在整个 daemon 线程生命周期都为 True
    # (包括 60s 等待 trigger 时),会导致手动按钮永远点不动。
    daemon = getattr(_app_state, "auto_llm_daemon", None)
    if daemon is not None:
        try:
            ds = daemon.get_status()
            if ds.get("batch_running"):
                return jsonify({"error": "AutoLlmDaemon is running", "pending": ds.get("pending", 0)}), 409
        except Exception as e:
            logger.warning("Daemon status check failed: %s", e)
    acquired = _signal_lock.acquire(blocking=False)
    if not acquired:
        return jsonify({"error": "already running", "lock": "signal"}), 409
    try:
        body = request.get_json(silent=True) or {}
        chat_id = body.get("chat_id")
        overwrite = body.get("overwrite", False)
        date_from = body.get("date_from")
        date_to = body.get("date_to")
        started = _app_state.signal_service.start(
            chat_id=chat_id, overwrite=overwrite,
            date_from=date_from, date_to=date_to,
        )
        if not started:
            return jsonify({"error": "Signal processing already running"}), 409
        return jsonify({"status": "started"})
    finally:
        _signal_lock.release()


@bp.route("/signal/daemon", methods=["GET"])
@require_auth
def signal_daemon_status():
    """Get AutoLlmDaemon status: running, pending, last_batch_at,
    last_batch_count, last_digest_at.

    Returns zeros/None if daemon not initialized.
    """
    daemon = getattr(_app_state, "auto_llm_daemon", None) if _app_state else None
    if daemon is None:
        return jsonify({
            "running": False, "pending": 0,
            "last_batch_at": None, "last_batch_count": None, "last_digest_at": None
        })
    return jsonify(daemon.get_status())


@bp.route("/signal/process/status", methods=["GET"])
@require_auth
def signal_process_status():
    """Get batch signal processing status."""
    if not _app_state.signal_service:
        return jsonify({"error": "Signal processing not enabled"}), 400
    return jsonify(_app_state.signal_service.status)


@bp.route("/signal/process/stop", methods=["POST"])
@require_auth
def signal_process_stop():
    """Stop batch signal processing."""
    if not _app_state.signal_service:
        return jsonify({"error": "Signal processing not enabled"}), 400
    stopped = _app_state.signal_service.stop()
    return jsonify({"stopped": stopped})


@bp.route("/signal/factors", methods=["GET"])
@require_auth
def signal_factors():
    """Query signal factors with filters."""
    if not _app_state.storage:
        return jsonify({"error": "Storage not initialized"}), 500
    chat_id = request.args.get("chat_id", type=int)
    event_type = request.args.get("event_type")
    direction = request.args.get("direction")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    page = request.args.get("page", 1, type=int)
    page_size = request.args.get("page_size", 50, type=int)
    if event_type and event_type not in ("security", "regulatory", "macro", "whale", "market", "listing", "partnership", "other"):
        return jsonify({"error": "Invalid event_type value"}), 400
    if direction and direction not in ("bullish", "neutral", "bearish"):
        return jsonify({"error": "Invalid direction value"}), 400
    # Convert local date strings to UTC for querying against UTC-stored Message.date
    date_from_utc = None
    date_to_utc = None
    if date_from:
        date_from_utc = local_to_utc(datetime.fromisoformat(date_from))
    if date_to:
        dt_local = datetime.fromisoformat(date_to)
        if dt_local.hour == 0 and dt_local.minute == 0 and dt_local.second == 0:
            dt_local = dt_local.replace(hour=23, minute=59, second=59, microsecond=999999)
        date_to_utc = local_to_utc(dt_local)
    result = _app_state.storage.query_signal_factors(
        chat_id=chat_id, event_type=event_type, direction=direction,
        date_from=date_from_utc, date_to=date_to_utc,
        page=page, page_size=page_size,
    )
    return jsonify(result)


@bp.route("/signal/stats", methods=["GET"])
@require_auth
def signal_stats():
    """Get aggregated signal factor statistics."""
    if not _app_state.storage:
        return jsonify({"error": "Storage not initialized"}), 500
    chat_id = request.args.get("chat_id", type=int)
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    # Convert local date strings to UTC
    date_from_utc = None
    date_to_utc = None
    if date_from:
        date_from_utc = local_to_utc(datetime.fromisoformat(date_from)).isoformat()
    if date_to:
        dt_local = datetime.fromisoformat(date_to)
        if dt_local.hour == 0 and dt_local.minute == 0 and dt_local.second == 0:
            dt_local = dt_local.replace(hour=23, minute=59, second=59, microsecond=999999)
        date_to_utc = local_to_utc(dt_local).isoformat()
    result = _app_state.storage.get_signal_stats(chat_id=chat_id, date_from=date_from_utc, date_to=date_to_utc)
    return jsonify(result)


@bp.route("/signal/trend", methods=["GET"])
@require_auth
def signal_trend():
    """Get sentiment trend time series."""
    if not _app_state.storage:
        return jsonify({"error": "Storage not initialized"}), 500
    period = request.args.get("period", "day")
    days = request.args.get("days", 30, type=int)
    chat_id = request.args.get("chat_id", type=int)
    result = _app_state.storage.get_signal_trend(period=period, days=days, chat_id=chat_id)
    return jsonify(result)


@bp.route("/signal/config", methods=["GET"])
@require_auth
def signal_config_get():
    """Get signal configuration (safe fields only)."""
    signal_cfg = _app_state.config.get("signal", {}) if _app_state.config else {}
    safe_cfg = {
        "enabled": signal_cfg.get("enabled", False),
        "batch_size": signal_cfg.get("batch_size", 50),
        "llm_delay": signal_cfg.get("llm_delay", 1.0),
        "factor_version": signal_cfg.get("factor_version", 1),
        "filter": signal_cfg.get("filter", {}),
        "llm": {
            "provider": signal_cfg.get("llm", {}).get("provider", ""),
            "model": signal_cfg.get("llm", {}).get("model", ""),
            "base_url": signal_cfg.get("llm", {}).get("base_url", ""),
        },
    }
    return jsonify(safe_cfg)


@bp.route("/signal/config", methods=["PUT"])
@require_auth
def signal_config_update():
    """Update signal keywords configuration."""
    if not _app_state.config:
        return jsonify({"error": "Config not loaded"}), 500
    body = request.get_json(silent=True) or {}
    keywords = body.get("keywords")
    if keywords and isinstance(keywords, dict):
        if "signal" not in _app_state.config:
            _app_state.config["signal"] = {}
        _app_state.config["signal"]["keywords"] = keywords
        return jsonify({"status": "updated"})
    return jsonify({"error": "Invalid keywords format"}), 400


@bp.route("/signal/reprocess/<int:message_id>", methods=["POST"])
@require_auth
def signal_reprocess(message_id: int):
    """Re-process a single message's signal factors."""
    if not _app_state.signal_engine or not _app_state.storage:
        return jsonify({"error": "Signal processing not available"}), 400
    chat_id = request.args.get("chat_id", type=int)
    if not chat_id:
        return jsonify({"error": "chat_id required"}), 400
    # Find the message
    msg = _app_state.storage.get_message_by_id(message_id)
    if not msg:
        return jsonify({"error": "Message not found"}), 404
    factor = _app_state.signal_engine.process_message(msg)
    if factor:
        return jsonify(factor)
    return jsonify({"error": "Processing failed"}), 500


# ===== Signal outcome feedback (downstream reports actual price action) =====

@bp.route("/signals/<int:message_id>/outcome", methods=["POST"])
@require_auth
def record_signal_outcome(message_id: int):
    """Record a downstream-reported outcome for a signal.

    Body may include chat_id; if omitted, falls back to ?chat_id= query param.
    Required: chat_id. Optional: actual_direction, magnitude_pct, time_horizon_min,
    price_t0, price_tn, note, source.
    """
    if not _app_state.storage:
        return jsonify({"error": "Storage not initialized"}), 500
    data = request.get_json(silent=True) or {}
    chat_id = data.get("chat_id") or request.args.get("chat_id", type=int)
    if not chat_id:
        return jsonify({"error": "chat_id required (body or query)"}), 400
    outcome = {
        "message_id": message_id,
        "chat_id": int(chat_id),
        "actual_direction": data.get("actual_direction"),
        "magnitude_pct": data.get("magnitude_pct"),
        "time_horizon_min": data.get("time_horizon_min"),
        "price_t0": data.get("price_t0"),
        "price_tn": data.get("price_tn"),
        "note": data.get("note"),
        "source": data.get("source"),
    }
    try:
        saved = _app_state.storage.save_signal_outcome(outcome)
        # Serialize datetimes so jsonify doesn't choke on raw datetime objects.
        for k, v in list(saved.items()):
            iso = _iso_z(v)
            if iso is not None:
                saved[k] = iso
        # Accumulate into source quality tracker (skeleton — no-op effect
        # until outcomes actually flow in, but the wiring is in place).
        if _app_state.source_quality_tracker is not None:
            try:
                _app_state.source_quality_tracker.accumulate(saved)
            except Exception as qe:
                logger.warning("Source quality tracker accumulate failed: %s", qe)
        return jsonify({"status": "recorded", "outcome": saved})
    except Exception as e:
        logger.error("save_signal_outcome failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@bp.route("/signals/source-quality", methods=["GET"])
@require_auth
def get_source_quality():
    """Return per-chat source quality stats accumulated from outcome feedback.

    Skeleton endpoint: returns zero stats until outcomes flow in (Selene
    not yet integrated). Per-chat aggregation includes outcome_count,
    avg_magnitude_pct, direction_distribution, last_outcome_at.

    Optional ?chat_id=<id> filters to a single chat.
    """
    if _app_state.source_quality_tracker is None:
        return jsonify({"error": "Source quality tracker not initialized"}), 503
    chat_id = request.args.get("chat_id", type=int)
    if chat_id is not None:
        return jsonify(_app_state.source_quality_tracker.stats(chat_id=chat_id))
    return jsonify(_app_state.source_quality_tracker.to_dict())


@bp.route("/signals/<int:message_id>/outcomes", methods=["GET"])
@require_auth
def get_signal_outcomes(message_id: int):
    """List all outcomes reported for a signal."""
    if not _app_state.storage:
        return jsonify({"error": "Storage not initialized"}), 500
    chat_id = request.args.get("chat_id", type=int)
    if not chat_id:
        return jsonify({"error": "chat_id required"}), 400
    outcomes = _app_state.storage.get_signal_outcomes(message_id, chat_id)
    # Serialize datetimes
    for o in outcomes:
        for k, v in list(o.items()):
            iso = _iso_z(v)
            if iso is not None:
                o[k] = iso
    return jsonify({"message_id": message_id, "chat_id": chat_id, "outcomes": outcomes})


@bp.route("/signals/export", methods=["GET"])
@require_auth
def export_signals():
    """Export signal analysis results with message context."""
    fmt = request.args.get("format", "json")
    chat_id = request.args.get("chat_id", type=int)
    date_from = request.args.get("date_from", type=str)
    date_to = request.args.get("date_to", type=str)
    event_type = request.args.get("event_type", type=str)
    direction = request.args.get("direction", type=str)
    llm_model = request.args.get("llm_model", type=str)
    is_signal = request.args.get("is_signal", type=str)
    count_only = request.args.get("count_only", "").lower() == "true"

    df = local_to_utc(datetime.fromisoformat(date_from)) if date_from else None
    dt = None
    if date_to:
        dt_local = datetime.fromisoformat(date_to)
        if dt_local.hour == 0 and dt_local.minute == 0 and dt_local.second == 0:
            dt_local = dt_local.replace(hour=23, minute=59, second=59, microsecond=999999)
        dt = local_to_utc(dt_local)

    rows = _app_state.storage.query_signals_export(
        chat_id=chat_id,
        date_from=df,
        date_to=dt,
        event_type=event_type,
        direction=direction,
        llm_model=llm_model,
        is_signal=is_signal,
        count_only=count_only,
    )

    if count_only:
        return jsonify({"count": rows})

    # Serialize date field for JSON/CSV/Markdown rendering
    for r in rows:
        d = r["date"]
        r["date"] = _iso_z(d) if isinstance(d, datetime) else (str(d) if d else None)

    if fmt == "csv":
        import csv
        import io
        output = io.StringIO()
        columns = ["message_id", "chat_id", "chat_title", "sender_name", "date", "text",
                    "direction", "magnitude", "urgency", "confidence",
                    "halflife_min", "symbols", "event_type", "reasoning"]
        writer = csv.writer(output)
        writer.writerow(columns)
        for r in rows:
            writer.writerow([
                r["message_id"], r["chat_id"], r["chat_title"], r["sender_name"],
                r["date"], (r["text"] or "").replace("\n", " "),
                r["direction"], r["magnitude"], r["urgency"], r["confidence"],
                r["halflife_min"], ",".join(r.get("symbols", [])),
                r["event_type"], r["reasoning"],
            ])
        return Response(output.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=signals_export.csv"})

    if fmt == "markdown":
        lines = ["# 信号分析导出\n"]
        meta_parts = []
        if chat_id:
            chat_title = rows[0].get("chat_title", "") if rows else ""
            meta_parts.append(f"群组: {chat_title}" if chat_title else f"群组ID: {chat_id}")
        else:
            meta_parts.append("群组: 全部")
        if date_from:
            meta_parts.append(f"起始: {date_from}")
        if date_to:
            meta_parts.append(f"结束: {date_to}")
        meta_parts.append(f"共 {len(rows)} 条")
        lines.append(" | ".join(meta_parts))
        lines.append("")

        for r in rows:
            date_str = (r.get("date") or "-").replace("T", " ")[:16]
            sender = r.get("sender_name") or "未知"
            chat_tag = f" [{r.get('chat_title', '')}]" if not chat_id and r.get("chat_title") else ""
            lines.append(f"### [{date_str}] {sender}{chat_tag}")

            text = r.get("text") or ""
            if text:
                lines.append(f"> {text}")
            lines.append("")

            d = r.get("direction", 0)
            direction_label = "利多" if d > 0.1 else ("利空" if d < -0.1 else "中性")
            symbols_str = ",".join(r.get("symbols", [])) or "-"
            event_map = {"security": "安全", "regulatory": "监管", "macro": "宏观",
                         "whale": "鲸鱼", "market": "市场", "listing": "上线",
                         "partnership": "合作", "other": "其他"}
            et = event_map.get(r["event_type"], r["event_type"])
            lines.append(f"**{direction_label}** ({d:+.2f}) | {et} | 幅度{r['magnitude']:.2f} | "
                         f"紧急{r['urgency']:.2f} | 置信{r['confidence']:.2f} | "
                         f"半衰期{r['halflife_min']}min | {symbols_str}")
            if r["reasoning"]:
                lines.append(f"  推理: {r['reasoning']}")

            lines.append("")
            lines.append("---")
            lines.append("")
        return Response("\n".join(lines), mimetype="text/markdown",
                        headers={"Content-Disposition": "attachment; filename=signals_export.md"})

    if fmt == "sqlite":
        import tempfile
        from sqlalchemy import create_engine as ce
        from sqlalchemy import text as sql_text
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp_path = tmp.name
        tmp.close()
        eng = ce(f"sqlite:///{tmp_path}")
        with eng.connect() as c:
            c.execute(sql_text("PRAGMA journal_mode=WAL"))
            c.execute(sql_text("""
                CREATE TABLE IF NOT EXISTS tg_factors (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    msg_id       INTEGER NOT NULL,
                    ts           TEXT NOT NULL,
                    symbols      TEXT NOT NULL,
                    direction    REAL NOT NULL,
                    magnitude    REAL NOT NULL,
                    urgency      REAL NOT NULL,
                    confidence   REAL NOT NULL,
                    halflife_min INTEGER NOT NULL,
                    event_type   TEXT NOT NULL,
                    reasoning    TEXT NOT NULL,
                    created_at   TEXT DEFAULT (datetime('now')),
                    UNIQUE(msg_id)
                )
            """))
            c.execute(sql_text("CREATE INDEX IF NOT EXISTS idx_tg_factors_ts ON tg_factors(ts)"))
            c.execute(sql_text("CREATE INDEX IF NOT EXISTS idx_tg_factors_event_type ON tg_factors(event_type)"))
            for r in rows:
                sym_list = r.get("symbols", [])
                if not sym_list:
                    sym_list = ["*"]
                symbols_json = json.dumps(sym_list, ensure_ascii=False)
                ts_val = r.get("date", "")
                c.execute(sql_text("""
                    INSERT OR REPLACE INTO tg_factors
                    (msg_id, ts, symbols, direction, magnitude, urgency, confidence, halflife_min, event_type, reasoning)
                    VALUES (:msg_id, :ts, :symbols, :direction, :magnitude, :urgency, :confidence, :halflife_min, :event_type, :reasoning)
                """), {
                    "msg_id": r["message_id"],
                    "ts": ts_val,
                    "symbols": symbols_json,
                    "direction": r.get("direction", 0.0) if r.get("direction") is not None else 0.0,
                    "magnitude": r.get("magnitude", 0.1) if r.get("magnitude") is not None else 0.1,
                    "urgency": r.get("urgency", 0.1) if r.get("urgency") is not None else 0.1,
                    "confidence": r.get("confidence", 0.9) if r.get("confidence") is not None else 0.9,
                    "halflife_min": r.get("halflife_min", 60) if r.get("halflife_min") is not None else 60,
                    "event_type": r.get("event_type") or "other",
                    "reasoning": r.get("reasoning") or "",
                })
            c.commit()
        eng.dispose()
        with open(tmp_path, "rb") as f:
            db_bytes = f.read()
        os.unlink(tmp_path)
        return Response(db_bytes, mimetype="application/x-sqlite3",
                        headers={"Content-Disposition": "attachment; filename=tg_factors.db"})

    return jsonify(rows)

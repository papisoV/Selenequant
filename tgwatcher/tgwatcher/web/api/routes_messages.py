"""Messages query routes for TGWatcher API.

Phase 2B batch 2: moved verbatim from tgwatcher/web/api/_legacy.py.
Sub-blueprint registered under the parent `api` blueprint.
"""
from datetime import datetime

from flask import Blueprint, jsonify, request, Response

from tgwatcher.tz_utils import local_to_utc

from ._legacy import _app_state, require_auth

bp = Blueprint("messages", __name__, url_prefix="")


@bp.route("/messages", methods=["GET"])
@require_auth
def get_messages():
    chat_id = request.args.get("chat_id", type=int)
    keyword = request.args.get("keyword", type=str)
    sender_id = request.args.get("sender_id", type=int)
    date_from = request.args.get("date_from", type=str)
    date_to = request.args.get("date_to", type=str)
    page = request.args.get("page", 1, type=int)
    page_size = request.args.get("size", 50, type=int)
    media_type = request.args.get("media_type", type=str)
    include_deleted = request.args.get("include_deleted", "0") == "1"

    if keyword and len(keyword) > 200:
        return jsonify({"error": "Keyword too long (max 200 characters)"}), 400
    page_size = max(1, min(page_size, 200))

    df = local_to_utc(datetime.fromisoformat(date_from)) if date_from else None
    dt = None
    if date_to:
        dt_local = datetime.fromisoformat(date_to)
        if dt_local.hour == 0 and dt_local.minute == 0 and dt_local.second == 0:
            dt_local = dt_local.replace(hour=23, minute=59, second=59, microsecond=999999)
        dt = local_to_utc(dt_local)

    result = _app_state.storage.query_messages(
        chat_id=chat_id, keyword=keyword, sender_id=sender_id,
        date_from=df, date_to=dt,
        page=page, page_size=page_size,
        media_type=media_type, include_deleted=include_deleted,
    )
    return jsonify(result)


@bp.route("/chats", methods=["GET"])
@require_auth
def get_chats():
    chats = _app_state.storage.get_chats()
    # Merge auto_catchup and auto_listen flags from config
    group_map = {g.get("id"): g for g in _app_state.config.get("groups", [])}
    for c in chats:
        g = group_map.get(c["chat_id"], {})
        c["auto_catchup"] = g.get("auto_catchup", False)
        c["auto_listen"] = g.get("auto_listen", False)
    return jsonify(chats)


@bp.route("/senders", methods=["GET"])
@require_auth
def get_senders():
    chat_id = request.args.get("chat_id", type=int)
    senders = _app_state.storage.get_senders(chat_id=chat_id)
    return jsonify(senders)


@bp.route("/messages/<int:message_id>/reply", methods=["GET"])
@require_auth
def get_reply_message(message_id):
    msg = _app_state.storage.get_message_by_id(message_id)
    if not msg:
        return jsonify({"error": "Message not found"}), 404
    return jsonify(msg)


@bp.route("/messages/export", methods=["GET"])
@require_auth
def export_messages():
    fmt = request.args.get("format", "json")
    chat_id = request.args.get("chat_id", type=int)
    keyword = request.args.get("keyword", type=str)
    sender_id = request.args.get("sender_id", type=int)
    date_from = request.args.get("date_from", type=str)
    date_to = request.args.get("date_to", type=str)

    if keyword and len(keyword) > 200:
        return jsonify({"error": "Keyword too long"}), 400

    df = local_to_utc(datetime.fromisoformat(date_from)) if date_from else None
    dt = None
    if date_to:
        dt_local = datetime.fromisoformat(date_to)
        if dt_local.hour == 0 and dt_local.minute == 0 and dt_local.second == 0:
            dt_local = dt_local.replace(hour=23, minute=59, second=59, microsecond=999999)
        dt = local_to_utc(dt_local)

    result = _app_state.storage.query_messages(
        chat_id=chat_id, keyword=keyword, sender_id=sender_id,
        date_from=df, date_to=dt, page=1, page_size=50000,
    )
    messages = result.get("messages", [])

    if fmt == "csv":
        import csv
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["message_id", "chat_id", "chat_title", "sender_id", "sender_name",
                         "sender_username", "text", "reply_to_msg_id", "forward_from", "date",
                         "has_media", "is_edited", "edited_at", "media_type"])
        for m in messages:
            writer.writerow([m.get("message_id"), m.get("chat_id"), m.get("chat_title"),
                             m.get("sender_id"), m.get("sender_name"), m.get("sender_username"),
                             m.get("text", "").replace("\n", "\\n"), m.get("reply_to_msg_id"),
                             m.get("forward_from"), m.get("date"), m.get("has_media"),
                             m.get("is_edited"), m.get("edited_at"), m.get("media_type")])
        return Response(output.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=tgwatcher_export.csv"})

    if fmt == "markdown":
        lines = ["# 聊天记录导出\n"]
        meta_parts = []
        if chat_id:
            chat_title = messages[0].get("chat_title", "") if messages else ""
            meta_parts.append(f"群组: {chat_title}" if chat_title else f"群组ID: {chat_id}")
        else:
            meta_parts.append("群组: 全部")
        if date_from:
            meta_parts.append(f"起始: {date_from}")
        if date_to:
            meta_parts.append(f"结束: {date_to}")
        meta_parts.append(f"共 {len(messages)} 条消息")
        lines.append(" | ".join(meta_parts))
        lines.append("")
        for m in messages:
            date_str = (m.get("date") or "-").replace("T", " ")[:16]
            sender = m.get("sender_name") or m.get("sender_username") or "未知"
            chat_tag = f" [{m.get('chat_title', '')}]" if not chat_id and m.get("chat_title") else ""
            lines.append(f"### [{date_str}] {sender}{chat_tag}")
            text = m.get("text") or ""
            if text:
                lines.append(text)
            fwd = m.get("forward_from")
            if fwd:
                lines.append(f"*转发自: {fwd}*")
            reply = m.get("reply_to_msg_id")
            if reply:
                lines.append(f"*回复消息ID: {reply}*")
            lines.append("")
            lines.append("---")
            lines.append("")
        return Response("\n".join(lines), mimetype="text/markdown",
                        headers={"Content-Disposition": "attachment; filename=tgwatcher_export.md"})

    return jsonify(messages)

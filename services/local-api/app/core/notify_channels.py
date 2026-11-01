"""Notification channel senders — webhook 五方言 + 渠道持久化。

吸收自 QD signal_notifier 的方言自动识别与签名协议（行为契约，cleanroom）：
- 按域名自动识别 feishu/dingtalk/wecom/slack/discord/generic
- feishu: body 内 timestamp+sign（HMAC-SHA256, string_to_sign="{ts}\n{secret}"）
- dingtalk: URL 追加 timestamp+sign（urlencoded base64）
- generic: body-bound HMAC-SHA256 hex → X-QD-Signature/X-QD-Timestamp
- wecom/slack: 密钥在 URL 内，免签
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlsplit, urlunsplit


from selene_space_core.resilience import OutboundGateway

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 3900


def detect_webhook_dialect(url: str) -> str:
    host = (urlsplit(str(url or "")).netloc or "").lower()
    raw = str(url or "").lower()
    if "discord.com/api/webhooks" in raw:
        return "discord"
    if "open.feishu.cn" in host or "larksuite.com" in host:
        return "feishu"
    if "oapi.dingtalk.com" in host:
        return "dingtalk"
    if "qyapi.weixin.qq.com" in host or "work.weixin.qq.com" in host:
        return "wecom"
    if "hooks.slack.com" in host:
        return "slack"
    return "generic"


def adapt_payload_for_dialect(dialect: str, text: str) -> dict[str, Any]:
    if dialect == "feishu":
        return {"msg_type": "text", "content": {"text": text}}
    if dialect in ("dingtalk", "wecom"):
        return {"msgtype": "text", "text": {"content": text}}
    # slack / discord / generic 都收 {"text": ...}（discord 也接受）
    return {"text": text}


def _feishu_sign(secret: str, timestamp: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _dingtalk_signed_url(url: str, secret: str) -> str:
    ts = str(int(time.time() * 1000))
    string_to_sign = f"{ts}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
    sign = quote_plus(base64.b64encode(digest).decode("utf-8"))
    parts = urlsplit(str(url))
    sep = "&" if parts.query else ""
    query = f"{parts.query}{sep}timestamp={ts}&sign={sign}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _check_vendor_response(
    dialect: str, status_code: int, text: str
) -> tuple[bool, str]:
    if not (200 <= int(status_code or 0) < 300):
        return False, f"http_{status_code}:{text[:300]}"
    try:
        data = json.loads(text or "{}")
    except Exception:
        data = {}
    if dialect in ("feishu", "dingtalk", "wecom") and isinstance(data, dict):
        code = data.get("StatusCode", data.get("code", data.get("errcode", 0)))
        if str(code) not in ("", "0", "200"):
            msg = (
                data.get("msg")
                or data.get("errmsg")
                or data.get("StatusMessage")
                or text
            )
            return False, f"vendor_{code}:{msg[:300]}"
    return True, ""


async def send_webhook(
    client: OutboundGateway,
    url: str,
    text: str,
    secret: str = "",
) -> str:
    """按方言发送 webhook 通知，返回状态串（"ok" 或失败原因）。"""
    dialect = detect_webhook_dialect(url)
    payload = adapt_payload_for_dialect(dialect, text[:MAX_TEXT_CHARS])
    try:
        if secret and dialect == "feishu":
            ts = str(int(time.time()))
            payload["timestamp"] = ts
            payload["sign"] = _feishu_sign(secret, ts)
            resp = await client.post(url, json=payload)
        elif secret and dialect == "dingtalk":
            resp = await client.post(_dingtalk_signed_url(url, secret), json=payload)
        elif secret and dialect == "generic":
            ts = str(int(time.time()))
            body = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            sig_base = (ts + ".").encode("utf-8") + body
            sig = hmac.new(secret.encode("utf-8"), sig_base, hashlib.sha256).hexdigest()
            resp = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-QD-Timestamp": ts,
                    "X-QD-Signature": sig,
                },
            )
        else:
            resp = await client.post(url, json=payload)
        ok, err = _check_vendor_response(dialect, resp.status_code, resp.text)
        return "ok" if ok else err
    except Exception as exc:  # noqa: BLE001
        logger.warning("webhook send failed (%s): %s", dialect, exc)
        return f"failed:{type(exc).__name__}"


# ---------------------------------------------------------------------------
# Channel persistence (JSON file under ~/.selenespace, same pattern as settings)
# ---------------------------------------------------------------------------


def channels_path() -> Path:
    return Path.home() / ".selenespace" / "notification_channels.json"


def load_channels() -> list[dict[str, Any]]:
    p = channels_path()
    if not p.exists():
        return []
    try:
        loaded: list[dict[str, Any]] = json.loads(p.read_text(encoding="utf-8-sig"))
        return loaded
    except Exception:  # noqa: BLE001
        logger.warning("Failed to read notification channels file, starting empty")
        return []


def save_channels(channels: list[dict[str, Any]]) -> None:
    p = channels_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(channels, ensure_ascii=False, indent=2), encoding="utf-8")


async def send_channel_test(client: OutboundGateway, channel: dict[str, Any]) -> str:
    """Send a bilingual test message through one channel config.

    Routes through the pulse transport layer so a channel tests exactly the
    way it will deliver: the 6 absorbed channels (dingtalk / serverchan3 /
    pushplus / ntfy / gotify / pushover) plus webhook dialect adaptation.
    """
    ch_type = str(channel.get("type", "webhook"))
    config = channel.get("config") or {}
    name = channel.get("name", channel.get("id", "?"))
    text = f"[SeleneQuant] 渠道测试成功 / Channel test OK — {name} ({ch_type})"

    if ch_type == "telegram":
        token = str(config.get("bot_token", ""))
        chat_id = str(config.get("chat_id", ""))
        if not token or not chat_id:
            return "missing bot_token/chat_id"
        try:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
            return "ok" if resp.status_code == 200 else f"http_{resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            return f"failed:{type(exc).__name__}"

    # Everything else goes through pulse (dialect-aware webhook + 6 channels).
    try:
        from selene_space_types.stock import NotificationTarget
        from selene_space_pulse.notify import send_to_targets

        target = NotificationTarget(
            type=ch_type, name=name, enabled=True, settings=dict(config)
        )
    except Exception as exc:  # noqa: BLE001
        return f"unsupported_type:{ch_type}:{type(exc).__name__}"

    results = send_to_targets([target], text)
    if not results:
        return "unsupported_type_or_disabled"
    status = results[0]
    return "ok" if status.endswith(" sent") else status

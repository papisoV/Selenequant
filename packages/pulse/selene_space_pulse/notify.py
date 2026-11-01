# SeleneQuant pulse channel senders. AGPL-3.0-only. Stdlib-only HTTP
# delivery to webhook-style notification vendors, with an SSRF guard,
# UTF-8-safe byte truncation, and per-target failure isolation.
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import smtplib
import socket
import ssl
import time
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path

from selene_space_stock.config import NotificationTarget
from selene_space_types.stock import DailyReport
from . import img_render
from .noise import NotificationNoise
from .reporting import render_markdown

logger = logging.getLogger(__name__)

_USER_AGENT = "selene-space-pulse/0.1"
_HTTP_TIMEOUT_SECONDS = 20

# Channels eligible for markdown-to-image rendering. feishu image upload
# requires app credentials, so it stays probe-only.
_IMAGE_CHANNELS = {"feishu", "wecom", "dingtalk"}
_IMAGE_MAX_BYTES = 2 * 1024 * 1024

# Noise-guard defaults when a target does not configure its own TTLs.
# Cooldown defaults to 0: only exact duplicate content is deduplicated.
_DEFAULT_NOISE_DEDUP_TTL_SECONDS = 300
_DEFAULT_NOISE_COOLDOWN_SECONDS = 0

# Vendor URL fragments used to classify a generic webhook target.
_WEBHOOK_DIALECT_SIGNS = {
    "discord": "discord.com/api/webhooks",
    "feishu": "open.feishu.cn",
    "larksuite": "larksuite.com",
    "dingtalk": "oapi.dingtalk.com",
    "wecom": "qyapi.weixin.qq.com",
    "work_weixin": "work.weixin.qq.com",
    "slack": "hooks.slack.com",
}


# ---------------------------------------------------------------------------
# Settings resolution
# ---------------------------------------------------------------------------


def resolve_setting(settings: dict, literal_key: str, env_key: str) -> str | None:
    """Prefer the literal settings value; fall back to an env var named by *env_key*."""
    literal = settings.get(literal_key)
    if literal:
        return str(literal)
    env_name = settings.get(env_key)
    if env_name:
        return os.getenv(str(env_name))
    return None


def _require_setting(settings: dict, kind: str, *pairs: tuple[str, str]) -> str:
    for literal_key, env_key in pairs:
        value = resolve_setting(settings, literal_key, env_key)
        if value:
            return value
    pretty = " and ".join(f"{lk}/{ek}" for lk, ek in pairs)
    raise ValueError(f"{kind} requires {pretty}")


def _require_channel_url(settings: dict, kind: str) -> str:
    url = _require_setting(settings, kind, ("url", "url_env"))
    _assert_safe_url(url)
    return url


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------


def _assert_safe_url(url: str) -> None:
    """Refuse URLs that do not point at a public HTTPS host.

    The hostname is resolved here, immediately before connecting. Redirects
    to unsafe addresses are closed separately by _NoRedirectHandler.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Unsafe notification URL scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError("Notification URL is missing a hostname")
    resolved_ip = ipaddress.ip_address(socket.gethostbyname(parsed.hostname))
    # is_global covers loopback/private/link-local/reserved plus carrier-grade
    # NAT (100.64.0.0/10) which is_private misses. Multicast reports
    # is_global=True in the ipaddress module, so it is rejected separately.
    if not resolved_ip.is_global or resolved_ip.is_multicast:
        raise ValueError(f"Unsafe notification URL host resolves to {resolved_ip}")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A validated public URL must not be allowed to redirect into
        # loopback/link-local/metadata space.
        return None


def _urlopen(request: urllib.request.Request, timeout: int):
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=timeout)


def _raise_for_status(response) -> None:
    if response.status >= 400:
        raise RuntimeError(f"HTTP {response.status}")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _post_json(url: str, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    with _urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        _raise_for_status(response)


def _post_form(url: str, fields: dict) -> None:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    with _urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        _raise_for_status(response)


def _truncate_bytes(text: str, max_bytes: int) -> str:
    """Truncate *text* to a UTF-8 byte budget without splitting a codepoint."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# Message composition
# ---------------------------------------------------------------------------


def _signed(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value > 0 else ""
    return f"{sign}{float(value):,.2f}"


def _compact_message(report: DailyReport, report_url: str | None = None) -> str:
    lines = [
        report.title,
        report.generated_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
        report.market_brief,
        "",
    ]
    if report.portfolio_ai_summary:
        lines.extend(["AI portfolio brief:", report.portfolio_ai_summary[:900], ""])
    if report.portfolio:
        for summary in report.portfolio:
            lines.append(
                f"{summary.currency} portfolio: value {summary.market_value:,.2f}, "
                f"day P/L {_signed(summary.day_pnl)}, unrealized {_signed(summary.unrealized_pnl)}"
            )
        lines.append("")
    for item in report.analyses[:12]:
        lines.append(
            f"{item.asset.symbol} {item.snapshot.name}: "
            f"score {item.signal.score}/100, {item.signal.stance}, risk {item.signal.risk_level}"
        )
    # Only include a link when it is a public URL; never leak a local path.
    if report_url and str(report_url).startswith("http"):
        lines.extend(["", f"Report: {report_url}"])
    lines.extend(["", "Research automation only. Not financial advice."])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown-to-image hook
# ---------------------------------------------------------------------------


def _image_enabled(settings: dict) -> bool:
    """Markdown-to-image is per-target opt-in; empty default keeps text."""
    return bool(
        settings.get("image")
        or settings.get("image_env") and os.getenv(str(settings["image_env"]))
    )


def _maybe_render_image(kind: str, message: str) -> bytes | None:
    """Render markdown to image bytes for whitelisted channels, else None.

    Failures never propagate: any render error falls back to text sending.
    """
    if kind not in _IMAGE_CHANNELS:
        return None
    try:
        image = img_render.render_markdown_image(message)
    except Exception as exc:
        logger.warning(
            "markdown-to-image render failed for %s, falling back to text: %s", kind, exc
        )
        return None
    if image is None:
        return None
    if len(image) > _IMAGE_MAX_BYTES:
        logger.warning(
            "rendered image too large (%d bytes) for %s, falling back to text",
            len(image),
            kind,
        )
        return None
    return image


# ---------------------------------------------------------------------------
# Channel senders
# ---------------------------------------------------------------------------


def _dingtalk_signed_url(url: str, secret: str | None) -> str:
    """Append DingTalk timestamp+HMAC-SHA256 signature params."""
    if not secret:
        return url
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}timestamp={timestamp}&sign={sign}"


def _send_dingtalk(
    url: str, settings: dict, title: str | None, text: str, *, markdown: bool
) -> None:
    signed_url = _dingtalk_signed_url(url, resolve_setting(settings, "secret", "secret_env"))
    if markdown:
        display_title = (title or "通知 (Notification)")[:100]
        _post_json(
            signed_url,
            {
                "msgtype": "markdown",
                "markdown": {
                    "title": display_title,
                    "text": _truncate_bytes(text, 19000),
                },
            },
        )
    else:
        _post_json(signed_url, {"msgtype": "text", "text": {"content": text[:3900]}})


def _send_serverchan3(sendkey: str, text: str, title: str | None = None) -> None:
    # sctp<size>t<key> keys use the per-channel ft07 mirror; everything else
    # the sctapi.ftqq.com endpoint.
    if sendkey.startswith("sctp"):
        digits = sendkey[4:].split("t", 1)[0]
        url = f"https://{digits}.push.ft07.com/send/{sendkey}.send"
    else:
        url = f"https://sctapi.ftqq.com/{sendkey}.send"
    _assert_safe_url(url)
    _post_json(
        url,
        {"title": (title or "Selene Pulse")[:100], "desp": text, "options": {}},
    )


def _send_pushplus(token: str, text: str, title: str | None = None, topic: str | None = None) -> None:
    url = "https://www.pushplus.plus/send"
    _assert_safe_url(url)
    payload: dict = {
        "token": token,
        "title": (title or "Selene Pulse")[:100],
        "content": _truncate_bytes(text, 18500),
        "template": "markdown",
    }
    if topic:
        payload["topic"] = topic
    _post_json(url, payload)


def _ntfy_endpoint(url: str) -> tuple[str, str]:
    """Split an ntfy URL into (server_root, topic).

    The topic is the final path segment; ntfy's JSON publish API is POSTed to
    the server ROOT with the topic in the body. Posting the JSON body at the
    topic URL makes ntfy treat the serialized JSON as literal message text.
    """
    parsed = urllib.parse.urlparse(url)
    segments = [s for s in parsed.path.split("/") if s]
    topic = urllib.parse.unquote(segments[-1]).strip() if segments else ""
    if not topic:
        raise ValueError("ntfy url must include a topic path segment")
    root_path = "/".join(segments[:-1])
    server = urllib.parse.urlunparse(
        parsed._replace(
            path=f"/{root_path}" if root_path else "", params="", query="", fragment=""
        )
    ).rstrip("/")
    return server, topic


def _send_ntfy(url: str, settings: dict, text: str, title: str | None = None) -> None:
    server, topic = _ntfy_endpoint(url)
    _assert_safe_url(server)
    body = json.dumps(
        {
            "topic": topic,
            "title": (title or "Selene Pulse")[:100],
            "message": text,
            "markdown": True,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": _USER_AGENT}
    token = resolve_setting(settings, "token", "token_env") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(server, data=body, headers=headers, method="POST")
    with _urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        _raise_for_status(response)


def _send_gotify(url: str, token: str, text: str, title: str | None = None) -> None:
    # The server base resolves into the fixed /message endpoint, authenticated
    # via the X-Gotify-Key header.
    endpoint = url.rstrip("/")
    if not endpoint.lower().endswith("/message"):
        endpoint = f"{endpoint}/message"
    payload = {
        "title": (title or "Selene Pulse")[:100],
        "message": text,
        "extras": {"client::display": {"contentType": "text/markdown"}},
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
            "X-Gotify-Key": token,
        },
        method="POST",
    )
    with _urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        _raise_for_status(response)


def _send_pushover(user_key: str, token: str, text: str, title: str | None = None) -> None:
    # Pushover accepts form-encoded POSTs with a 1024-char message limit.
    url = "https://api.pushover.net/1/messages.json"
    _assert_safe_url(url)
    _post_form(
        url,
        {
            "token": token,
            "user": user_key,
            "message": text,
            "title": (title or "Selene Pulse")[:100],
            "priority": 0,
        },
    )


def _send_email(settings: dict, markdown: str, html_path: Path | None) -> None:
    host = resolve_setting(settings, "smtp_host", "smtp_host_env")
    port = int(resolve_setting(settings, "smtp_port", "smtp_port_env") or "465")
    username = resolve_setting(settings, "username", "username_env")
    password = resolve_setting(settings, "password", "password_env")
    sender = resolve_setting(settings, "sender", "sender_env") or username
    recipients = resolve_setting(settings, "to", "to_env")
    if not host or not sender or not recipients:
        raise ValueError("email requires smtp_host, sender, and to")

    message = EmailMessage()
    message["Subject"] = settings.get("subject", "AI Market Pulse")
    message["From"] = sender
    message["To"] = recipients
    message.set_content(markdown)
    if html_path and html_path.exists():
        message.add_attachment(
            html_path.read_text(encoding="utf-8"),
            subtype="html",
            filename=html_path.name,
        )

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=context) as server:
        if username and password:
            server.login(username, password)
        server.send_message(message)


# ---------------------------------------------------------------------------
# Webhook dialect adaptation
# ---------------------------------------------------------------------------


def detect_webhook_dialect(url: str) -> str:
    """Classify a webhook URL into a vendor dialect (else "generic")."""
    raw = str(url or "").lower()
    if _WEBHOOK_DIALECT_SIGNS["discord"] in raw:
        return "discord"
    host = urllib.parse.urlparse(raw).netloc
    if _WEBHOOK_DIALECT_SIGNS["feishu"] in host or _WEBHOOK_DIALECT_SIGNS["larksuite"] in host:
        return "feishu"
    if _WEBHOOK_DIALECT_SIGNS["dingtalk"] in host:
        return "dingtalk"
    if (
        _WEBHOOK_DIALECT_SIGNS["wecom"] in host
        or _WEBHOOK_DIALECT_SIGNS["work_weixin"] in host
    ):
        return "wecom"
    if _WEBHOOK_DIALECT_SIGNS["slack"] in host:
        return "slack"
    return "generic"


def _feishu_signed_payload(text: str, secret: str) -> dict:
    """Feishu custom-bot signing: body carries timestamp + HMAC-SHA256 sign."""
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, b"", hashlib.sha256).digest()
    return {
        "timestamp": timestamp,
        "sign": base64.b64encode(digest).decode("utf-8"),
        "msg_type": "text",
        "content": {"text": text},
    }


def _generic_signed_headers(text: str, secret: str) -> tuple[bytes, dict]:
    """Generic webhook signing: body-bound HMAC hex in X-QD-* headers."""
    timestamp = str(int(time.time()))
    body = json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    sig = hmac.new(
        secret.encode("utf-8"), (timestamp + ".").encode("utf-8") + body, hashlib.sha256
    ).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
        "X-QD-Timestamp": timestamp,
        "X-QD-Signature": sig,
    }


def _send_webhook(url: str, settings: dict, text: str) -> None:
    """Send to a `webhook` target, adapting payload + signing to the vendor."""
    _assert_safe_url(url)
    secret = resolve_setting(settings, "secret", "secret_env") or ""
    dialect = detect_webhook_dialect(url)

    if dialect == "feishu":
        if secret:
            _post_json(url, _feishu_signed_payload(text, secret))
        else:
            _post_json(url, {"msg_type": "text", "content": {"text": text}})
        return
    if dialect == "dingtalk":
        if secret:
            _post_json(
                _dingtalk_signed_url(url, secret),
                {"msgtype": "text", "text": {"content": text}},
            )
        else:
            _post_json(url, {"msgtype": "text", "text": {"content": text}})
        return
    if dialect == "wecom":
        _post_json(url, {"msgtype": "text", "text": {"content": text}})
        return
    if dialect == "slack":
        _post_json(url, {"text": text})
        return
    if dialect == "discord":
        _post_json(url, {"content": text[:1900]})
        return
    if secret:
        body, headers = _generic_signed_headers(text, secret)
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with _urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            _raise_for_status(response)
        return
    _post_json(url, {"text": text})


# ---------------------------------------------------------------------------
# Target dispatch
# ---------------------------------------------------------------------------


def _send_channel_text(kind: str, settings: dict, text: str, *, markdown: bool) -> None:
    """Dispatch plain text (or markdown) to one configured channel. Raises on
    missing configuration or delivery failure."""
    if kind == "telegram":
        token = _require_setting(settings, kind, ("token", "token_env"))
        chat_id = _require_setting(settings, kind, ("chat_id", "chat_id_env"))
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        _post_json(
            url,
            {"chat_id": chat_id, "text": text[:3900], "disable_web_page_preview": True},
        )
    elif kind == "webhook":
        url = _require_setting(settings, kind, ("url", "url_env"))
        _send_webhook(url, settings, text[:3900])
    elif kind == "slack":
        url = _require_setting(settings, kind, ("url", "url_env"))
        _assert_safe_url(url)
        _post_json(url, {"text": text[:3900]})
    elif kind == "discord":
        url = _require_setting(settings, kind, ("url", "url_env"))
        _assert_safe_url(url)
        _post_json(url, {"content": text[:1900]})
    elif kind == "feishu":
        url = _require_channel_url(settings, kind)
        _post_json(url, {"msg_type": "text", "content": {"text": text[:3900]}})
    elif kind == "wecom":
        url = _require_channel_url(settings, kind)
        _post_json(url, {"msgtype": "text", "text": {"content": text[:3900]}})
    elif kind == "dingtalk":
        url = _require_channel_url(settings, kind)
        _send_dingtalk(url, settings, title=None, text=text, markdown=markdown)
    elif kind in {"serverchan3", "serverchan"}:
        sendkey = _require_setting(
            settings, kind, ("token", "token_env"), ("sendkey", "sendkey_env")
        )
        _send_serverchan3(sendkey, text[:3900])
    elif kind == "pushplus":
        token = _require_setting(settings, kind, ("token", "token_env"))
        _send_pushplus(token, text, topic=resolve_setting(settings, "topic", "topic_env"))
    elif kind == "ntfy":
        url = _require_channel_url(settings, kind)
        _send_ntfy(url, settings, text[:3900])
    elif kind == "gotify":
        token = _require_setting(settings, kind, ("token", "token_env"))
        url = _require_channel_url(settings, kind)
        _send_gotify(url, token, text[:3900])
    elif kind == "pushover":
        user_key = _require_setting(settings, kind, ("user_key", "user_key_env"))
        token = _require_setting(settings, kind, ("token", "token_env"))
        _send_pushover(user_key, token, text[:1024])
    else:
        raise ValueError(f"Unsupported notification type: {kind}")


def _send_target(
    target: NotificationTarget,
    message: str,
    report: DailyReport,
    html_path: Path | None,
) -> None:
    kind = target.type.lower()
    settings = target.settings
    if kind == "email":
        _send_email(settings, render_markdown(report), html_path)
        return
    if kind in _IMAGE_CHANNELS and _image_enabled(settings):
        image = _maybe_render_image(kind, message)
        if image is not None:
            url = _require_channel_url(settings, kind)
            if kind == "wecom":
                _post_json(
                    url,
                    {
                        "msgtype": "image",
                        "image": {
                            "base64": base64.b64encode(image).decode("ascii"),
                            "md5": hashlib.md5(image).hexdigest(),
                        },
                    },
                )
            else:
                # feishu image delivery requires app-credential media upload;
                # dingtalk image upload is not wired up either. Fall back to
                # the text/markdown sender below.
                logger.info(
                    "%s markdown-to-image rendered but image upload is not supported yet; sending text",
                    kind,
                )
                _send_channel_text(kind, settings, message, markdown=True)
            return
    _send_channel_text(kind, settings, message, markdown=True)


def _send_target_text(target: NotificationTarget, text: str) -> None:
    """Send plain text to one target, silently skipping unconfigured channels
    (grid/trade alert path: missing settings are not an error)."""
    kind = target.type.lower()
    settings = target.settings
    if kind == "email":
        return
    try:
        _send_channel_text(kind, settings, text, markdown=False)
    except ValueError as exc:
        if "requires" in str(exc) or "Unsupported" in str(exc):
            return
        raise


# ---------------------------------------------------------------------------
# Batch entry points
# ---------------------------------------------------------------------------


def send_notifications(
    report: DailyReport,
    targets: list[NotificationTarget],
    html_path: Path | None = None,
    report_url: str | None = None,
    *,
    noise: NotificationNoise | None = None,
) -> list[str]:
    results: list[str] = []
    message = _compact_message(report, report_url)
    for target in targets:
        if not target.enabled:
            continue
        if noise is not None:
            try:
                content_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]
                decision = noise.evaluate(
                    dedup_key=f"{target.type}:{content_hash}",
                    cooldown_key=f"{target.type}:cd",
                    dedup_ttl=int(
                        target.settings.get(
                            "dedup_ttl_seconds", _DEFAULT_NOISE_DEDUP_TTL_SECONDS
                        )
                        or 0
                    ),
                    cooldown_ttl=int(
                        target.settings.get(
                            "cooldown_seconds", _DEFAULT_NOISE_COOLDOWN_SECONDS
                        )
                        or 0
                    ),
                )
            except Exception as exc:  # fail-open: noise must never block delivery
                logger.warning("noise evaluation failed for %s: %s", target.type, exc)
                decision = None
            if decision is not None and not decision.allowed:
                results.append(
                    f"{target.type}:{target.name or 'default'} suppressed: {decision.reason}"
                )
                continue
        try:
            _send_target(target, message, report, html_path)
            results.append(f"{target.type}:{target.name or 'default'} sent")
        except Exception as exc:
            results.append(f"{target.type}:{target.name or 'default'} failed: {exc}")
    return results


def _evaluate_target_noise(
    target: NotificationTarget, text: str, noise: NotificationNoise
):
    """Evaluate the noise gate for one plain-text target delivery.

    Mirrors the send_notifications key scheme (per-target-type + content hash)
    so dedup state is shared between the report path and the alert path.
    Fail-open: any evaluation error is treated as an allow decision.
    """
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return noise.evaluate(
        dedup_key=f"{target.type}:{target.name or 'default'}:{content_hash}",
        cooldown_key=f"{target.type}:{target.name or 'default'}:cd",
        dedup_ttl=int(
            target.settings.get("dedup_ttl_seconds", _DEFAULT_NOISE_DEDUP_TTL_SECONDS)
            or 0
        ),
        cooldown_ttl=int(
            target.settings.get("cooldown_seconds", _DEFAULT_NOISE_COOLDOWN_SECONDS)
            or 0
        ),
    )


def send_to_targets(
    targets: list[NotificationTarget],
    text: str,
    noise: NotificationNoise | None = None,
) -> list[str]:
    """Send plain text to an explicit target list (no global/env lookup).

    The caller owns target resolution. Failures are reported per target,
    never raised — alert delivery must not break the caller's control flow.
    Suppression by the optional noise gate is a reported state, not a failure.
    """
    results: list[str] = []
    for target in targets:
        if not target.enabled:
            continue
        label = f"{target.type}:{target.name or 'default'}"
        if noise is not None:
            try:
                decision = _evaluate_target_noise(target, text, noise)
            except Exception as exc:  # fail-open: noise must never block delivery
                logger.warning("noise evaluation failed for %s: %s", label, exc)
                decision = None
            if decision is not None and not decision.allowed:
                results.append(f"{label} suppressed: {decision.reason}")
                continue
        try:
            _send_target_text(target, text)
            results.append(f"{label} sent")
        except Exception as exc:
            logger.warning("[dispatch] %s failed: %s", label, exc)
            results.append(f"{label} failed: {exc}")
    return results


# ---------------------------------------------------------------------------
# Grid / trade alert helpers
# ---------------------------------------------------------------------------

# Lazy-loaded grid notification targets, populated on first use from
# environment variables (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, NOTIFY_WEBHOOK).
_grid_targets: list[NotificationTarget] | None = None


def _load_grid_targets() -> list[NotificationTarget]:
    targets: list[NotificationTarget] = []
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if tg_token and tg_chat:
        targets.append(
            NotificationTarget(
                type="telegram",
                name="grid-telegram",
                enabled=True,
                settings={"token": tg_token, "chat_id": tg_chat},
            )
        )
    webhook_url = os.getenv("NOTIFY_WEBHOOK", "")
    if webhook_url:
        targets.append(
            NotificationTarget(
                type="webhook",
                name="grid-webhook",
                enabled=True,
                settings={"url": webhook_url},
            )
        )
    return targets


def send_grid_alert(text: str) -> list[str]:
    """Send a grid alert to all configured targets. Never raises."""
    global _grid_targets
    if _grid_targets is None:
        try:
            _grid_targets = _load_grid_targets()
        except Exception:
            _grid_targets = []

    if not _grid_targets:
        logger.info(
            "[grid-alert] No notification targets configured, skipping: %s", text[:100]
        )
        return []

    results: list[str] = []
    for target in _grid_targets:
        if not target.enabled:
            continue
        try:
            _send_target_text(target, text)
            results.append(f"{target.type}:{target.name or 'grid'} sent")
        except Exception as exc:
            logger.warning("[grid-alert] %s:%s failed: %s", target.type, target.name, exc)
            results.append(f"{target.type}:{target.name or 'grid'} failed: {exc}")
    return results


def send_trade_alert(text: str) -> list[str]:
    """Send a trade alert; messages are prefixed so recipients can filter."""
    return send_grid_alert(f"[trade-alert] {text}")

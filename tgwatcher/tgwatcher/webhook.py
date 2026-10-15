"""Webhook dispatcher for new signals.

Sends HMAC-signed POST requests to downstream endpoints when a new signal is
produced. Dispatch is async (thread pool) — failures never block signal_engine.
"""
import hashlib
import hmac
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)


class WebhookEndpoint:
    """A single webhook endpoint config (url + secret + enabled)."""

    def __init__(self, url: str, secret: str = "", enabled: bool = True):
        self.url = url
        self.secret = secret
        self.enabled = enabled

    def to_dict(self, include_secret: bool = False) -> dict:
        d = {"url": self.url, "enabled": self.enabled}
        if include_secret:
            d["secret"] = self.secret
        return d


class WebhookDispatcher:
    """Dispatch new_signal payloads to all configured endpoints.

    Failed dispatches are logged + surfaced via SSE (webhook_status) but
    never retried (avoids out-of-order delivery). Disabled or empty config
    yields a no-op dispatcher (dispatch() returns immediately).
    """

    def __init__(self, config: dict):
        self._endpoints: list[WebhookEndpoint] = []
        self._timeout: float = 5.0
        self._max_workers: int = 4
        self._enabled: bool = False
        self._fail_counts: dict[str, int] = {}
        self._success_counts: dict[str, int] = {}
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None

        try:
            wh = config.get("webhook", {}) or {}
            self._enabled = bool(wh.get("enabled", False))
            self._timeout = float(wh.get("timeout_seconds", 5))
            self._max_workers = int(wh.get("max_workers", 4))
            for ep in wh.get("endpoints", []) or []:
                self._endpoints.append(WebhookEndpoint(
                    url=ep.get("url", ""),
                    secret=ep.get("secret", ""),
                    enabled=bool(ep.get("enabled", True)),
                ))
            # Filter out endpoints with empty URL
            self._endpoints = [e for e in self._endpoints if e.url]
            if self._enabled and self._endpoints:
                self._executor = ThreadPoolExecutor(
                    max_workers=self._max_workers,
                    thread_name_prefix="webhook",
                )
                logger.info(
                    "WebhookDispatcher initialized: %d endpoint(s), timeout=%.1fs",
                    len(self._endpoints), self._timeout,
                )
            else:
                logger.info(
                    "WebhookDispatcher disabled (enabled=%s, endpoints=%d)",
                    self._enabled, len(self._endpoints),
                )
        except Exception as e:
            logger.error("WebhookDispatcher init failed: %s", e, exc_info=True)
            self._enabled = False
            self._endpoints = []

    @property
    def enabled(self) -> bool:
        return self._enabled and bool(self._endpoints)

    def dispatch(self, signal_payload: dict) -> None:
        """Async-dispatch to all enabled endpoints. Returns immediately.

        signal_payload: the inner `data` field of the new_signal event.
        This wraps it with event/timestamp meta before sending.
        """
        if not self.enabled or not self._executor:
            return

        envelope = {
            "event": "new_signal",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": signal_payload,
        }
        body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")

        for ep in self._endpoints:
            if not ep.enabled:
                continue
            self._executor.submit(self._send, ep, body)

    def _send(self, ep: WebhookEndpoint, body: bytes) -> None:
        """Send one POST. Records success/failure counter + SSE on failure."""
        try:
            headers = {
                "Content-Type": "application/json",
                "X-TGWatcher-Event": "new_signal",
            }
            if ep.secret:
                sig = hmac.new(ep.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
                headers["X-TGWatcher-Signature"] = sig
            t0 = time.time()
            r = requests.post(ep.url, data=body, headers=headers, timeout=self._timeout)
            elapsed_ms = int((time.time() - t0) * 1000)
            if 200 <= r.status_code < 300:
                with self._lock:
                    self._success_counts[ep.url] = self._success_counts.get(ep.url, 0) + 1
                logger.info(
                    "Webhook delivered to %s (HTTP %d, %dms)",
                    ep.url, r.status_code, elapsed_ms,
                )
            else:
                self._record_failure(ep.url, f"HTTP {r.status_code}: {r.text[:200]}")
        except Exception as e:
            self._record_failure(ep.url, f"{type(e).__name__}: {e}")

    def _record_failure(self, url: str, err: str) -> None:
        with self._lock:
            self._fail_counts[url] = self._fail_counts.get(url, 0) + 1
        logger.warning("Webhook delivery to %s failed: %s", url, err)
        # SSE notify (lazy import to avoid circular dependency at module load)
        try:
            from tgwatcher.web.api import push_sse_event
            push_sse_event("webhook_status", {
                "url": url,
                "error": err[:200],
                "fail_count": self._fail_counts.get(url, 0),
            })
        except Exception:
            pass

    def get_status(self) -> dict:
        """Return a status snapshot (for GET /api/webhook/config)."""
        with self._lock:
            return {
                "enabled": self.enabled,
                "endpoints": [
                    {
                        "url": ep.url,
                        "enabled": ep.enabled,
                        "success_count": self._success_counts.get(ep.url, 0),
                        "fail_count": self._fail_counts.get(ep.url, 0),
                    }
                    for ep in self._endpoints
                ],
            }

    def send_test(self, url: str | None = None) -> dict:
        """Send a test payload. If url is None, sends to all enabled endpoints.

        Returns a per-endpoint result dict.
        """
        test_payload = {
            "event": "new_signal",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {
                "message_id": 0,
                "chat_id": 0,
                "chat_title": "[TEST]",
                "text": "Webhook dispatch test from TGWatcher",
                "direction": 0.5,
                "magnitude": 0.5,
                "urgency": 0.5,
                "confidence": 0.5,
                "halflife_min": 60,
                "symbols": ["TEST"],
                "event_type": "other",
                "reasoning": "Manual test triggered from /api/webhook/test",
                "signal_score": 0.5 * 0.5 * 0.5 * (0.5 + 0.5 * 0.5),
                # Mirrors real payload semantics: timestamp + 2 * halflife_min.
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=120)).isoformat(),
                "date": datetime.now(timezone.utc).isoformat(),
            },
        }
        body = json.dumps(test_payload, ensure_ascii=False).encode("utf-8")
        results: list[dict] = []

        targets = [e for e in self._endpoints if e.enabled and (url is None or e.url == url)]
        if not targets:
            return {"status": "no_enabled_endpoints"}

        for ep in targets:
            try:
                headers = {
                    "Content-Type": "application/json",
                    "X-TGWatcher-Event": "new_signal",
                }
                if ep.secret:
                    sig = hmac.new(ep.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
                    headers["X-TGWatcher-Signature"] = sig
                r = requests.post(ep.url, data=body, headers=headers, timeout=self._timeout)
                results.append({
                    "url": ep.url,
                    "status_code": r.status_code,
                    "ok": 200 <= r.status_code < 300,
                    "response": r.text[:200] if r.text else "",
                })
            except Exception as e:
                results.append({
                    "url": ep.url,
                    "status_code": 0,
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                })
        return {"status": "sent", "results": results}

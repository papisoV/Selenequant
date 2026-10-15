from __future__ import annotations

from datetime import datetime

import pytest

from ai_market_pulse import notify
from ai_market_pulse.config import NotificationTarget
from selene_space_types.stock import AssetAnalysis, DailyReport, PriceSnapshot, SignalScore, StockAsset


def _report() -> DailyReport:
    return DailyReport(
        title="Pulse",
        generated_at=datetime(2026, 7, 8, 8, 0),
        timezone="UTC",
        language="en-US",
        market_brief="brief",
        analyses=[
            AssetAnalysis(
                asset=StockAsset(symbol="AAA"),
                snapshot=PriceSnapshot(
                    symbol="AAA",
                    name="AAA Corp",
                    currency="USD",
                    last_close=10,
                    previous_close=9,
                    change_pct=0.1,
                    start_date="2026-01-01",
                    end_date="2026-07-08",
                    rows=2,
                    source="unit",
                ),
                metrics={},
                signal=SignalScore(score=70, stance="watch bullish", risk_level="low", reasons=["r"]),
                news=[],
            )
        ],
    )


def test_compact_message_never_leaks_local_path() -> None:
    message = notify._compact_message(_report(), None)

    assert "Report:" not in message
    assert ".html" not in message
    assert "AAA" in message


def test_compact_message_includes_public_url() -> None:
    message = notify._compact_message(_report(), "https://example.github.io/site/")

    assert "https://example.github.io/site/" in message


def test_compact_message_ignores_non_http_url() -> None:
    message = notify._compact_message(_report(), "/tmp/reports/market-pulse.html")

    assert "Report:" not in message
    assert "/tmp/reports" not in message


def test_send_notifications_posts_webhook_with_url(monkeypatch) -> None:
    sent: dict[str, object] = {}

    def fake_post(url, payload):
        sent["url"] = url
        sent["payload"] = payload

    monkeypatch.setattr(notify, "_post_json", fake_post)
    monkeypatch.setattr(notify.socket, "gethostbyname", lambda host: "93.184.216.34")
    target = NotificationTarget(
        type="webhook", name="hook", enabled=True, settings={"url": "https://hook.example/x"}
    )

    results = notify.send_notifications(
        _report(), [target], html_path=None, report_url="https://pages.example/r"
    )

    assert results and "sent" in results[0]
    assert sent["url"] == "https://hook.example/x"
    assert "https://pages.example/r" in sent["payload"]["text"]


def test_send_notifications_skips_disabled_targets(monkeypatch) -> None:
    monkeypatch.setattr(notify, "_post_json", lambda url, payload: None)
    target = NotificationTarget(
        type="webhook", name="hook", enabled=False, settings={"url": "https://hook.example/x"}
    )

    results = notify.send_notifications(_report(), [target])

    assert results == []


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def test_post_json_builds_post_request_with_json_body(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        captured["method"] = request.get_method()
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["content_type"] = request.get_header("Content-type")
        captured["timeout"] = timeout
        return _FakeResponse(200)

    monkeypatch.setattr(notify, "_urlopen", fake_urlopen)

    notify._post_json("https://hook.example/x", {"text": "hello"})

    assert captured["method"] == "POST"
    assert captured["url"] == "https://hook.example/x"
    assert captured["body"] == b'{"text": "hello"}'
    assert captured["content_type"] == "application/json"


def test_post_json_raises_runtime_error_on_4xx_or_5xx(monkeypatch) -> None:
    monkeypatch.setattr(notify, "_urlopen", lambda request, timeout: _FakeResponse(500))

    with pytest.raises(RuntimeError):
        notify._post_json("https://hook.example/x", {"text": "hello"})


def test_post_json_does_not_raise_below_400(monkeypatch) -> None:
    monkeypatch.setattr(notify, "_urlopen", lambda request, timeout: _FakeResponse(204))

    notify._post_json("https://hook.example/x", {"text": "hello"})


def test_no_redirect_handler_refuses_to_follow_redirects() -> None:
    handler = notify._NoRedirectHandler()

    result = handler.redirect_request(
        req=None, fp=None, code=302, msg="Found", headers={}, newurl="http://169.254.169.254/"
    )

    assert result is None


def test_urlopen_uses_no_redirect_opener(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeOpener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _FakeResponse(200)

    def fake_build_opener(*handlers):
        assert any(isinstance(h, notify._NoRedirectHandler) for h in handlers)
        return _FakeOpener()

    monkeypatch.setattr(notify.urllib.request, "build_opener", fake_build_opener)

    response = notify._urlopen("a-request", timeout=20)

    assert response.status == 200
    assert captured["request"] == "a-request"
    assert captured["timeout"] == 20


def test_assert_safe_url_rejects_loopback() -> None:
    with pytest.raises(ValueError):
        notify._assert_safe_url("http://127.0.0.1/x")


def test_assert_safe_url_rejects_cloud_metadata_address() -> None:
    with pytest.raises(ValueError):
        notify._assert_safe_url("https://169.254.169.254/latest/meta-data")


def test_assert_safe_url_allows_public_https_host(monkeypatch) -> None:
    monkeypatch.setattr(notify.socket, "gethostbyname", lambda host: "93.184.216.34")

    notify._assert_safe_url("https://hooks.slack.com/services/x")


def test_assert_safe_url_rejects_carrier_grade_nat_range(monkeypatch) -> None:
    # 100.64.0.0/10 (RFC 6598 shared address space) is not flagged by
    # is_private, so this specifically guards against a naive private-range
    # blocklist letting it through.
    monkeypatch.setattr(notify.socket, "gethostbyname", lambda host: "100.64.0.5")

    with pytest.raises(ValueError):
        notify._assert_safe_url("https://hook.example/x")


def test_assert_safe_url_rejects_multicast() -> None:
    with pytest.raises(ValueError):
        notify._assert_safe_url("https://224.0.0.1/x")


def test_send_target_rejects_unsafe_webhook_url_before_post(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(notify, "_post_json", lambda url, payload: calls.append((url, payload)))
    target = NotificationTarget(
        type="webhook", name="hook", enabled=True, settings={"url": "http://127.0.0.1/x"}
    )

    with pytest.raises(ValueError):
        notify._send_target(target, "message", _report(), None)

    assert calls == []


def test_send_target_telegram_posts_to_bot_api(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(notify, "_post_json", lambda url, payload: calls.append((url, payload)))
    target = NotificationTarget(
        type="telegram", name="tg", enabled=True, settings={"token": "abc", "chat_id": "1"}
    )

    notify._send_target(target, "message", _report(), None)

    (url, payload) = calls[0]
    assert url == "https://api.telegram.org/botabc/sendMessage"
    assert payload["chat_id"] == "1"
    assert payload["text"] == "message"


def test_send_target_telegram_requires_token_and_chat_id() -> None:
    target = NotificationTarget(type="telegram", name="tg", enabled=True, settings={})

    with pytest.raises(ValueError, match="telegram"):
        notify._send_target(target, "message", _report(), None)


def test_send_target_discord_uses_content_key(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(notify, "_post_json", lambda url, payload: calls.append((url, payload)))
    monkeypatch.setattr(notify.socket, "gethostbyname", lambda host: "93.184.216.34")
    target = NotificationTarget(
        type="discord", name="d", enabled=True, settings={"url": "https://discord.example/x"}
    )

    notify._send_target(target, "message", _report(), None)

    (url, payload) = calls[0]
    assert url == "https://discord.example/x"
    assert payload == {"content": "message"}


def test_send_target_feishu_posts_text_content(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(notify, "_post_json", lambda url, payload: calls.append((url, payload)))
    monkeypatch.setattr(notify.socket, "gethostbyname", lambda host: "93.184.216.34")
    target = NotificationTarget(
        type="feishu", name="f", enabled=True, settings={"url": "https://feishu.example/x"}
    )

    notify._send_target(target, "message", _report(), None)

    (url, payload) = calls[0]
    assert url == "https://feishu.example/x"
    assert payload == {"msg_type": "text", "content": {"text": "message"}}


def test_send_target_wecom_posts_text_content(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(notify, "_post_json", lambda url, payload: calls.append((url, payload)))
    monkeypatch.setattr(notify.socket, "gethostbyname", lambda host: "93.184.216.34")
    target = NotificationTarget(
        type="wecom", name="w", enabled=True, settings={"url": "https://wecom.example/x"}
    )

    notify._send_target(target, "message", _report(), None)

    (url, payload) = calls[0]
    assert url == "https://wecom.example/x"
    assert payload == {"msgtype": "text", "text": {"content": "message"}}


def test_send_target_unsupported_type_raises() -> None:
    target = NotificationTarget(type="carrier-pigeon", name="p", enabled=True, settings={})

    with pytest.raises(ValueError, match="Unsupported notification type"):
        notify._send_target(target, "message", _report(), None)


def test_send_target_email_sends_via_smtp_ssl(monkeypatch) -> None:
    sent: dict[str, object] = {}

    class _FakeSMTP:
        def __init__(self, host, port, context=None):
            sent["host"] = host
            sent["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def login(self, username, password):
            sent["login"] = (username, password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", _FakeSMTP)
    target = NotificationTarget(
        type="email",
        name="mail",
        enabled=True,
        settings={
            "smtp_host": "smtp.example.com",
            "smtp_port": "465",
            "username": "user@example.com",
            "password": "secret",
            "to": "reader@example.com",
        },
    )

    notify._send_target(target, "message", _report(), None)

    assert sent["host"] == "smtp.example.com"
    assert sent["port"] == 465
    assert sent["login"] == ("user@example.com", "secret")
    assert sent["message"]["To"] == "reader@example.com"


def test_send_target_email_requires_host_sender_and_recipients() -> None:
    target = NotificationTarget(type="email", name="mail", enabled=True, settings={})

    with pytest.raises(ValueError, match="email"):
        notify._send_target(target, "message", _report(), None)


def test_setting_resolves_via_env_indirection(monkeypatch) -> None:
    monkeypatch.setenv("AI_MARKET_PULSE_TEST_WEBHOOK_TOKEN", "from-env")

    value = notify.resolve_setting({"token_env": "AI_MARKET_PULSE_TEST_WEBHOOK_TOKEN"}, "token", "token_env")

    assert value == "from-env"

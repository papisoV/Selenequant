from __future__ import annotations

from datetime import datetime

import pytest

from ai_market_pulse import notify
from ai_market_pulse.noise import NoiseStore, NotificationNoise
from selene_space_types.stock import (
    AssetAnalysis,
    DailyReport,
    PriceSnapshot,
    SignalScore,
    StockAsset,
)


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
                signal=SignalScore(
                    score=70, stance="watch bullish", risk_level="low", reasons=["r"]
                ),
                news=[],
            )
        ],
    )


def _capture_post(monkeypatch) -> list[tuple[str, str, dict]]:
    calls: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        notify, "_post_json", lambda url, payload: calls.append(("json", url, payload))
    )
    monkeypatch.setattr(
        notify, "_post_form", lambda url, fields: calls.append(("form", url, fields))
    )
    monkeypatch.setattr(notify.socket, "gethostbyname", lambda host: "93.184.216.34")
    return calls


# ---------------------------------------------------------------------------
# Per-channel contract tests (payload shapes per vendor public API docs)
# ---------------------------------------------------------------------------


def test_send_target_dingtalk_markdown_payload(monkeypatch) -> None:
    calls = _capture_post(monkeypatch)
    target = notify.NotificationTarget(
        type="dingtalk",
        name="dt",
        enabled=True,
        settings={"url": "https://oapi.dingtalk.com/robot/send?access_token=z"},
    )

    notify._send_target(target, "hello", _report(), None)

    _, url, payload = calls[0]
    assert url.startswith("https://oapi.dingtalk.com/robot/send?access_token=z")
    assert payload["msgtype"] == "markdown"
    assert payload["markdown"]["title"]
    assert payload["markdown"]["text"] == "hello"


def test_send_target_dingtalk_secret_signature_params(monkeypatch) -> None:
    calls = _capture_post(monkeypatch)
    target = notify.NotificationTarget(
        type="dingtalk",
        name="dt",
        enabled=True,
        settings={"url": "https://oapi.dingtalk.com/robot/send", "secret": "SECtop"},
    )

    notify._send_target(target, "hello", _report(), None)

    _, url, _ = calls[0]
    assert "timestamp=" in url
    assert "sign=" in url
    # HMAC-SHA256 of f"{timestamp}\n{secret}" base64+urlencoded.
    import base64 as b64
    import hashlib
    import hmac
    import time
    import urllib.parse

    timestamp = url.split("timestamp=")[1].split("&")[0]
    string_to_sign = f"{timestamp}\nSECtop".encode("utf-8")
    expected = urllib.parse.quote_plus(
        b64.b64encode(hmac.new(b"SECtop", string_to_sign, hashlib.sha256).digest())
    )
    assert url.split("sign=")[1] == expected
    assert abs(int(timestamp) - round(time.time() * 1000)) < 60_000


def test_send_target_serverchan3_uses_sctapi_endpoint(monkeypatch) -> None:
    calls = _capture_post(monkeypatch)
    target = notify.NotificationTarget(
        type="serverchan3", name="sc", enabled=True, settings={"token": "SCT123"}
    )

    notify._send_target(target, "hello", _report(), None)

    _, url, payload = calls[0]
    assert url == "https://sctapi.ftqq.com/SCT123.send"
    assert payload["title"]
    assert payload["desp"] == "hello"
    assert payload["options"] == {}


def test_send_target_serverchan_sctp_key_uses_ft07_mirror(monkeypatch) -> None:
    calls = _capture_post(monkeypatch)
    target = notify.NotificationTarget(
        type="serverchan", name="sc", enabled=True, settings={"token": "sctp666tABC"}
    )

    notify._send_target(target, "hello", _report(), None)

    _, url, _ = calls[0]
    assert url == "https://666.push.ft07.com/send/sctp666tABC.send"


def test_send_target_pushplus_payload(monkeypatch) -> None:
    calls = _capture_post(monkeypatch)
    target = notify.NotificationTarget(
        type="pushplus",
        name="pp",
        enabled=True,
        settings={"token": "tok", "topic": "grp"},
    )

    notify._send_target(target, "hello", _report(), None)

    _, url, payload = calls[0]
    assert url == "https://www.pushplus.plus/send"
    assert payload == {
        "token": "tok",
        "title": payload["title"],
        "content": "hello",
        "template": "markdown",
        "topic": "grp",
    }


def test_send_target_ntfy_bearer_header_present(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        captured["auth"] = request.get_header("Authorization")
        captured["url"] = request.full_url
        captured["body"] = request.data

        class _Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None

        return _Resp()

    monkeypatch.setattr(notify, "_urlopen", fake_urlopen)
    monkeypatch.setattr(notify.socket, "gethostbyname", lambda host: "93.184.216.34")
    target = notify.NotificationTarget(
        type="ntfy",
        name="n",
        enabled=True,
        settings={"url": "https://ntfy.sh/my-topic", "token": "tkX"},
    )

    notify._send_target(target, "hello", _report(), None)

    assert captured["auth"] == "Bearer tkX"
    # ntfy's JSON publish API is POSTed to the server ROOT with the topic in
    # the body; POSTing JSON at the topic URL delivers the JSON text verbatim.
    assert captured["url"] == "https://ntfy.sh"
    assert b'"topic": "my-topic"' in captured["body"]  # type: ignore[operator]
    assert b'"markdown": true' in captured["body"]  # type: ignore[operator]


def test_ntfy_endpoint_splits_root_and_topic() -> None:
    assert notify._ntfy_endpoint("https://ntfy.sh/my-topic") == (
        "https://ntfy.sh",
        "my-topic",
    )
    # Self-hosted ntfy under a sub-path keeps the prefix in the server root.
    assert notify._ntfy_endpoint("https://host.example/ntfy/topic-a") == (
        "https://host.example/ntfy",
        "topic-a",
    )
    # A trailing slash and a percent-encoded topic both normalize.
    assert notify._ntfy_endpoint("https://ntfy.sh/space%20topic/") == (
        "https://ntfy.sh",
        "space topic",
    )


def test_ntfy_endpoint_rejects_topicless_url() -> None:
    # A bare server root has no topic; publishing would 404 against ntfy.
    with pytest.raises(ValueError):
        notify._ntfy_endpoint("https://ntfy.sh")


def test_send_target_gotify_message_endpoint_and_key_header(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["key"] = request.get_header("X-gotify-key")
        captured["body"] = request.data

        class _Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None

        return _Resp()

    monkeypatch.setattr(notify, "_urlopen", fake_urlopen)
    monkeypatch.setattr(notify.socket, "gethostbyname", lambda host: "93.184.216.34")
    target = notify.NotificationTarget(
        type="gotify",
        name="g",
        enabled=True,
        settings={"url": "https://push.example.com", "token": "gkey"},
    )

    notify._send_target(target, "hello", _report(), None)

    assert captured["url"] == "https://push.example.com/message"
    assert captured["key"] == "gkey"
    assert b"text/markdown" in captured["body"]  # type: ignore[operator]


def test_send_target_pushover_uses_form_encoding(monkeypatch) -> None:
    calls = _capture_post(monkeypatch)
    target = notify.NotificationTarget(
        type="pushover",
        name="po",
        enabled=True,
        settings={"user_key": "uk", "token": "at"},
    )

    notify._send_target(target, "hello", _report(), None)

    kind, url, fields = calls[0]
    assert kind == "form"
    assert url == "https://api.pushover.net/1/messages.json"
    assert fields["user"] == "uk"
    assert fields["token"] == "at"
    assert fields["message"] == "hello"


def test_send_target_text_channels(monkeypatch) -> None:
    # _send_target_text must route the 6 new channels too (grid/trade alerts).
    calls = _capture_post(monkeypatch)
    dingtalk = notify.NotificationTarget(
        type="dingtalk",
        name="dt",
        enabled=True,
        settings={"url": "https://oapi.dingtalk.com/robot/send?access_token=z"},
    )
    pushover = notify.NotificationTarget(
        type="pushover",
        name="po",
        enabled=True,
        settings={"user_key": "uk", "token": "at"},
    )

    notify._send_target_text(dingtalk, "alert!")
    notify._send_target_text(pushover, "alert!")

    dingtalk_calls = [c for c in calls if c[1].startswith("https://oapi.dingtalk.com")]
    assert dingtalk_calls and dingtalk_calls[0][2]["msgtype"] == "text"
    assert dingtalk_calls[0][2]["text"]["content"] == "alert!"
    pushover_calls = [c for c in calls if c[0] == "form"]
    assert pushover_calls and pushover_calls[0][2]["message"] == "alert!"


def test_send_target_text_skips_unconfigured_new_channels(monkeypatch) -> None:
    calls = _capture_post(monkeypatch)
    target = notify.NotificationTarget(
        type="pushover", name="po", enabled=True, settings={}
    )

    notify._send_target_text(target, "alert!")

    assert calls == []


# ---------------------------------------------------------------------------
# Config validation and SSRF guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "settings"),
    [
        ("dingtalk", {}),
        ("serverchan3", {}),
        ("pushplus", {}),
        ("ntfy", {}),
        ("gotify", {"url": "https://push.example.com"}),
        ("pushover", {}),
    ],
)
def test_new_channels_require_configuration(kind: str, settings: dict) -> None:
    target = notify.NotificationTarget(
        type=kind, name="x", enabled=True, settings=settings
    )

    with pytest.raises(ValueError):
        notify._send_target(target, "hello", _report(), None)


@pytest.mark.parametrize(
    ("kind", "settings"),
    [
        ("dingtalk", {"url": "https://127.0.0.1/x"}),
        ("serverchan3", {"token": "sct-loopback"}),
        ("ntfy", {"url": "https://127.0.0.1/x"}),
        ("gotify", {"url": "https://127.0.0.1/x", "token": "k"}),
        ("pushover", {"user_key": "u", "token": "t"}),
        ("pushplus", {"token": "t"}),
    ],
)
def test_new_channels_reject_unsafe_urls_before_send(
    monkeypatch, kind: str, settings: dict
) -> None:
    # _assert_safe_url runs before any request; no post helper should fire.
    calls = _capture_post(monkeypatch)
    monkeypatch.setattr(notify.socket, "gethostbyname", lambda host: "127.0.0.1")
    target = notify.NotificationTarget(
        type=kind, name="x", enabled=True, settings=settings
    )

    with pytest.raises(ValueError):
        notify._send_target(target, "hello", _report(), None)

    assert calls == []


# ---------------------------------------------------------------------------
# Noise guard integration
# ---------------------------------------------------------------------------


def _webhook_target() -> "notify.NotificationTarget":
    return notify.NotificationTarget(
        type="webhook",
        name="hook",
        enabled=True,
        settings={"url": "https://hook.example/x"},
    )


def test_send_notifications_noise_suppresses_second_identical_report(
    monkeypatch, tmp_path
) -> None:
    calls = _capture_post(monkeypatch)
    noise = NotificationNoise(NoiseStore(tmp_path / "noise.db"))
    target = _webhook_target()

    first = notify.send_notifications(_report(), [target], noise=noise)
    second = notify.send_notifications(_report(), [target], noise=noise)

    assert first == ["webhook:hook sent"]
    assert second == ["webhook:hook suppressed: dedup"]
    assert len(calls) == 1


def test_send_notifications_without_noise_unchanged(monkeypatch) -> None:
    calls = _capture_post(monkeypatch)
    target = _webhook_target()

    results = notify.send_notifications(_report(), [target])

    assert results == ["webhook:hook sent"]
    assert len(calls) == 1


def test_send_notifications_noise_dedup_key_is_per_type(monkeypatch, tmp_path) -> None:
    # Brief semantics: dedup_key = f"{target.type}:{content-hash}" -- keyed by
    # channel type, not target name, so two webhook targets receiving the
    # identical report share one dedup key and the second is suppressed.
    calls = _capture_post(monkeypatch)
    noise = NotificationNoise(NoiseStore(tmp_path / "noise.db"))
    hook1 = _webhook_target()
    hook2 = notify.NotificationTarget(
        type="webhook",
        name="hook2",
        enabled=True,
        settings={"url": "https://hook.example/y"},
    )

    results1 = notify.send_notifications(_report(), [hook1], noise=noise)
    results2 = notify.send_notifications(_report(), [hook2], noise=noise)

    assert results1 == ["webhook:hook sent"]
    assert results2 == ["webhook:hook2 suppressed: dedup"]
    assert len(calls) == 1


def test_send_notifications_noise_ttl_from_target_settings(
    monkeypatch, tmp_path
) -> None:
    calls = _capture_post(monkeypatch)
    noise = NotificationNoise(NoiseStore(tmp_path / "noise.db"))
    target = notify.NotificationTarget(
        type="webhook",
        name="hook",
        enabled=True,
        settings={"url": "https://hook.example/x", "dedup_ttl_seconds": 600},
    )

    notify.send_notifications(_report(), [target], noise=noise)
    second = notify.send_notifications(_report(), [target], noise=noise)

    assert second == ["webhook:hook suppressed: dedup"]
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# send_to_targets noise gating (alert/dispatch path)
# ---------------------------------------------------------------------------


def test_send_to_targets_dedups_identical_text(monkeypatch, tmp_path) -> None:
    calls = _capture_post(monkeypatch)
    noise = NotificationNoise(NoiseStore(tmp_path / "noise.db"))
    target = _webhook_target()

    first = notify.send_to_targets([target], "alert: breach", noise=noise)
    second = notify.send_to_targets([target], "alert: breach", noise=noise)

    assert first == ["webhook:hook sent"]
    assert second == ["webhook:hook suppressed: dedup"]
    assert len(calls) == 1


def test_send_to_targets_allows_different_text(monkeypatch, tmp_path) -> None:
    calls = _capture_post(monkeypatch)
    noise = NotificationNoise(NoiseStore(tmp_path / "noise.db"))
    target = _webhook_target()

    first = notify.send_to_targets([target], "alert: breach", noise=noise)
    second = notify.send_to_targets([target], "alert: recovered", noise=noise)

    assert first == ["webhook:hook sent"]
    assert second == ["webhook:hook sent"]
    assert len(calls) == 2


def test_send_to_targets_without_noise_unchanged(monkeypatch) -> None:
    calls = _capture_post(monkeypatch)
    target = _webhook_target()

    results = notify.send_to_targets([target], "alert: breach")

    assert results == ["webhook:hook sent"]
    assert len(calls) == 1


def test_send_to_targets_noise_evaluation_failure_fails_open(
    monkeypatch, tmp_path
) -> None:
    calls = _capture_post(monkeypatch)

    class BrokenNoise:
        def evaluate(self, **_kwargs):
            raise RuntimeError("store exploded")

    target = _webhook_target()

    results = notify.send_to_targets([target], "alert: breach", noise=BrokenNoise())

    # Fail-open: the evaluation error must not block delivery.
    assert results == ["webhook:hook sent"]
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Webhook dialect adaptation (absorbed from local-api notify_channels; without
# this a `type: webhook` target sent {"text": ...} to feishu/wecom/dingtalk,
# which those vendors reject).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://open.feishu.cn/open-apis/bot/v2/hook/x", "feishu"),
        ("https://foo.larksuite.com/hook/x", "feishu"),
        ("https://oapi.dingtalk.com/robot/send?access_token=z", "dingtalk"),
        ("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k", "wecom"),
        ("https://work.weixin.qq.com/cgi-bin/webhook/send?key=k", "wecom"),
        ("https://hooks.slack.com/services/T/B/X", "slack"),
        ("https://discord.com/api/webhooks/1/abc", "discord"),
        ("https://example.com/whatever", "generic"),
    ],
)
def test_detect_webhook_dialect(url, expected) -> None:
    assert notify.detect_webhook_dialect(url) == expected


def test_webhook_feishu_gets_vendor_payload_not_generic_text(monkeypatch) -> None:
    calls = _capture_post(monkeypatch)
    target = notify.NotificationTarget(
        type="webhook",
        name="fs",
        enabled=True,
        settings={"url": "https://open.feishu.cn/open-apis/bot/v2/hook/x"},
    )

    notify._send_target_text(target, "hi")

    _, url, payload = calls[0]
    # feishu requires msg_type/content, NOT {"text": ...}
    assert payload["msg_type"] == "text"
    assert payload["content"]["text"] == "hi"
    assert "text" not in payload


def test_webhook_dingtalk_gets_msgtype_payload(monkeypatch) -> None:
    calls = _capture_post(monkeypatch)
    target = notify.NotificationTarget(
        type="webhook",
        name="dt",
        enabled=True,
        settings={"url": "https://oapi.dingtalk.com/robot/send?access_token=z"},
    )

    notify._send_target_text(target, "hi")

    _, _, payload = calls[0]
    assert payload["msgtype"] == "text"


def test_webhook_feishu_with_secret_is_signed_in_body(monkeypatch) -> None:
    calls = _capture_post(monkeypatch)
    target = notify.NotificationTarget(
        type="webhook",
        name="fs",
        enabled=True,
        settings={
            "url": "https://open.feishu.cn/open-apis/bot/v2/hook/x",
            "secret": "SEC",
        },
    )

    notify._send_target_text(target, "hi")

    _, _, payload = calls[0]
    assert "timestamp" in payload and "sign" in payload
    assert payload["msg_type"] == "text"


def test_webhook_generic_stays_plain_text(monkeypatch) -> None:
    calls = _capture_post(monkeypatch)
    target = notify.NotificationTarget(
        type="webhook",
        name="gen",
        enabled=True,
        settings={"url": "https://example.com/hook"},
    )

    notify._send_target_text(target, "hi")

    _, _, payload = calls[0]
    assert payload == {"text": "hi"}


def test_webhook_session_and_explicit_kinds_unaffected(monkeypatch) -> None:
    # Explicit slack/discord types keep their original shape.
    calls = _capture_post(monkeypatch)
    notify._send_target_text(
        notify.NotificationTarget(
            type="discord", name="d", enabled=True,
            settings={"url": "https://discord.com/api/webhooks/1/a"},
        ),
        "hi",
    )
    _, _, payload = calls[0]
    assert payload == {"content": "hi"}


def test_send_to_targets_sends_only_to_given_list(monkeypatch) -> None:
    calls = _capture_post(monkeypatch)
    targets = [
        notify.NotificationTarget(
            type="webhook", name="n1", enabled=True,
            settings={"url": "https://example.com/h1"},
        ),
        notify.NotificationTarget(
            type="webhook", name="disabled", enabled=False,
            settings={"url": "https://example.com/h2"},
        ),
    ]

    results = notify.send_to_targets(targets, "hello")

    # Disabled target skipped; only the enabled one was sent.
    assert len(calls) == 1
    assert results == ["webhook:n1 sent"]

"""通知壳填真批:notify_channels 五方言 + channels 持久化/脱敏 + exchange probe/hint."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from app.core.dependencies import get_settings_service
from app.core.settings_service import SettingsService
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from app.core.notify_channels import (
    adapt_payload_for_dialect,
    detect_webhook_dialect,
    save_channels,
    _check_vendor_response,
    _dingtalk_signed_url,
    _feishu_sign,
)
from selene_space_security.secrets import SecretRef, SecretSource, SecretResolutionError


class _Vault:
    """In-memory secret vault standing in for the keyring-backed manager."""

    def __init__(self) -> None:
        self._vault: dict[str, str] = {}

    @staticmethod
    def _ref_id(ref) -> str:
        return f"{ref.provider}:{ref.id}"

    def keyring_available(self) -> bool:
        return True

    def keyring_backend_info(self) -> dict[str, object]:
        return {
            "available": True,
            "encryption_active": False,
            "backend": "stub",
            "is_secure_backend": True,
        }

    async def exists(self, ref) -> bool:
        return self._ref_id(ref) in self._vault

    async def resolve(self, ref):
        slot = self._ref_id(ref)
        if slot not in self._vault:
            raise SecretResolutionError(f"not found: {slot}")

        class _Unlocked:
            def __init__(self, value: str):
                self._value = value

            def use(self_):
                return self_._value

        return _Unlocked(self._vault[slot])

    async def store(self, ref, value: str) -> None:
        self._vault[self._ref_id(ref)] = value

    async def delete(self, ref) -> None:
        self._vault.pop(self._ref_id(ref), None)


# ---------------------------------------------------------------------------
# 方言识别
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://discord.com/api/webhooks/123/abc", "discord"),
        ("https://open.feishu.cn/open-apis/bot/v2/hook/xxx", "feishu"),
        ("https://xxx.larksuite.com/open-apis/bot/v2/hook/xxx", "feishu"),
        ("https://oapi.dingtalk.com/robot/send?access_token=x", "dingtalk"),
        ("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x", "wecom"),
        ("https://hooks.slack.com/services/T00/B00/xxx", "slack"),
        ("https://example.com/hook", "generic"),
        ("", "generic"),
    ],
)
def test_detect_webhook_dialect(url: str, expected: str) -> None:
    assert detect_webhook_dialect(url) == expected


def test_adapt_payload_shapes() -> None:
    assert adapt_payload_for_dialect("feishu", "hi") == {
        "msg_type": "text",
        "content": {"text": "hi"},
    }
    assert adapt_payload_for_dialect("dingtalk", "hi") == {
        "msgtype": "text",
        "text": {"content": "hi"},
    }
    assert adapt_payload_for_dialect("wecom", "hi") == {
        "msgtype": "text",
        "text": {"content": "hi"},
    }
    assert adapt_payload_for_dialect("slack", "hi") == {"text": "hi"}
    assert adapt_payload_for_dialect("generic", "hi") == {"text": "hi"}


# ---------------------------------------------------------------------------
# 签名协议(对照 QD 行为契约)
# ---------------------------------------------------------------------------


def test_feishu_sign_matches_hmac_spec() -> None:
    secret = "s3cret"
    ts = "1700000000"
    expected = base64.b64encode(
        hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
    ).decode()
    assert _feishu_sign(secret, ts) == expected


def test_dingtalk_signed_url_appends_timestamp_and_sign() -> None:
    secret = "SECxxxxxxxx"
    url = "https://oapi.dingtalk.com/robot/send?access_token=tok"
    signed = _dingtalk_signed_url(url, secret)
    q = parse_qs(urlsplit(signed).query)
    assert "timestamp" in q and "sign" in q
    assert q["access_token"] == ["tok"]
    # sign = urlencoded(base64(HMAC-SHA256(key=secret, msg=f"{ts}\n{secret}")))
    expected = base64.b64encode(
        hmac.new(
            secret.encode(), f"{q['timestamp'][0]}\n{secret}".encode(), hashlib.sha256
        ).digest()
    ).decode()
    assert q["sign"][0] == expected


def test_check_vendor_response_codes() -> None:
    # 2xx + errcode 0 → ok
    ok, err = _check_vendor_response(
        "dingtalk", 200, json.dumps({"errcode": 0, "errmsg": "ok"})
    )
    assert ok and err == ""
    # 2xx + errcode 非 0 → vendor 错误带 errmsg
    ok, err = _check_vendor_response(
        "feishu", 200, json.dumps({"code": 19021, "msg": "sign match fail"})
    )
    assert not ok and "sign match fail" in err
    # 非 2xx → http 错误
    ok, err = _check_vendor_response("slack", 404, "not found")
    assert not ok and err.startswith("http_404")
    # slack 不查 vendor code
    ok, _ = _check_vendor_response("slack", 200, json.dumps({"ok": True}))
    assert ok


# ---------------------------------------------------------------------------
# 渠道持久化 + API 脱敏
# ---------------------------------------------------------------------------


@pytest.fixture
def channels_file(tmp_path, monkeypatch):
    p = tmp_path / "notification_channels.json"
    monkeypatch.setattr("app.core.notify_channels.channels_path", lambda: p)
    return p


def _wire_notify_app():
    app = create_app()
    return app


@pytest.mark.asyncio
async def test_channel_persist_and_mask(channels_file, tmp_path, monkeypatch):
    app = _wire_notify_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # create → 磁盘落明文
        resp = await c.post(
            "/api/v1/notifications/channels",
            json={
                "type": "webhook",
                "name": "feishu机器人",
                "config": {
                    "url": "https://open.feishu.cn/hook/x",
                    "secret": "topsecret",
                },
            },
        )
        assert resp.status_code == 200
        cid = resp.json()["data"]["id"]

        saved = json.loads(channels_file.read_text("utf-8"))
        assert saved[0]["config"]["secret"] == "topsecret"  # 磁盘存明文

        # list → secret/url 脱敏
        resp = await c.get("/api/v1/notifications/channels")
        cfg = resp.json()["data"]["channels"][0]["config"]
        assert cfg["secret"] == "***"
        assert cfg["url"] == "***"

        # test 真发(用假 URL,允许失败但链路要通)
        resp = await c.post(f"/api/v1/notifications/channels/{cid}/test")
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["tested"] is False  # example.com 假地址发不通
        assert body["status"]

        # soft delete → deleted 标记落盘
        resp = await c.delete(f"/api/v1/notifications/channels/{cid}")
        assert resp.status_code == 200
        assert json.loads(channels_file.read_text("utf-8"))[0]["deleted"] is True


@pytest.mark.asyncio
async def test_channel_test_not_found_and_disabled(channels_file):
    app = _wire_notify_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/v1/notifications/channels/nope/test")
        assert resp.status_code == 404

        save_channels(
            [
                {
                    "id": "c1",
                    "type": "webhook",
                    "name": "x",
                    "config": {"url": "https://e.com"},
                    "enabled": False,
                }
            ]
        )
        resp = await c.post("/api/v1/notifications/channels/c1/test")
        assert resp.status_code == 400  # VALIDATION_ERROR → HTTP 400
        assert "disabled" in resp.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_preferences_persist_and_validate(channels_file, tmp_path):
    app = _wire_notify_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # 引用未知渠道 → 404
        resp = await c.put(
            "/api/v1/notifications/preferences",
            json={"items": [{"event_type": "order_filled", "channel_ids": ["ghost"]}]},
        )
        assert resp.status_code == 404  # 未知渠道引用被拒
        # 走正常链:先建渠道再挂偏好
        resp = await c.post(
            "/api/v1/notifications/channels",
            json={"type": "webhook", "name": "w", "config": {"url": "https://e.com/h"}},
        )
        cid = resp.json()["data"]["id"]
        resp = await c.put(
            "/api/v1/notifications/preferences",
            json={"items": [{"event_type": "order_filled", "channel_ids": [cid]}]},
        )
        assert resp.status_code == 200
        resp = await c.get("/api/v1/notifications/preferences")
        assert resp.json()["data"]["preferences"]["order_filled"] == [cid]


# ---------------------------------------------------------------------------
# 件3:api_key_hint + exchange probe
# ---------------------------------------------------------------------------


def test_api_key_hint_caliber() -> None:
    from app.core.settings_service import _api_key_hint

    assert _api_key_hint("") == ""
    assert _api_key_hint("short") == "sh***"
    assert _api_key_hint("abcd1234efgh9012") == "abcd...9012"


def _wire_settings_app(tmp_path, vault):
    service = SettingsService(
        config_path=tmp_path / "settings.json", secret_manager=vault
    )
    app = create_app()
    app.dependency_overrides[get_settings_service] = lambda: service
    return app, service


@pytest.mark.asyncio
async def test_get_settings_returns_api_key_hint(tmp_path):
    vault = _Vault()
    app, service = _wire_settings_app(tmp_path, vault)
    await vault.store(
        SecretRef(
            source=SecretSource.KEYRING, provider="selenespace.exchange", id="api_key"
        ),
        "ABCD1234EFGH9012",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/settings")
    assert resp.status_code == 200
    ex = resp.json()["data"]["exchange"]
    assert ex["api_key_hint"] == "ABCD...9012"
    assert ex["api_key_masked"] == "••••••••"


@pytest.mark.asyncio
async def test_exchange_probe_requires_credentials(tmp_path):
    app, _ = _wire_settings_app(tmp_path, _Vault())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/v1/settings/exchange/test")
    # 未配置凭证 → VALIDATION_ERROR
    assert resp.status_code in (200, 400)
    body = resp.json()
    if resp.status_code == 200:
        assert body["error"] is not None

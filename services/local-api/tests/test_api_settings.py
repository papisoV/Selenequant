"""Settings endpoints: masking, secret storage, crypto round-trips, flags."""

from __future__ import annotations

import base64

import pytest
from app.core.crypto import ENCRYPTED_PREFIX, KeyPairManager
from app.core.dependencies import get_key_pair_manager, get_settings_service
from app.core.settings_service import SettingsService
from app.main import create_app
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from httpx import ASGITransport, AsyncClient

from selene_space_security.secrets import SecretResolutionError

SETTINGS_URL = "/api/v1/settings"


class _Vault:
    """In-memory secret vault standing in for the keyring-backed manager."""

    def __init__(self, keyring_available: bool = True) -> None:
        self._open = keyring_available
        self._vault: dict[str, str] = {}

    @staticmethod
    def _ref_id(ref) -> str:
        return f"{ref.provider}:{ref.id}"

    def keyring_available(self) -> bool:
        return self._open

    def keyring_backend_info(self) -> dict[str, object]:
        return {
            "available": self._open,
            "encryption_active": False,
            "backend": "stub" if self._open else "unavailable",
            "is_secure_backend": self._open,
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
        if not self._open:
            raise SecretResolutionError("keyring unavailable")
        self._vault[self._ref_id(ref)] = value

    async def delete(self, ref) -> None:
        self._vault.pop(self._ref_id(ref), None)


def _wire_app(tmp_path, vault=None, **service_kwargs):
    """Build (app, vault); registers dependency overrides for the service."""
    vault = vault or _Vault()
    service_kwargs.setdefault("secret_manager", vault)
    service = SettingsService(config_path=tmp_path / "settings.json", **service_kwargs)
    app = create_app()
    app.dependency_overrides[get_settings_service] = lambda: service
    kpm = service_kwargs.get("key_pair_manager")
    if kpm is not None:
        app.dependency_overrides[get_key_pair_manager] = lambda: kpm
    return app, vault


async def _call(app, method: str, path: str, **kw):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await getattr(c, method)(path, **kw)


def _llm_payload(**llm_fields) -> dict:
    return {"llm": llm_fields}


def _exchange_payload(**exchange_fields) -> dict:
    return {"exchange": exchange_fields}


@pytest.mark.asyncio
async def test_get_masks_secret_state(tmp_path):
    app, _ = _wire_app(tmp_path)
    resp = await _call(app, "get", SETTINGS_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["general"]["api_url"] == "http://localhost:8088"
    assert body["data"]["exchange"]["market_type"] == "spot"
    assert body["data"]["llm"]["api_key_configured"] is False
    assert "request_id" in body["meta"]


@pytest.mark.asyncio
async def test_put_stores_and_masks_llm_key(tmp_path):
    app, _ = _wire_app(tmp_path)
    resp = await _call(
        app,
        "put",
        SETTINGS_URL,
        json={
            "general": {"api_url": "http://127.0.0.1:8088"},
            "exchange": {"provider": "okx", "market_type": "futures"},
            "llm": {
                "model": "claude-opus-4-6",
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-test-secret",
                "provider": "openai",
            },
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["general"]["api_url"] == "http://127.0.0.1:8088"
    assert body["data"]["exchange"]["provider"] == "okx"
    assert body["data"]["exchange"]["market_type"] == "futures"
    assert body["data"]["llm"]["api_key_configured"] is True
    assert body["data"]["llm"]["api_key_masked"] == "••••••••"


@pytest.mark.asyncio
async def test_clear_api_key_removes_configured_flag(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(app, "put", SETTINGS_URL, json=_llm_payload(api_key="sk-test-secret"))
    wiped = await _call(app, "put", SETTINGS_URL, json=_llm_payload(clear_api_key=True))

    assert wiped.status_code == 200
    assert wiped.json()["data"]["llm"]["api_key_configured"] is False


@pytest.mark.asyncio
async def test_invalid_exchange_provider_is_refused(tmp_path):
    app, _ = _wire_app(tmp_path)
    bad = await _call(
        app,
        "put",
        SETTINGS_URL,
        json=_exchange_payload(provider="bybit", market_type="perp"),
    )

    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_store_without_keyring_yields_503(tmp_path):
    app, _ = _wire_app(tmp_path, vault=_Vault(keyring_available=False))
    denied = await _call(
        app, "put", SETTINGS_URL, json=_llm_payload(api_key="sk-test-secret")
    )

    assert denied.status_code == 503
    assert denied.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


# --- public-key endpoint -----------------------------------------------------


@pytest.mark.asyncio
async def test_public_key_endpoint_serves_rsa_jwk(tmp_path):
    app, _ = _wire_app(tmp_path, key_pair_manager=KeyPairManager())
    jwk_resp = await _call(app, "get", f"{SETTINGS_URL}/public-key")

    assert jwk_resp.status_code == 200
    jwk = jwk_resp.json()["data"]
    assert jwk["kty"] == "RSA"
    assert jwk["alg"] == "RSA-OAEP-256"
    assert "n" in jwk
    assert "e" in jwk


# --- encrypted secret round-trip ---------------------------------------------


def _seal(kpm: KeyPairManager, plaintext: str) -> str:
    """RSA-OAEP encrypt then prefix — mirrors the browser client."""
    pub = kpm._public_key  # noqa: SLF001
    ciphertext = pub.encrypt(
        plaintext.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return ENCRYPTED_PREFIX + base64.b64encode(ciphertext).decode("ascii")


@pytest.mark.asyncio
async def test_encrypted_llm_key_lands_decrypted_in_store(tmp_path):
    kpm = KeyPairManager()
    app, vault = _wire_app(tmp_path, key_pair_manager=kpm)

    sealed = _seal(kpm, "sk-real-secret-key")
    put = await _call(app, "put", SETTINGS_URL, json=_llm_payload(api_key=sealed))

    assert put.status_code == 200
    assert put.json()["data"]["llm"]["api_key_configured"] is True
    assert vault._vault.get("selenespace.llm:api_key") == "sk-real-secret-key"


@pytest.mark.asyncio
async def test_encrypted_exchange_credentials_both_land(tmp_path):
    kpm = KeyPairManager()
    app, vault = _wire_app(tmp_path, key_pair_manager=kpm)

    pair = {
        name: _seal(kpm, value)
        for name, value in (("api_key", "my-api-key"), ("api_secret", "my-api-secret"))
    }
    put = await _call(app, "put", SETTINGS_URL, json=_exchange_payload(**pair))

    assert put.status_code == 200
    body = put.json()
    assert body["data"]["exchange"]["api_key_configured"] is True
    assert body["data"]["exchange"]["api_secret_configured"] is True
    assert vault._vault["selenespace.exchange:api_key"] == "my-api-key"
    assert vault._vault["selenespace.exchange:api_secret"] == "my-api-secret"


@pytest.mark.asyncio
async def test_plaintext_keys_still_accepted(tmp_path):
    """Backward compat: plaintext secrets (no enc: prefix) still work."""
    app, vault = _wire_app(tmp_path)
    plain = await _call(
        app, "put", SETTINGS_URL, json=_llm_payload(api_key="sk-plain-key")
    )

    assert plain.status_code == 200
    assert vault._vault["selenespace.llm:api_key"] == "sk-plain-key"


# --- testnet flag (P1-4 Stage C prep) ----------------------------------------


@pytest.mark.asyncio
async def test_testnet_defaults_to_safe_true(tmp_path):
    """Fresh settings: exchange.testnet defaults to True (safe default)."""
    app, _ = _wire_app(tmp_path)
    fresh = await _call(app, "get", SETTINGS_URL)

    assert fresh.status_code == 200
    assert fresh.json()["data"]["exchange"]["testnet"] is True


@pytest.mark.asyncio
async def test_testnet_flag_round_trips(tmp_path):
    """PUT testnet=False → GET returns False; PUT True → returns True."""
    app, _ = _wire_app(tmp_path)
    off = await _call(app, "put", SETTINGS_URL, json=_exchange_payload(testnet=False))
    assert off.status_code == 200
    assert off.json()["data"]["exchange"]["testnet"] is False

    read_back = await _call(app, "get", SETTINGS_URL)
    assert read_back.json()["data"]["exchange"]["testnet"] is False

    on = await _call(app, "put", SETTINGS_URL, json=_exchange_payload(testnet=True))
    assert on.json()["data"]["exchange"]["testnet"] is True


@pytest.mark.asyncio
async def test_non_bool_testnet_is_rejected(tmp_path):
    """Non-coercible testnet value must be rejected at the API boundary."""
    app, _ = _wire_app(tmp_path)
    junk = await _call(
        app, "put", SETTINGS_URL, json=_exchange_payload(testnet="not-a-bool")
    )

    assert junk.status_code == 422
    assert junk.json()["error"]["code"] == "VALIDATION_ERROR"


# --- execution_mode (P1-4 Stage B) --------------------------------------------


@pytest.mark.asyncio
async def test_execution_mode_defaults_to_paper(tmp_path):
    """Fresh settings: general.execution_mode defaults to 'paper' (safe)."""
    app, _ = _wire_app(tmp_path)
    fresh = await _call(app, "get", SETTINGS_URL)

    assert fresh.status_code == 200
    assert fresh.json()["data"]["general"]["execution_mode"] == "paper"


@pytest.mark.asyncio
async def test_execution_mode_round_trips(tmp_path):
    """PUT execution_mode=shadow → GET returns shadow; then paper round-trip."""
    app, _ = _wire_app(tmp_path)
    shadow = await _call(
        app, "put", SETTINGS_URL, json={"general": {"execution_mode": "shadow"}}
    )
    assert shadow.status_code == 200
    assert shadow.json()["data"]["general"]["execution_mode"] == "shadow"

    read_back = await _call(app, "get", SETTINGS_URL)
    assert read_back.json()["data"]["general"]["execution_mode"] == "shadow"

    back = await _call(
        app, "put", SETTINGS_URL, json={"general": {"execution_mode": "paper"}}
    )
    assert back.json()["data"]["general"]["execution_mode"] == "paper"


# ── network proxy ───────────────────────────────────────────────────────────
# The regression these lock down: proxy_url used to be written to the keyring
# ONLY (_persistable dropped the whole network section). UI-saved proxies then
# vanished on restart, because main.py builds proxy_url at startup from
# _load_non_sensitive() — which reads settings.json, never the keyring. The
# setting looked saved and had no effect.


@pytest.mark.asyncio
async def test_network_proxy_round_trips(tmp_path):
    """PUT proxy_url → GET returns it."""
    app, _ = _wire_app(tmp_path)
    resp = await _call(
        app,
        "put",
        SETTINGS_URL,
        json={"network": {"proxy_url": "socks5://127.0.0.1:1080"}},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["network"]["proxy_url"] == "socks5://127.0.0.1:1080"

    read_back = await _call(app, "get", SETTINGS_URL)
    assert read_back.json()["data"]["network"]["proxy_url"] == "socks5://127.0.0.1:1080"


@pytest.mark.asyncio
async def test_network_proxy_survives_restart(tmp_path):
    """The startup path reads settings.json, not the keyring — so a new
    SettingsService over the same file must still see the proxy."""
    app, _ = _wire_app(tmp_path)
    await _call(
        app,
        "put",
        SETTINGS_URL,
        json={"network": {"proxy_url": "http://127.0.0.1:7890"}},
    )

    # Fresh service over the same config file = what a process restart sees.
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    settings = restarted._load_non_sensitive()
    assert settings["network"]["proxy_url"] == "http://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_network_proxy_clear(tmp_path):
    """Empty string clears it; the key must remain present (as "") so the
    loader's merge does not resurrect a stale value."""
    app, _ = _wire_app(tmp_path)
    await _call(
        app,
        "put",
        SETTINGS_URL,
        json={"network": {"proxy_url": "http://127.0.0.1:7890"}},
    )
    await _call(app, "put", SETTINGS_URL, json={"network": {"proxy_url": ""}})

    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert restarted._load_non_sensitive()["network"].get("proxy_url", "") == ""


# ── network proxy invalidates the cached agent runtime ─────────────────────
# The agent runtime builds its httpx client (proxy included) once and caches
# it per user.  Only an "llm" change used to drop that cache, so a proxy fix
# saved from the UI kept talking through the stale, dead proxy until restart.


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["llm", "network"])
async def test_settings_change_invalidates_agent_runtime(
    tmp_path, monkeypatch, section
):
    calls: list[str | None] = []

    async def _fake_refresh(service, *, user_id=None):
        calls.append(user_id)

    monkeypatch.setattr("app.api.v1.settings._refresh_agent_runtime", _fake_refresh)
    app, _ = _wire_app(tmp_path)

    payload = (
        {"llm": {"model": "m"}} if section == "llm" else {"network": {"proxy_url": ""}}
    )
    resp = await _call(app, "put", SETTINGS_URL, json=payload)

    assert resp.status_code == 200
    assert len(calls) == 1, f"a {section} change must invalidate the runtime"


@pytest.mark.asyncio
async def test_unrelated_settings_change_keeps_agent_runtime(tmp_path, monkeypatch):
    calls: list[str | None] = []

    async def _fake_refresh(service, *, user_id=None):
        calls.append(user_id)

    monkeypatch.setattr("app.api.v1.settings._refresh_agent_runtime", _fake_refresh)
    app, _ = _wire_app(tmp_path)

    await _call(app, "put", SETTINGS_URL, json={"general": {"execution_mode": "paper"}})

    assert calls == []


# ── PA heartbeat 设置(设置页开关) ──────────────────────────────────────────
# 事故背景:心跳原本只在启动期由 env 门控,设置页没有开关;后来加了设置页开关,
# 但若仍要求重启才生效,UI 显示的"已开启"就是看得见的假象。这组锁住:
# 默认关、PUT 落盘、重启后仍在、0 = 不设上限、非法周期被拒、越界小时被夹紧。


def _heartbeat_payload(**fields) -> dict:
    return {"pa_heartbeat": fields}


@pytest.mark.asyncio
async def test_pa_heartbeat_defaults_to_off(tmp_path):
    app, _ = _wire_app(tmp_path)
    resp = await _call(app, "get", SETTINGS_URL)
    section = resp.json()["data"]["pa_heartbeat"]
    assert section["enabled"] is False
    assert section["daily_max"] == 20


@pytest.mark.asyncio
async def test_pa_heartbeat_round_trips_and_survives_restart(tmp_path):
    app, _ = _wire_app(tmp_path)
    resp = await _call(
        app,
        "put",
        SETTINGS_URL,
        json=_heartbeat_payload(enabled=True, daily_max=0, interval="4h", hour_utc=6),
    )
    assert resp.status_code == 200

    # 新的 service 读同一个文件 = 进程重启后看到的东西(调度器每 tick 都这样读)
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    section = restarted.get_pa_heartbeat()
    assert section == {
        "enabled": True,
        "daily_max": 0,
        "interval": "4h",
        "hour_utc": 6,
    }


@pytest.mark.asyncio
async def test_pa_heartbeat_daily_max_zero_means_no_cap(tmp_path):
    """0 是"不设上限"的语义标记,不能被夹成 1 之类的最小值。"""
    app, _ = _wire_app(tmp_path)
    await _call(app, "put", SETTINGS_URL, json=_heartbeat_payload(daily_max=0))
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert restarted.get_pa_heartbeat()["daily_max"] == 0


@pytest.mark.asyncio
async def test_pa_heartbeat_negative_daily_max_clamps_to_zero(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(app, "put", SETTINGS_URL, json=_heartbeat_payload(daily_max=-5))
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert restarted.get_pa_heartbeat()["daily_max"] == 0


@pytest.mark.asyncio
async def test_pa_heartbeat_unknown_interval_keeps_previous_value(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(app, "put", SETTINGS_URL, json=_heartbeat_payload(interval="1h"))
    await _call(app, "put", SETTINGS_URL, json=_heartbeat_payload(interval="7m"))
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert restarted.get_pa_heartbeat()["interval"] == "1h"


@pytest.mark.asyncio
async def test_pa_heartbeat_hour_is_clamped_to_a_day(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(app, "put", SETTINGS_URL, json=_heartbeat_payload(hour_utc=99))
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert restarted.get_pa_heartbeat()["hour_utc"] == 23


# ── MissionWorker 设置(调查管道消费者) ──────────────────────────────────────
# 契约:默认**关**(2026-10-26 起;它是无人值守的 LLM 消费者,默认开等于装好
# 就开始花)、非法值保持原值、数值夹紧、未知键不落盘。


def _worker_payload(**fields) -> dict:
    return {"mission_worker": fields}


@pytest.mark.asyncio
async def test_mission_worker_defaults_to_off(tmp_path):
    app, _ = _wire_app(tmp_path)
    resp = await _call(app, "get", SETTINGS_URL)
    assert resp.json()["data"]["mission_worker"] == {
        "enabled": False,
        "interval_minutes": 15,
        "max_rounds_per_mission": 3,
    }


@pytest.mark.asyncio
async def test_mission_worker_can_be_switched_on_and_survives_restart(tmp_path):
    app, _ = _wire_app(tmp_path)
    resp = await _call(app, "put", SETTINGS_URL, json=_worker_payload(enabled=True))
    assert resp.status_code == 200
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert restarted.get_mission_worker()["enabled"] is True


@pytest.mark.asyncio
async def test_mission_worker_round_trips_and_survives_restart(tmp_path):
    app, _ = _wire_app(tmp_path)
    resp = await _call(
        app,
        "put",
        SETTINGS_URL,
        json=_worker_payload(
            enabled=False, interval_minutes=30, max_rounds_per_mission=5
        ),
    )
    assert resp.status_code == 200
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert restarted.get_mission_worker() == {
        "enabled": False,
        "interval_minutes": 30,
        "max_rounds_per_mission": 5,
    }


@pytest.mark.asyncio
async def test_mission_worker_numbers_are_clamped(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(
        app,
        "put",
        SETTINGS_URL,
        json=_worker_payload(interval_minutes=0, max_rounds_per_mission=999),
    )
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    section = restarted.get_mission_worker()
    assert section["interval_minutes"] == 1
    assert section["max_rounds_per_mission"] == 20


@pytest.mark.asyncio
async def test_mission_worker_invalid_values_keep_previous(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(app, "put", SETTINGS_URL, json=_worker_payload(interval_minutes=45))
    await _call(
        app,
        "put",
        SETTINGS_URL,
        json=_worker_payload(interval_minutes="soon", max_rounds_per_mission=True),
    )
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    section = restarted.get_mission_worker()
    assert section["interval_minutes"] == 45
    assert section["max_rounds_per_mission"] == 3


@pytest.mark.asyncio
async def test_mission_worker_unknown_keys_are_not_persisted(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(app, "put", SETTINGS_URL, json=_worker_payload(auto_execute=True))
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert "auto_execute" not in restarted.get_mission_worker()


# ── ResearchAutorenew 设置(⑤→① 心跳) ────────────────────────────────────────
# 契约与 MissionWorker 相反的第一条:**默认关**。这一节跨红线("开调查是用户
# 的按钮"),开关就是授权;默认开 = 未经同意就花钱。


def _autorenew_payload(**fields) -> dict:
    return {"research_autorenew": fields}


@pytest.mark.asyncio
async def test_research_autorenew_defaults_to_off(tmp_path):
    app, _ = _wire_app(tmp_path)
    resp = await _call(app, "get", SETTINGS_URL)
    assert resp.json()["data"]["research_autorenew"] == {
        "enabled": False,
        "interval_minutes": 30,
        "max_per_round": 3,
        "auto_promote": True,
    }


@pytest.mark.asyncio
async def test_research_autorenew_round_trips_and_survives_restart(tmp_path):
    app, _ = _wire_app(tmp_path)
    resp = await _call(
        app,
        "put",
        SETTINGS_URL,
        json=_autorenew_payload(
            enabled=True, interval_minutes=45, max_per_round=5, auto_promote=False
        ),
    )
    assert resp.status_code == 200
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert restarted.get_research_autorenew() == {
        "enabled": True,
        "interval_minutes": 45,
        "max_per_round": 5,
        "auto_promote": False,
    }


@pytest.mark.asyncio
async def test_research_autorenew_numbers_are_clamped(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(
        app,
        "put",
        SETTINGS_URL,
        json=_autorenew_payload(interval_minutes=0, max_per_round=999),
    )
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    section = restarted.get_research_autorenew()
    assert section["interval_minutes"] == 1
    assert section["max_per_round"] == 20


@pytest.mark.asyncio
async def test_research_autorenew_truthy_string_cannot_enable_it(tmp_path):
    """跨红线的开关只认真 bool:字符串 "false"/"true" 一律拒绝。

    否则 ``bool("false")`` 为真,用户在表单里填 "false" 反而把无人值守的
    调查会打开了 —— 这正是这一节默认关着要防的事。
    """
    app, _ = _wire_app(tmp_path)
    await _call(app, "put", SETTINGS_URL, json=_autorenew_payload(enabled="false"))
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert restarted.get_research_autorenew()["enabled"] is False


@pytest.mark.asyncio
async def test_research_autorenew_unknown_keys_are_not_persisted(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(app, "put", SETTINGS_URL, json=_autorenew_payload(auto_close=True))
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert "auto_close" not in restarted.get_research_autorenew()


# ── ResearchSensor 设置(① 传感器,只读扫描) ────────────────────────────────
# 同样默认关:不花 LLM 钱,但会往图里写证据行;图保持只有人亲手产生的内容。


def _sensor_payload(**fields) -> dict:
    return {"research_sensor": fields}


@pytest.mark.asyncio
async def test_research_sensor_defaults_to_off(tmp_path):
    app, _ = _wire_app(tmp_path)
    resp = await _call(app, "get", SETTINGS_URL)
    assert resp.json()["data"]["research_sensor"] == {
        "enabled": False,
        "interval_minutes": 60,
        "question_limit": 200,
    }


@pytest.mark.asyncio
async def test_research_sensor_round_trips_and_survives_restart(tmp_path):
    app, _ = _wire_app(tmp_path)
    resp = await _call(
        app,
        "put",
        SETTINGS_URL,
        json=_sensor_payload(enabled=True, interval_minutes=90, question_limit=50),
    )
    assert resp.status_code == 200
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert restarted.get_research_sensor() == {
        "enabled": True,
        "interval_minutes": 90,
        "question_limit": 50,
    }


@pytest.mark.asyncio
async def test_research_sensor_numbers_are_clamped(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(
        app,
        "put",
        SETTINGS_URL,
        json=_sensor_payload(interval_minutes=0, question_limit=99999),
    )
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    section = restarted.get_research_sensor()
    assert section["interval_minutes"] == 1
    assert section["question_limit"] == 2000


@pytest.mark.asyncio
async def test_research_sensor_truthy_string_cannot_enable_it(tmp_path):
    """写入开关只认真 bool —— 理由同 research_autorenew。"""
    app, _ = _wire_app(tmp_path)
    await _call(app, "put", SETTINGS_URL, json=_sensor_payload(enabled="false"))
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert restarted.get_research_sensor()["enabled"] is False


@pytest.mark.asyncio
async def test_research_sensor_unknown_keys_are_not_persisted(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(app, "put", SETTINGS_URL, json=_sensor_payload(auto_close=True))
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert "auto_close" not in restarted.get_research_sensor()


# -- workspace_layouts:跨设备布局同步 -----------------------------------------


def _layout_payload(**layout_fields) -> dict:
    layout = {
        "id": "signal",
        "name": "信号 → 下单",
        "panels": [{"id": "watch_vchart"}, {"id": "order_form"}],
        "columns": 2,
        "schemaVersion": 2,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-02T00:00:00Z",
    }
    layout.update(layout_fields)
    return layout


def _sync_payload(layouts=None, tombstones=None) -> dict:
    section: dict = {}
    if layouts is not None:
        section["layouts"] = layouts
    if tombstones is not None:
        section["tombstones"] = tombstones
    return {"workspace_layouts": section}


@pytest.mark.asyncio
async def test_workspace_layouts_default_to_empty(tmp_path):
    app, _ = _wire_app(tmp_path)
    resp = await _call(app, "get", SETTINGS_URL)
    assert resp.json()["data"]["workspace_layouts"] == {
        "layouts": [],
        "tombstones": {},
    }


@pytest.mark.asyncio
async def test_workspace_layouts_roundtrip_survives_restart(tmp_path):
    app, _ = _wire_app(tmp_path)
    resp = await _call(
        app,
        "put",
        SETTINGS_URL,
        json=_sync_payload(
            layouts=[_layout_payload()], tombstones={"gone": "2026-02-01T00:00:00Z"}
        ),
    )
    assert resp.status_code == 200

    # 重启后仍在(证明真的落进 settings.json,不是只活在内存)
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    section = restarted._load_non_sensitive()["workspace_layouts"]
    assert [l["id"] for l in section["layouts"]] == ["signal"]
    assert section["tombstones"] == {"gone": "2026-02-01T00:00:00Z"}


@pytest.mark.asyncio
async def test_workspace_layouts_panel_props_whitelist_scalars(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(
        app,
        "put",
        SETTINGS_URL,
        json=_sync_payload(
            layouts=[
                _layout_payload(
                    panels=[
                        {
                            "id": "watch_kf",
                            "props": {"symbol": "BTC/USDT", "nested": {"a": 1}},
                            "w": 960,
                            "h": 480,
                        }
                    ]
                )
            ]
        ),
    )
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    panel = restarted._load_non_sensitive()["workspace_layouts"]["layouts"][0][
        "panels"
    ][0]
    assert panel["props"] == {"symbol": "BTC/USDT"}
    assert panel["w"] == 960
    assert panel["h"] == 480


@pytest.mark.asyncio
async def test_workspace_layouts_reject_panelless_layout(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(
        app,
        "put",
        SETTINGS_URL,
        json=_sync_payload(
            layouts=[_layout_payload(panels=[]), _layout_payload(id="ok")]
        ),
    )
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    ids = [
        l["id"] for l in restarted._load_non_sensitive()["workspace_layouts"]["layouts"]
    ]
    # 空 panels 的布局被拒收,其余保留 —— 一个坏条目不该拖垮整次同步
    assert ids == ["ok"]


@pytest.mark.asyncio
async def test_workspace_layouts_malformed_section_does_not_wipe_stored(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(
        app, "put", SETTINGS_URL, json=_sync_payload(layouts=[_layout_payload()])
    )

    # layouts 给成对象而不是数组 → 请求校验层拒收(422),存储保持原样
    bad = await _call(
        app, "put", SETTINGS_URL, json={"workspace_layouts": {"layouts": {"a": 1}}}
    )
    assert bad.status_code == 422

    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert len(restarted._load_non_sensitive()["workspace_layouts"]["layouts"]) == 1


@pytest.mark.asyncio
async def test_workspace_layouts_duplicate_ids_collapse(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(
        app,
        "put",
        SETTINGS_URL,
        json=_sync_payload(
            layouts=[
                _layout_payload(name="first"),
                _layout_payload(name="second"),
            ]
        ),
    )
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    layouts = restarted._load_non_sensitive()["workspace_layouts"]["layouts"]
    assert len(layouts) == 1
    assert layouts[0]["name"] == "first"


@pytest.mark.asyncio
async def test_workspace_layouts_omit_does_not_clear(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(
        app, "put", SETTINGS_URL, json=_sync_payload(layouts=[_layout_payload()])
    )

    # 只提交 tombstones,不提交 layouts → layouts 保持
    await _call(
        app,
        "put",
        SETTINGS_URL,
        json=_sync_payload(tombstones={"x": "2026-03-01T00:00:00Z"}),
    )
    restarted = SettingsService(config_path=tmp_path / "settings.json")
    section = restarted._load_non_sensitive()["workspace_layouts"]
    assert [l["id"] for l in section["layouts"]] == ["signal"]
    assert section["tombstones"] == {"x": "2026-03-01T00:00:00Z"}


@pytest.mark.asyncio
async def test_workspace_layouts_explicit_empty_list_clears(tmp_path):
    app, _ = _wire_app(tmp_path)
    await _call(
        app, "put", SETTINGS_URL, json=_sync_payload(layouts=[_layout_payload()])
    )
    await _call(app, "put", SETTINGS_URL, json=_sync_payload(layouts=[]))

    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert restarted._load_non_sensitive()["workspace_layouts"]["layouts"] == []


@pytest.mark.asyncio
async def test_workspace_layouts_are_capped(tmp_path):
    app, _ = _wire_app(tmp_path)
    layouts = [_layout_payload(id=f"ws-{i}") for i in range(80)]
    await _call(app, "put", SETTINGS_URL, json=_sync_payload(layouts=layouts))

    restarted = SettingsService(config_path=tmp_path / "settings.json")
    store = restarted._load_non_sensitive()["workspace_layouts"]["layouts"]
    assert len(store) == 50

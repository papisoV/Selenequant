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
        app, "put", SETTINGS_URL, json=_exchange_payload(provider="bybit", market_type="perp")
    )

    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_store_without_keyring_yields_503(tmp_path):
    app, _ = _wire_app(tmp_path, vault=_Vault(keyring_available=False))
    denied = await _call(app, "put", SETTINGS_URL, json=_llm_payload(api_key="sk-test-secret"))

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

    pair = {name: _seal(kpm, value) for name, value in (("api_key", "my-api-key"), ("api_secret", "my-api-secret"))}
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
    plain = await _call(app, "put", SETTINGS_URL, json=_llm_payload(api_key="sk-plain-key"))

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
    shadow = await _call(app, "put", SETTINGS_URL, json={"general": {"execution_mode": "shadow"}})
    assert shadow.status_code == 200
    assert shadow.json()["data"]["general"]["execution_mode"] == "shadow"

    read_back = await _call(app, "get", SETTINGS_URL)
    assert read_back.json()["data"]["general"]["execution_mode"] == "shadow"

    back = await _call(app, "put", SETTINGS_URL, json={"general": {"execution_mode": "paper"}})
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
        app, "put", SETTINGS_URL, json={"network": {"proxy_url": "socks5://127.0.0.1:1080"}}
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
        app, "put", SETTINGS_URL, json={"network": {"proxy_url": "http://127.0.0.1:7890"}}
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
        app, "put", SETTINGS_URL, json={"network": {"proxy_url": "http://127.0.0.1:7890"}}
    )
    await _call(app, "put", SETTINGS_URL, json={"network": {"proxy_url": ""}})

    restarted = SettingsService(config_path=tmp_path / "settings.json")
    assert restarted._load_non_sensitive()["network"].get("proxy_url", "") == ""

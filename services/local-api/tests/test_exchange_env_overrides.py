"""Tests for exchange env var fallback resolution (P1-4 Stage C prep).

Covers:
  - testnet defaults to True when neither settings nor env var specify it
  - settings.testnet=False honored when env var absent
  - env var SELENE_EXCHANGE_TESTNET overrides settings
  - env var SELENE_EXCHANGE_PROVIDER overrides settings provider
  - env var credentials returned when keyring secrets unavailable
"""

from __future__ import annotations

from app.main import _resolve_exchange_env_overrides


class TestResolveExchangeEnvOverrides:
    def test_testnet_defaults_true_when_unset(self) -> None:
        saved = {"exchange": {"provider": "binance"}}
        provider, testnet, _ak, _as_, _pa = _resolve_exchange_env_overrides(
            saved, env={}
        )
        assert provider == "binance"
        assert testnet is True

    def test_settings_testnet_false_honored(self) -> None:
        saved = {"exchange": {"provider": "binance", "testnet": False}}
        _p, testnet, _ak, _as_, _pa = _resolve_exchange_env_overrides(
            saved, env={}
        )
        assert testnet is False

    def test_env_testnet_false_overrides_settings_true(self) -> None:
        saved = {"exchange": {"provider": "binance", "testnet": True}}
        _p, testnet, _ak, _as_, _pa = _resolve_exchange_env_overrides(
            saved, env={"SELENE_EXCHANGE_TESTNET": "0"}
        )
        assert testnet is False

    def test_env_testnet_true_overrides_settings_false(self) -> None:
        saved = {"exchange": {"provider": "binance", "testnet": False}}
        _p, testnet, _ak, _as_, _pa = _resolve_exchange_env_overrides(
            saved, env={"SELENE_EXCHANGE_TESTNET": "true"}
        )
        assert testnet is True

    def test_env_testnet_synonyms_accepted(self) -> None:
        for val in ("1", "true", "yes", "on"):
            _p, t, _ak, _as_, _pa = _resolve_exchange_env_overrides(
                {"exchange": {}}, env={"SELENE_EXCHANGE_TESTNET": val}
            )
            assert t is True
        for val in ("0", "false", "no", "off"):
            _p, t, _ak, _as_, _pa = _resolve_exchange_env_overrides(
                {"exchange": {}}, env={"SELENE_EXCHANGE_TESTNET": val}
            )
            assert t is False

    def test_env_provider_overrides_settings(self) -> None:
        saved = {"exchange": {"provider": "binance"}}
        provider, _t, _ak, _as_, _pa = _resolve_exchange_env_overrides(
            saved, env={"SELENE_EXCHANGE_PROVIDER": "okx"}
        )
        assert provider == "okx"

    def test_env_provider_ignored_when_invalid(self) -> None:
        """Unknown provider strings fall back to the settings value."""
        saved = {"exchange": {"provider": "binance"}}
        provider, _t, _ak, _as_, _pa = _resolve_exchange_env_overrides(
            saved, env={"SELENE_EXCHANGE_PROVIDER": "bybit"}
        )
        assert provider == "binance"

    def test_env_credentials_returned(self) -> None:
        saved = {"exchange": {"provider": "binance"}}
        _p, _t, ak, sk, pa = _resolve_exchange_env_overrides(
            saved,
            env={
                "SELENE_EXCHANGE_API_KEY": "env-key",
                "SELENE_EXCHANGE_API_SECRET": "env-secret",
                "SELENE_EXCHANGE_PASSPHRASE": "env-pass",
            },
        )
        assert ak == "env-key"
        assert sk == "env-secret"
        assert pa == "env-pass"

    def test_env_credentials_empty_when_unset(self) -> None:
        saved = {"exchange": {"provider": "binance"}}
        _p, _t, ak, sk, pa = _resolve_exchange_env_overrides(saved, env={})
        assert ak == ""
        assert sk == ""
        assert pa == ""

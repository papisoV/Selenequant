"""C1 setup.py keyring store test.

Asserts that _store_binance_secrets writes to OS keyring via SecretManager
(encrypted, provider=selenespace.exchange), NOT to plaintext files. This
closes the C1 gap where `selenespace setup` wrote to a file path that
main.py:1108-1147 never reads.

Tests the C1 fix in packages/cli/selene_space_cli/commands/setup.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from selene_space_cli.commands.setup import _store_binance_secrets, _write_binance_key


class _FakeKeyringBackend:
    """In-memory fake keyring backend."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, key: str, value: str) -> None:
        self._store[(service, key)] = value

    def get_password(self, service: str, key: str) -> str | None:
        return self._store.get((service, key))

    def delete_password(self, service: str, key: str) -> None:
        self._store.pop((service, key), None)


class TestSetupKeyringStore:
    def test_store_writes_to_keyring_provider_selenespace_exchange(self) -> None:
        """_store_binance_secrets writes to keyring with the SAME provider/id
        that main.py reads (selenespace.exchange / {env}_api_key etc.).
        Default env="mainnet" → ids mainnet_api_key/mainnet_api_secret/mainnet_passphrase.
        """
        fake_backend = _FakeKeyringBackend()
        with patch("keyring.get_keyring", return_value=fake_backend), \
             patch("keyring.set_password", side_effect=fake_backend.set_password):
            ok = _store_binance_secrets("test-key", "test-secret", "test-pass")
        assert ok, "keyring store should succeed when backend available"
        # Verify the values landed in the right service/id slots.
        assert fake_backend.get_password("selenespace.exchange", "mainnet_api_key") is not None
        assert fake_backend.get_password("selenespace.exchange", "mainnet_api_secret") is not None
        assert fake_backend.get_password("selenespace.exchange", "mainnet_passphrase") is not None
        # Values should be encrypted (not plaintext) — decrypt via SecretManager.
        from selene_space_security.encryption import decrypt_value

        assert decrypt_value(fake_backend.get_password("selenespace.exchange", "mainnet_api_key")) == "test-key"
        assert decrypt_value(fake_backend.get_password("selenespace.exchange", "mainnet_api_secret")) == "test-secret"
        assert decrypt_value(fake_backend.get_password("selenespace.exchange", "mainnet_passphrase")) == "test-pass"

    def test_store_testnet_env_uses_testnet_ids(self) -> None:
        """env='testnet' → ids testnet_api_key/testnet_api_secret (coexist with mainnet)."""
        fake_backend = _FakeKeyringBackend()
        with patch("keyring.get_keyring", return_value=fake_backend), \
             patch("keyring.set_password", side_effect=fake_backend.set_password):
            ok = _store_binance_secrets("tn-key", "tn-secret", "", env="testnet")
        assert ok
        assert fake_backend.get_password("selenespace.exchange", "testnet_api_key") is not None
        assert fake_backend.get_password("selenespace.exchange", "testnet_api_secret") is not None
        assert fake_backend.get_password("selenespace.exchange", "testnet_passphrase") is None
        # mainnet slots should be empty (coexist, not overwrite)
        assert fake_backend.get_password("selenespace.exchange", "mainnet_api_key") is None

    def test_store_without_passphrase_skips_passphrase_slot(self) -> None:
        """Empty passphrase → passphrase slot not written (main.py reads None)."""
        fake_backend = _FakeKeyringBackend()
        with patch("keyring.get_keyring", return_value=fake_backend), \
             patch("keyring.set_password", side_effect=fake_backend.set_password):
            ok = _store_binance_secrets("k", "s", "")
        assert ok
        assert fake_backend.get_password("selenespace.exchange", "mainnet_api_key") is not None
        assert fake_backend.get_password("selenespace.exchange", "mainnet_api_secret") is not None
        assert fake_backend.get_password("selenespace.exchange", "mainnet_passphrase") is None

    def test_store_returns_false_when_keyring_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When keyring lib missing, store returns False (caller falls back)."""
        monkeypatch.setattr(
            "selene_space_security.secrets.SecretManager.keyring_available",
            lambda self: False,
        )
        ok = _store_binance_secrets("k", "s", "")
        assert ok is False

    def test_legacy_write_binance_key_still_works(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Legacy file writer kept for backward-compat fallback (deprecated)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _write_binance_key("legacy-key")
        key_file = tmp_path / ".selenespace" / "secrets" / "binance" / "api_key"
        assert key_file.exists()
        assert key_file.read_text(encoding="utf-8") == "legacy-key"

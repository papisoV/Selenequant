"""Behavior locks for selene_space_security.secrets: ResolvedSecret leak
safety, env/file/keyring resolution, store/delete, and caching."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from selene_space_security.secrets import (
    ResolvedSecret,
    SecretManager,
    SecretRef,
    SecretResolutionError,
    SecretSource,
)


def _env_secret(payload: str, ref_id: str = "TEST_KEY") -> ResolvedSecret:
    return ResolvedSecret(
        value=payload,
        source=SecretSource.ENV,
        ref=SecretRef(source=SecretSource.ENV, id=ref_id),
    )


def _env_manager() -> SecretManager:
    return SecretManager()


def _env_ref(var_name: str) -> SecretRef:
    return SecretRef(source=SecretSource.ENV, id=var_name)


# --- ResolvedSecret safety ------------------------------------------------------


def test_repr_hides_payload() -> None:
    holder = _env_secret("super-secret-key")
    assert "super-secret-key" not in repr(holder)
    assert "redacted" in repr(holder)


def test_str_is_fixed_mask() -> None:
    assert str(_env_secret("super-secret-key")) == "***"


def test_use_hands_back_the_real_value() -> None:
    holder = _env_secret("my-api-key", ref_id="KEY")
    assert holder.use() == "my-api-key"


def test_accidental_fstring_stays_masked() -> None:
    # Common accident: f-string with secret
    rendered = f"Key is {_env_secret('leaked-value', ref_id='KEY')}"
    assert "leaked-value" not in rendered and "***" in rendered


# --- env resolution ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_reference_resolves_from_environment() -> None:
    vault = _env_manager()
    with patch.dict(os.environ, {"TEST_SECRET_VAR": "test-value"}):
        got = await vault.resolve(_env_ref("TEST_SECRET_VAR"))
        assert (got.use(), got.source) == ("test-value", SecretSource.ENV)


@pytest.mark.asyncio
async def test_absent_env_variable_raises() -> None:
    with pytest.raises(SecretResolutionError, match="not found"):
        await _env_manager().resolve(_env_ref("NONEXISTENT_VAR_12345"))


# --- file resolution ------------------------------------------------------------------


def _plant(directory: Path, name: str, payload: str) -> Path:
    target = directory / name
    target.write_text(payload)
    if sys.platform != "win32":
        target.chmod(0o600)
    return target


@pytest.mark.asyncio
async def test_flat_file_reference_resolves(tmp_path: Path) -> None:
    folder = tmp_path / "secrets"
    folder.mkdir()
    _plant(folder, "my_key", "file-secret-value\n")

    vault = SecretManager(base_dir=folder)
    ref = SecretRef(source=SecretSource.FILE, id="my_key")
    assert (await vault.resolve(ref)).use() == "file-secret-value"


@pytest.mark.asyncio
async def test_provider_scoped_file_reference_resolves(tmp_path: Path) -> None:
    nested = tmp_path / "secrets" / "exchange"
    nested.mkdir(parents=True)
    _plant(nested, "api_key", "exchange-key")

    vault = SecretManager(base_dir=tmp_path / "secrets")
    ref = SecretRef(source=SecretSource.FILE, provider="exchange", id="api_key")
    assert (await vault.resolve(ref)).use() == "exchange-key"


@pytest.mark.asyncio
async def test_unknown_file_reference_raises(tmp_path: Path) -> None:
    vault = SecretManager(base_dir=tmp_path)
    with pytest.raises(SecretResolutionError, match="not found"):
        await vault.resolve(SecretRef(source=SecretSource.FILE, id="nonexistent"))


@pytest.mark.asyncio
async def test_oversized_file_reference_raises(tmp_path: Path) -> None:
    _plant(tmp_path, "big_secret", "x" * 2_000_000)

    vault = SecretManager(base_dir=tmp_path)
    with pytest.raises(SecretResolutionError, match="exceeds"):
        await vault.resolve(SecretRef(source=SecretSource.FILE, id="big_secret"))


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
async def test_group_readable_file_reference_raises(tmp_path: Path) -> None:
    loose = tmp_path / "bad_perms"
    loose.write_text("secret")
    loose.chmod(0o644)

    vault = SecretManager(base_dir=tmp_path)
    with pytest.raises(SecretResolutionError, match="readable by group"):
        await vault.resolve(SecretRef(source=SecretSource.FILE, id="bad_perms"))


# --- keyring resolution -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keyring_entry_resolves() -> None:
    stub = MagicMock()
    stub.get_password.return_value = "keyring-secret"

    vault = SecretManager()
    ref = SecretRef(source=SecretSource.KEYRING, provider="selenespace", id="api_key")

    with patch.dict("sys.modules", {"keyring": stub}):
        got = await vault.resolve(ref)
        assert got.use() == "keyring-secret"
        stub.get_password.assert_called_once_with("selenespace", "api_key")


@pytest.mark.asyncio
async def test_missing_keyring_library_raises() -> None:
    ref = SecretRef(source=SecretSource.KEYRING, provider="selenespace", id="key")

    with patch.dict("sys.modules", {"keyring": None}):
        with pytest.raises(SecretResolutionError, match="keyring library not installed"):
            await SecretManager().resolve(ref)


@pytest.mark.asyncio
async def test_empty_keyring_slot_raises() -> None:
    empty_backend = MagicMock()
    empty_backend.get_password.return_value = None

    ref = SecretRef(source=SecretSource.KEYRING, provider="selenespace", id="missing")

    with patch.dict("sys.modules", {"keyring": empty_backend}):
        with pytest.raises(SecretResolutionError, match="No keyring entry"):
            await SecretManager().resolve(ref)


# --- store / delete -------------------------------------------------------------------------


def _memory_keyring() -> MagicMock:
    """Keyring double backed by a plain dict so store/resolve/delete interact."""
    slots: dict[tuple[str, str], str] = {}
    backend = MagicMock()
    backend.set_password.side_effect = lambda svc, key, val: slots.setdefault((svc, key), val)
    backend.get_password.side_effect = lambda svc, key: slots.get((svc, key))
    backend.delete_password.side_effect = lambda svc, key: slots.pop((svc, key), None)
    return backend


@pytest.mark.asyncio
async def test_store_then_resolve_then_delete_roundtrip() -> None:
    vault = SecretManager()
    slot = SecretRef(source=SecretSource.KEYRING, provider="selenespace", id="llm_api_key")

    with patch.dict("sys.modules", {"keyring": _memory_keyring()}):
        await vault.store(slot, "sk-live-key")
        assert (await vault.resolve(slot)).use() == "sk-live-key"
        await vault.delete(slot)
        assert await vault.exists(slot) is False


@pytest.mark.asyncio
async def test_store_to_file_rejected_under_default_policy(tmp_path: Path) -> None:
    vault = SecretManager(base_dir=tmp_path)
    with pytest.raises(SecretResolutionError, match="requires keyring backend"):
        await vault.store(SecretRef(source=SecretSource.FILE, id="fallback_key"), "value")


@pytest.mark.asyncio
async def test_file_store_allowed_when_policy_relaxed(tmp_path: Path) -> None:
    vault = SecretManager(base_dir=tmp_path, keyring_required_for_store=False)
    slot = SecretRef(source=SecretSource.FILE, provider="exchange", id="api_key")

    await vault.store(slot, "file-value")
    assert (await vault.resolve(slot)).use() == "file-value"

    await vault.delete(slot)
    assert await vault.exists(slot) is False


def test_keyring_availability_false_when_module_missing() -> None:
    with patch.dict("sys.modules", {"keyring": None}):
        assert SecretManager().keyring_available() is False


# --- cache -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeat_resolution_returns_cached_object() -> None:
    vault = SecretManager()

    with patch.dict(os.environ, {"CACHE_TEST_VAR": "cached-value"}):
        first = await vault.resolve(_env_ref("CACHE_TEST_VAR"))
        second = await vault.resolve(_env_ref("CACHE_TEST_VAR"))
        assert first is second


@pytest.mark.asyncio
async def test_cache_invalidation_picks_up_new_value() -> None:
    vault = SecretManager()

    with patch.dict(os.environ, {"CLEAR_CACHE_VAR": "v1"}):
        stale = await vault.resolve(_env_ref("CLEAR_CACHE_VAR"))

    vault.clear_cache()

    with patch.dict(os.environ, {"CLEAR_CACHE_VAR": "v2"}):
        fresh = await vault.resolve(_env_ref("CLEAR_CACHE_VAR"))
        assert fresh.use() == "v2" and stale is not fresh

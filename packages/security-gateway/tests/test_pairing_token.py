"""Behavior locks for selene_space_security.pairing.token issuance,
validation, revocation, and persistence."""

import time
from pathlib import Path

import pytest

from selene_space_security.pairing.token import TokenStore


@pytest.fixture
def make_tokens(tmp_path: Path):
    def _build() -> TokenStore:
        return TokenStore(store_dir=tmp_path)

    return _build


# --- issuance -----------------------------------------------------------------


def test_issued_token_carries_defaults(make_tokens) -> None:
    token = make_tokens().issue_token("device-1")
    assert bool(token.token)
    assert token.device_id == "device-1"
    assert token.expires_at > token.created_at
    assert "read" in token.scopes


def test_custom_scope_list_honored(make_tokens) -> None:
    assert make_tokens().issue_token("device-1", scopes=["read", "write"]).scopes == ["read", "write"]


def test_reissue_invalidates_previous_token(make_tokens) -> None:
    vault = make_tokens()
    superseded = vault.issue_token("device-1")
    current = vault.issue_token("device-1")
    assert superseded.token != current.token
    # Only the newest credential remains valid.
    assert vault.validate_token(superseded.token) is None
    assert vault.validate_token(current.token) is not None


def test_ten_devices_yield_ten_distinct_tokens(make_tokens) -> None:
    vault = make_tokens()
    minted = {vault.issue_token(f"dev-{n}").token for n in range(10)}
    assert len(minted) == 10


# --- validation -----------------------------------------------------------------


def test_fresh_token_resolves_to_device(make_tokens) -> None:
    vault = make_tokens()
    issued = vault.issue_token("device-1")

    resolved = vault.validate_token(issued.token)
    assert resolved is not None and resolved.device_id == "device-1"


def test_garbage_string_rejected(make_tokens) -> None:
    vault = make_tokens()
    vault.issue_token("device-1")
    assert vault.validate_token("not-a-valid-token") is None


def test_past_ttl_token_rejected(make_tokens) -> None:
    vault = make_tokens()
    issued = vault.issue_token("device-1", ttl_seconds=1)

    time.sleep(1.1)
    assert vault.validate_token(issued.token) is None


def test_validation_on_empty_store_is_none(make_tokens) -> None:
    assert make_tokens().validate_token("any-token") is None


# --- revocation ------------------------------------------------------------------


def test_revoke_kills_the_device_token(make_tokens) -> None:
    vault = make_tokens()
    live = vault.issue_token("device-1")
    assert vault.revoke_token("device-1") is True
    assert vault.validate_token(live.token) is None


def test_revoke_unknown_device_reports_false(make_tokens) -> None:
    assert make_tokens().revoke_token("no-such-device") is False


def test_revoke_all_counts_and_clears(make_tokens) -> None:
    vault = make_tokens()
    devices = [f"device-{n}" for n in range(3)]
    for d in devices:
        vault.issue_token(d)

    cleared = vault.revoke_all()
    assert cleared == 3
    for d in devices:
        assert vault.revoke_token(d) is False


# --- persistence -------------------------------------------------------------------


def test_token_usable_from_second_store_instance(tmp_path: Path) -> None:
    writer = TokenStore(store_dir=tmp_path)
    planted = writer.issue_token("device-1", scopes=["read", "trade"])

    reader = TokenStore(store_dir=tmp_path)
    recovered = reader.validate_token(planted.token)
    assert recovered is not None and recovered.scopes == ["read", "trade"]

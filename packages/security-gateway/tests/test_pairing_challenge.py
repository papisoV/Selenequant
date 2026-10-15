"""Behavior locks for selene_space_security.pairing.challenge code
verification including consumption, normalization, and expiry."""

import time
from pathlib import Path

from selene_space_security.pairing.challenge import PairingChallenge
from selene_space_security.pairing.store import PairingStore


def _fresh_pair(tmp_path: Path, *, device: dict | None = None, ttl: int | None = None) -> tuple[PairingStore, PairingChallenge]:
    store = PairingStore(store_dir=tmp_path, ttl_seconds=ttl) if ttl else PairingStore(store_dir=tmp_path)
    return store, PairingChallenge(store)


def test_correct_code_verifies_with_device_metadata(tmp_path: Path) -> None:
    store, challenge = _fresh_pair(tmp_path, device={"name": "phone"})
    request = store.create_request(device_info={"name": "phone"})

    verdict = challenge.verify_code(request.code)
    assert verdict.success is True
    assert verdict.device_info == {"name": "phone"}
    assert verdict.error is None


def test_unknown_code_rejected(tmp_path: Path) -> None:
    _, challenge = _fresh_pair(tmp_path)
    _.create_request()

    verdict = challenge.verify_code("ZZZZZZZZ")
    assert verdict.success is False
    assert verdict.error is not None


def test_code_single_use_then_dead(tmp_path: Path) -> None:
    store, challenge = _fresh_pair(tmp_path)
    request = store.create_request()

    assert challenge.verify_code(request.code).success is True
    assert challenge.verify_code(request.code).success is False


def test_lowercase_input_accepted(tmp_path: Path) -> None:
    store, challenge = _fresh_pair(tmp_path)
    request = store.create_request()

    assert challenge.verify_code(request.code.lower()).success is True


def test_padded_input_accepted(tmp_path: Path) -> None:
    store, challenge = _fresh_pair(tmp_path)
    request = store.create_request()

    assert challenge.verify_code(f"  {request.code}  ").success is True


def test_expired_code_rejected(tmp_path: Path) -> None:
    store, challenge = _fresh_pair(tmp_path, ttl=1)
    request = store.create_request()

    time.sleep(1.1)
    assert challenge.verify_code(request.code).success is False


def test_error_text_does_not_leak_reason(tmp_path: Path) -> None:
    """Error messages must not distinguish 'not found' from 'expired'."""
    store_a, challenge_a = _fresh_pair(tmp_path)
    missing = challenge_a.verify_code("AAAAAAAA")

    store_b, _ = _fresh_pair(tmp_path, ttl=1)
    store_b.create_request()
    time.sleep(1.1)
    stale = challenge_a.verify_code("AAAAAAAA")

    assert missing.error == stale.error


def test_verification_against_empty_store_fails(tmp_path: Path) -> None:
    _, challenge = _fresh_pair(tmp_path)
    assert challenge.verify_code("ABCD1234").success is False

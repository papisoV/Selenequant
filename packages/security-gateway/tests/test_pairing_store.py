"""Behavior locks for selene_space_security.pairing.store: code shape,
request lifecycle, TTL purge, and on-disk persistence."""

import time
from pathlib import Path

import pytest

from selene_space_security.pairing.store import (
    CODE_LENGTH,
    MAX_PENDING,
    UNAMBIGUOUS_ALPHABET,
    PairingStore,
)


@pytest.fixture
def make_store(tmp_path: Path):
    def _build(**kwargs) -> PairingStore:
        return PairingStore(store_dir=tmp_path, **kwargs)

    return _build


# --- code generation ---------------------------------------------------------


def test_generated_code_meets_length_contract(make_store) -> None:
    assert len(make_store().generate_code()) == CODE_LENGTH


def test_hundred_codes_all_within_alphabet(make_store) -> None:
    builder = make_store()
    for _ in range(100):
        assert set(builder.generate_code()) <= set(UNAMBIGUOUS_ALPHABET)


CONFUSABLES = set("0O1I")


def test_confusable_characters_never_appear(make_store) -> None:
    builder = make_store()
    for _ in range(100):
        assert not CONFUSABLES.intersection(builder.generate_code())


# --- request creation --------------------------------------------------------


def test_new_request_carries_code_expiry_and_metadata(make_store) -> None:
    req = make_store().create_request(device_info={"name": "test-device"})
    assert len(req.code) == CODE_LENGTH
    assert req.expires_at > req.created_at
    assert req.device_info == {"name": "test-device"}


def test_pending_capped_at_limit(make_store) -> None:
    builder = make_store()
    for _ in range(MAX_PENDING + 2):
        builder.create_request()
    assert len(builder.get_pending()) <= MAX_PENDING


def test_codes_unique_across_full_batch(make_store) -> None:
    builder = make_store()
    minted = {builder.create_request().code for _ in range(MAX_PENDING)}
    assert len(minted) == MAX_PENDING


# --- ttl ----------------------------------------------------------------------


def test_stale_requests_dropped_from_pending(make_store) -> None:
    builder = make_store(ttl_seconds=1)
    builder.create_request()
    assert len(builder.get_pending()) == 1

    time.sleep(1.1)
    assert builder.get_pending() == []


# --- removal ------------------------------------------------------------------


def test_remove_live_request_succeeds(make_store) -> None:
    builder = make_store()
    code = builder.create_request().code
    assert builder.remove_request(code) is True
    assert builder.get_pending() == []


def test_remove_unknown_code_reports_false(make_store) -> None:
    assert make_store().remove_request("ZZZZZZZZ") is False


def test_remove_accepts_lowercase_form(make_store) -> None:
    builder = make_store()
    code = builder.create_request().code
    assert builder.remove_request(code.lower()) is True


# --- persistence ---------------------------------------------------------------


def test_state_survives_store_reconstruction(tmp_path: Path) -> None:
    first = PairingStore(store_dir=tmp_path)
    planted = first.create_request(device_info={"type": "desktop"})

    reopened = PairingStore(store_dir=tmp_path)
    pending = reopened.get_pending()
    assert [p.code for p in pending] == [planted.code]
    assert [p.device_info for p in pending] == [{"type": "desktop"}]


def test_unparseable_state_file_treated_as_empty(tmp_path: Path) -> None:
    (tmp_path / "pending.json").write_text("not valid json")

    assert PairingStore(store_dir=tmp_path).get_pending() == []

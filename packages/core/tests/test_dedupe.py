"""Infra locks for selene_space_core.infra.dedupe — first/second sightings,
key separation, TTL expiry, size-bounded eviction, and clear()."""

import time

from selene_space_core.infra.dedupe import Deduplicator


def test_debut_key_is_not_a_duplicate():
    seen = Deduplicator(ttl_seconds=10)
    assert seen.is_duplicate("alpha") is False


def test_repeat_sighting_flags_duplicate():
    seen = Deduplicator(ttl_seconds=10)
    seen.is_duplicate("alpha")
    assert seen.is_duplicate("alpha") is True


def test_distinct_keys_stay_independent():
    seen = Deduplicator(ttl_seconds=10)
    seen.is_duplicate("alpha")
    assert seen.is_duplicate("beta") is False


def test_entry_expires_after_ttl():
    seen = Deduplicator(ttl_seconds=0.05)
    seen.is_duplicate("alpha")
    time.sleep(0.1)
    assert seen.is_duplicate("alpha") is False  # Expired


def test_capacity_bound_evicts_oldest():
    seen = Deduplicator(ttl_seconds=60, max_size=3)
    for k in ("k1", "k2", "k3", "k4"):
        seen.is_duplicate(k)  # k4 pushes out the oldest entry
    assert seen.size <= 3


def test_clear_forgets_everything():
    seen = Deduplicator()
    seen.is_duplicate("alpha")
    seen.clear()
    checks = (seen.size == 0, seen.is_duplicate("alpha") is False)
    assert all(checks)

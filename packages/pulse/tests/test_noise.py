from __future__ import annotations

from datetime import datetime, timezone

import pytest

from selene_space_pulse.noise import (
    Decision,
    NoiseStore,
    NotificationNoise,
    is_time_in_quiet_hours,
    parse_quiet_hours,
    resolve_noise_db_path,
)


def _noise(tmp_path, quiet_hours: str | None = None) -> NotificationNoise:
    return NotificationNoise(NoiseStore(tmp_path / "noise.db"), quiet_hours=quiet_hours)


def test_same_dedup_key_within_ttl_is_suppressed(tmp_path) -> None:
    noise = _noise(tmp_path)
    now = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)

    first = noise.evaluate("d:k", "cd:k", dedup_ttl=600, cooldown_ttl=0, now=now)
    second = noise.evaluate("d:k", "cd:k", dedup_ttl=600, cooldown_ttl=0, now=now)

    assert first.allowed is True
    assert first.reason == "allowed"
    assert second.allowed is False
    assert second.reason == "dedup"


def test_dedup_allows_again_after_ttl_expiry(tmp_path) -> None:
    noise = _noise(tmp_path)
    base = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)

    noise.evaluate("d:k", "cd:k", dedup_ttl=600, cooldown_ttl=0, now=base)
    after_expiry = noise.evaluate(
        "d:k",
        "cd:k",
        dedup_ttl=600,
        cooldown_ttl=0,
        now=base.fromtimestamp(base.timestamp() + 601, tz=timezone.utc),
    )

    assert after_expiry.allowed is True


def test_same_cooldown_key_within_cooldown_is_suppressed(tmp_path) -> None:
    noise = _noise(tmp_path)
    now = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)

    first = noise.evaluate("d1", "cd:k", dedup_ttl=0, cooldown_ttl=1800, now=now)
    second = noise.evaluate("d2", "cd:k", dedup_ttl=0, cooldown_ttl=1800, now=now)

    assert first.allowed is True
    assert second.allowed is False
    assert second.reason == "cooldown"


def test_dedup_is_checked_before_cooldown(tmp_path) -> None:
    # DSA check order: dedup first, then cooldown.
    noise = _noise(tmp_path)
    now = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)

    noise.evaluate("d:x", "cd:y", dedup_ttl=600, cooldown_ttl=1800, now=now)
    decision = noise.evaluate("d:x", "cd:y", dedup_ttl=600, cooldown_ttl=1800, now=now)

    assert decision.reason == "dedup"


def test_quiet_hours_suppresses_with_reason(tmp_path) -> None:
    # Naive datetimes are used as-is for minute-of-day checks (DSA semantics:
    # no configured timezone means the caller's wall clock is authoritative).
    noise = _noise(tmp_path, quiet_hours="22:00-08:00")
    now = datetime(2026, 7, 8, 23, 0)

    decision = noise.evaluate("d:k", "cd:k", dedup_ttl=0, cooldown_ttl=0, now=now)

    assert decision.allowed is False
    assert decision.reason == "quiet_hours"


def test_quiet_hours_checked_before_state(tmp_path) -> None:
    # Quiet hours must suppress even when dedup would allow (DSA order:
    # quiet-hours before dedup/cooldown lookups).
    noise = _noise(tmp_path, quiet_hours="02:00-04:00")
    now = datetime(2026, 7, 8, 3, 0)

    decision = noise.evaluate(
        "d:fresh", "cd:fresh", dedup_ttl=600, cooldown_ttl=600, now=now
    )

    assert decision.allowed is False
    assert decision.reason == "quiet_hours"


def test_quiet_hours_cross_midnight_wrap(tmp_path) -> None:
    # 22:00-08:00 window: 23:00 and 07:00 are quiet, 12:00 is not.
    quiet = parse_quiet_hours("22:00-08:00")
    assert quiet == (22 * 60, 8 * 60)
    assert quiet is not None

    assert is_time_in_quiet_hours(datetime(2026, 7, 8, 23, 0), quiet) is True
    assert is_time_in_quiet_hours(datetime(2026, 7, 8, 7, 0), quiet) is True
    assert is_time_in_quiet_hours(datetime(2026, 7, 8, 12, 0), quiet) is False


def test_quiet_hours_same_start_end_means_no_quiet(tmp_path) -> None:
    quiet = parse_quiet_hours("08:00-08:00")
    assert quiet is not None

    assert is_time_in_quiet_hours(datetime(2026, 7, 8, 8, 30), quiet) is False


def test_quiet_hours_invalid_format_raises(tmp_path) -> None:
    with pytest.raises(ValueError):
        parse_quiet_hours("25:00-08:00")
    with pytest.raises(ValueError):
        parse_quiet_hours("not-a-window")


def test_noise_store_persists_across_instances(tmp_path) -> None:
    db_path = tmp_path / "shared.db"
    now_ts = 1_000_000.0

    first = NoiseStore(db_path)
    first.record("dedup:k", now_ts + 600)
    # A second store instance over the same path must see the written expiry.
    second = NoiseStore(db_path)

    assert second.expires_at("dedup:k", now_ts) == now_ts + 600
    assert second.expires_at("dedup:k", now_ts + 601) is None
    assert second.expires_at("missing", now_ts) is None


def test_resolve_noise_db_path_env_override(monkeypatch, tmp_path) -> None:
    custom = tmp_path / "custom" / "noise.db"
    monkeypatch.setenv("SELENE_PULSE_NOISE_DB", str(custom))

    assert resolve_noise_db_path() == custom


def test_resolve_noise_db_path_default_without_env(monkeypatch) -> None:
    monkeypatch.delenv("SELENE_PULSE_NOISE_DB", raising=False)

    resolved = resolve_noise_db_path()

    assert resolved.name == "pulse_noise.db"
    assert ".selenespace" in str(resolved)


def test_evaluate_is_fail_open_on_store_errors(tmp_path) -> None:
    class _BrokenStore:
        def expires_at(self, key, now_ts):
            raise RuntimeError("boom")

        def record(self, key, expires_at):
            raise RuntimeError("boom")

    noise = NotificationNoise(_BrokenStore())  # type: ignore[arg-type]
    now = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)

    decision = noise.evaluate("d:k", "cd:k", dedup_ttl=600, cooldown_ttl=0, now=now)

    assert isinstance(decision, Decision)
    assert decision.allowed is True
    assert decision.reason == "noise_check_failed_open"


def test_zero_ttls_never_write_state(tmp_path) -> None:
    noise = _noise(tmp_path)
    now = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)

    for _ in range(3):
        decision = noise.evaluate("d:k", "cd:k", dedup_ttl=0, cooldown_ttl=0, now=now)
        assert decision.allowed is True

"""Tests for OutageTracker — outage-episode bookkeeping for the poll source.

Fully offline and pure: every call takes an injected ``now_ms`` wall clock, so
there is no ``time`` module dependency and no I/O. The tracker is deliberately
total (no-raise) because it runs inside the poll loop's hot path — a raise here
would kill polling for every symbol.
"""

from __future__ import annotations

import dataclasses

import pytest

from selene_space_market.sources.astock_outage_tracker import (
    OutageEpisode,
    OutageTracker,
)


def test_first_failure_opens_an_episode() -> None:
    t = OutageTracker(source="astock_intraday")
    ep = t.record_batch_failure(["600519.SH", "000001.SZ"], now_ms=1_000_000)

    assert ep is not None
    assert ep.source == "astock_intraday"
    assert ep.opened_at_ms == 1_000_000
    assert ep.closed_at_ms is None
    assert ep.is_open is True
    assert ep.duration_sec is None
    assert ep.failed_batches == 1
    assert ep.symbols_affected == frozenset({"600519.SH", "000001.SZ"})


def test_open_and_close_share_the_episode_identity() -> None:
    """Open and close of one outage must agree on (source, opened_at_ms).

    The DB sink upserts on that natural key, so the close has to target the
    row the open wrote — a mismatch would insert a second row instead.
    """
    t = OutageTracker()
    opened = t.record_batch_failure(["600519.SH"], now_ms=1000)
    assert opened is not None
    closed = t.record_batch_success(["600519.SH"], now_ms=2000, healthy=True)
    assert closed is not None
    assert (closed.source, closed.opened_at_ms) == (
        opened.source,
        opened.opened_at_ms,
    )


def test_separate_outages_open_at_distinct_instants() -> None:
    """Two outages must not share a key, or the second would clobber the first.

    Regression: identity used to be a per-process counter, which restarts at 1
    after a restart — so the first outage of a new process overwrote the first
    outage of the old one. The opening wall clock cannot repeat (closing an
    outage takes at least one poll cycle), so it is collision-free.
    """
    t = OutageTracker()
    first = t.record_batch_failure(["600519.SH"], now_ms=1000)
    assert first is not None
    t.record_batch_success(["600519.SH"], now_ms=2000, healthy=True)
    second = t.record_batch_failure(["600519.SH"], now_ms=5000)
    assert second is not None
    assert second.opened_at_ms != first.opened_at_ms


def test_consecutive_failures_extend_one_episode() -> None:
    t = OutageTracker()
    first = t.record_batch_failure(["600519.SH"], now_ms=1000)
    assert first is not None

    # While an episode is open, further failures extend it and return None — the
    # sink is meant to persist exactly once, at open.
    again = t.record_batch_failure(["000001.SZ"], now_ms=2000)
    assert again is None

    current = t.open_episode()
    assert current is not None
    assert current.opened_at_ms == 1000  # unchanged opening instant
    assert current.failed_batches == 2
    assert current.symbols_affected == frozenset({"600519.SH", "000001.SZ"})


def test_healthy_success_closes_episode_with_duration() -> None:
    t = OutageTracker()
    t.record_batch_failure(["600519.SH"], now_ms=1_000_000)

    closed = t.record_batch_success(["600519.SH"], now_ms=1_030_000, healthy=True)
    assert closed is not None
    assert closed.closed_at_ms == 1_030_000
    assert closed.is_open is False
    assert closed.duration_sec == pytest.approx(30.0)
    # The episode is no longer open.
    assert t.open_episode() is None


def test_empty_but_healthy_false_does_not_close_episode() -> None:
    """A 200 whose body yields no payload is NOT recovery.

    Mirrors ``_note_fetch_outcome``: an empty-but-well-formed response can mean
    the upstream format broke. Closing the episode on it would record a gap as
    healed while zero data actually flowed — a silent hole wearing a green light.
    """
    t = OutageTracker()
    t.record_batch_failure(["600519.SH"], now_ms=1000)

    still_open = t.record_batch_success(["600519.SH"], now_ms=2000, healthy=False)
    assert still_open is None
    current = t.open_episode()
    assert current is not None and current.is_open


def test_success_without_episode_returns_none() -> None:
    t = OutageTracker()
    assert t.record_batch_success(["600519.SH"], now_ms=1000, healthy=True) is None
    assert t.open_episode() is None


def test_empty_healthy_false_without_episode_returns_none() -> None:
    t = OutageTracker()
    assert t.record_batch_success(["600519.SH"], now_ms=1000, healthy=False) is None
    assert t.open_episode() is None


def test_reopening_after_close_starts_a_fresh_episode() -> None:
    t = OutageTracker()
    t.record_batch_failure(["600519.SH"], now_ms=1000)
    t.record_batch_success(["600519.SH"], now_ms=2000, healthy=True)

    second = t.record_batch_failure(["600519.SH"], now_ms=5000)
    assert second is not None
    assert second.opened_at_ms == 5000
    assert second.failed_batches == 1  # fresh, not accumulated


def test_episode_is_immutable() -> None:
    t = OutageTracker()
    ep = t.record_batch_failure(["600519.SH"], now_ms=1000)
    assert ep is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        ep.failed_batches = 99  # type: ignore[misc]


def test_open_episode_snapshot_is_a_copy_not_the_live_object() -> None:
    """Mutating tracker state must not retroactively rewrite an emitted episode."""
    t = OutageTracker()
    t.record_batch_failure(["600519.SH"], now_ms=1000)
    snapshot = t.open_episode()
    assert snapshot is not None
    t.record_batch_failure(["000001.SZ"], now_ms=2000)  # extend the live episode
    # The previously returned snapshot still reflects the state at its creation.
    assert snapshot.symbols_affected == frozenset({"600519.SH"})
    assert snapshot.failed_batches == 1


def test_record_methods_never_raise_on_odd_chunks() -> None:
    """Total by contract: the poll loop calls these in its hot path."""
    t = OutageTracker()
    # Empty chunk, then a healthy close — neither may raise.
    assert t.record_batch_failure([], now_ms=1000) is not None
    assert t.record_batch_success([], now_ms=2000, healthy=True) is not None


def test_episode_symbols_are_frozen() -> None:
    t = OutageTracker()
    ep = t.record_batch_failure(["600519.SH"], now_ms=1000)
    assert ep is not None
    assert isinstance(ep.symbols_affected, frozenset)


def test_source_label_is_carried_on_the_episode() -> None:
    t = OutageTracker(source="custom_src")
    ep = t.record_batch_failure(["600519.SH"], now_ms=1000)
    assert ep is not None
    assert ep.source == "custom_src"
    assert isinstance(ep, OutageEpisode)


# -- Option A′: the volume bracket across the gap -----------------------------


def test_volume_delta_brackets_turnover_across_the_gap() -> None:
    """The one thing an outage can still report: how much traded while dark.

    Sina's field 8 is *today's cumulative* volume, so the last reading before
    the gap and the first after it bracket the trades that happened in between
    — without reconstructing the lost ticks themselves.
    """
    t = OutageTracker()
    # Tick before the outage establishes each symbol's "before" reading.
    t.record_batch_success(
        ["600519.SH", "000001.SZ"],
        now_ms=1000,
        healthy=True,
        volumes={"600519.SH": 1000.0, "000001.SZ": 500.0},
    )
    opened = t.record_batch_failure(["600519.SH", "000001.SZ"], now_ms=2000)
    assert opened is not None
    assert opened.volume_before == 1500.0
    assert opened.volume_symbols == 2

    closed = t.record_batch_success(
        ["600519.SH", "000001.SZ"],
        now_ms=1_030_000,
        healthy=True,
        volumes={"600519.SH": 1700.0, "000001.SZ": 900.0},
    )
    assert closed is not None
    assert closed.volume_before == 1500.0
    assert closed.volume_after == 2600.0
    assert closed.volume_delta == 1100.0
    assert closed.volume_symbols == 2


def test_volume_delta_is_none_when_nothing_was_seen_before_the_gap() -> None:
    """No pre-gap reading means no bracket — reported missing, never as zero.

    Zero would be a *claim* that nothing traded; the truth is that we cannot
    know, and S2's whole discipline is to not fabricate.
    """
    t = OutageTracker()
    t.record_batch_failure(["600519.SH"], now_ms=1000)  # never seen before
    closed = t.record_batch_success(
        ["600519.SH"], now_ms=2000, healthy=True, volumes={"600519.SH": 900.0}
    )
    assert closed is not None
    assert closed.volume_before is None
    assert closed.volume_after is None
    assert closed.volume_delta is None
    assert closed.volume_symbols == 0


def test_volume_delta_excludes_a_symbol_absent_from_the_post_gap_batch() -> None:
    """Bracket only what was observed on both sides, and say how many that was.

    A delta over one of two symbols must not be presented as if it covered both.
    """
    t = OutageTracker()
    t.record_batch_success(
        ["600519.SH", "000001.SZ"],
        now_ms=1000,
        healthy=True,
        volumes={"600519.SH": 1000.0, "000001.SZ": 500.0},
    )
    t.record_batch_failure(["600519.SH", "000001.SZ"], now_ms=2000)
    closed = t.record_batch_success(
        ["600519.SH"],  # 000001.SZ did not come back in this batch
        now_ms=3000,
        healthy=True,
        volumes={"600519.SH": 1200.0},
    )
    assert closed is not None
    assert closed.volume_symbols == 1
    assert closed.volume_before == 1000.0
    assert closed.volume_after == 1200.0
    assert closed.volume_delta == 200.0


def test_volume_delta_drops_a_symbol_whose_counter_rolled_back() -> None:
    """A negative difference is a session rollover, not negative turnover.

    Field 8 resets at the session boundary, so an outage straddling it leaves
    yesterday's total on the "before" side and a small fresh number on the
    "after" side. Volume cannot be un-traded; exclude rather than emit nonsense.
    """
    t = OutageTracker()
    t.record_batch_success(
        ["600519.SH", "000001.SZ"],
        now_ms=1000,
        healthy=True,
        volumes={"600519.SH": 5_000_000.0, "000001.SZ": 100.0},
    )
    t.record_batch_failure(["600519.SH", "000001.SZ"], now_ms=2000)
    closed = t.record_batch_success(
        ["600519.SH", "000001.SZ"],
        now_ms=3000,
        healthy=True,
        # 600519 rolled over (new session); 000001 did not.
        volumes={"600519.SH": 250.0, "000001.SZ": 175.0},
    )
    assert closed is not None
    assert closed.volume_symbols == 1
    assert closed.volume_delta == 75.0  # only 000001 counted


def test_open_episode_reports_the_pre_gap_bracket_before_recovery() -> None:
    """The open row already knows the "before" side; only "after" is pending."""
    t = OutageTracker()
    t.record_batch_success(
        ["600519.SH"], now_ms=1000, healthy=True, volumes={"600519.SH": 1000.0}
    )
    opened = t.record_batch_failure(["600519.SH"], now_ms=2000)
    assert opened is not None
    assert opened.volume_before == 1000.0
    assert opened.volume_after is None
    assert opened.volume_delta is None


def test_volume_map_refreshes_across_episodes() -> None:
    """The second gap is bracketed from the post-recovery reading, not the first."""
    t = OutageTracker()
    t.record_batch_success(
        ["600519.SH"], now_ms=1000, healthy=True, volumes={"600519.SH": 1000.0}
    )
    t.record_batch_failure(["600519.SH"], now_ms=2000)
    t.record_batch_success(
        ["600519.SH"], now_ms=3000, healthy=True, volumes={"600519.SH": 1500.0}
    )

    second_open = t.record_batch_failure(["600519.SH"], now_ms=4000)
    assert second_open is not None
    assert second_open.volume_before == 1500.0  # 1000 would be the stale reading

    second_closed = t.record_batch_success(
        ["600519.SH"], now_ms=5000, healthy=True, volumes={"600519.SH": 1600.0}
    )
    assert second_closed is not None
    assert second_closed.volume_delta == 100.0


def test_unhealthy_batch_does_not_refresh_the_volume_map() -> None:
    """A format-break batch carries no trustworthy volume — do not ingest it."""
    t = OutageTracker()
    t.record_batch_success(
        ["600519.SH"], now_ms=1000, healthy=True, volumes={"600519.SH": 1000.0}
    )
    t.record_batch_success(
        ["600519.SH"], now_ms=1500, healthy=False, volumes={"600519.SH": 9999.0}
    )
    opened = t.record_batch_failure(["600519.SH"], now_ms=2000)
    assert opened is not None
    assert opened.volume_before == 1000.0  # not 9999.0


def test_late_symbol_entering_a_widening_outage_is_bracketed() -> None:
    """A symbol whose first failure comes in a later batch still has a "before".

    The volume map is only refreshed on healthy fetches, so a symbol that has
    not succeeded since the outage began still holds its pre-gap reading.
    """
    t = OutageTracker()
    t.record_batch_success(
        ["600519.SH", "000001.SZ"],
        now_ms=1000,
        healthy=True,
        volumes={"600519.SH": 1000.0, "000001.SZ": 500.0},
    )
    first = t.record_batch_failure(["600519.SH"], now_ms=2000)
    assert first is not None
    # 000001.SZ fails for the first time in a later batch of the same outage.
    t.record_batch_failure(["000001.SZ"], now_ms=2500)
    closed = t.record_batch_success(
        ["600519.SH", "000001.SZ"],
        now_ms=3000,
        healthy=True,
        volumes={"600519.SH": 1100.0, "000001.SZ": 700.0},
    )
    assert closed is not None
    assert closed.volume_symbols == 2
    assert closed.volume_delta == 300.0  # (1100-1000) + (700-500)

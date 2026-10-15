"""Tests for AstockIntradaySource (Sina realtime L1 snapshot adapter).

All tests are offline: the HTTP call is mocked and payloads are captured
verbatim from a live ``hq.sinajs.cn`` response (GB18030 body, one
``var hq_str_<code>="...";`` line per symbol).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest

from selene_space_market.sources.astock_intraday_source import (
    AstockIntradaySource,
    _in_l1_window,
    _parse_hq_line,
    _parse_ticker,
    _sina_code_to_symbol,
    _to_epoch_ms,
    parse_hq_response,
)

# Two symbols exercising both boards plus an unknown code Sina answers empty.
_LIVE_BODY = "\n".join(
    [
        'var hq_str_sh600519="贵州茅台,1287.000,1288.000,1274.890,1288.480,'
        "1268.110,1274.660,1274.890,2657276,3393315572.000,100,1274.660,200,"
        "1274.430,300,1274.000,100,1273.980,100,1273.900,56,1274.890,100,"
        "1274.950,600,1274.990,5900,1275.000,100,1275.030,2026-10-13,15:34:59,"
        '00,D|1200|1529868.00";',
        'var hq_str_sz000001="平安银行,11.730,11.730,11.570,11.770,11.550,'
        "11.570,11.580,99493686,1157375141.240,520200,11.570,1435600,11.560,"
        "903000,11.550,200000,11.540,226000,11.530,24600,11.580,14100,11.590,"
        "281082,11.600,68740,11.610,115500,11.620,2026-10-13,15:36:00,00,"
        'D|60500|699985.000";',
        'var hq_str_badcode="";',
    ]
)


# ---------------------------------------------------------------------------
# Symbol / time helpers


def test_sina_code_to_symbol_inverts_to_sina_symbol() -> None:
    assert _sina_code_to_symbol("sh600519") == "600519.SH"
    assert _sina_code_to_symbol("sz000001") == "000001.SZ"


def test_to_epoch_ms_is_asia_shanghai() -> None:
    # 15:34:59 at +08:00 — same wall clock, same instant, same epoch.
    expected = datetime(2026, 10, 13, 15, 34, 59, tzinfo=timezone(timedelta(hours=8)))
    assert _to_epoch_ms("2026-10-13", "15:34:59") == int(expected.timestamp() * 1000)


def test_in_l1_window_covers_session_and_auction() -> None:
    assert _in_l1_window(datetime(2026, 10, 13, 9, 20))  # call auction
    assert _in_l1_window(datetime(2026, 10, 13, 10, 0))
    assert _in_l1_window(datetime(2026, 10, 13, 15, 0))
    assert not _in_l1_window(datetime(2026, 10, 13, 9, 0))  # too early
    assert not _in_l1_window(datetime(2026, 10, 13, 15, 30))
    # 2026-10-11 is a Sunday
    assert not _in_l1_window(datetime(2026, 10, 11, 10, 0))


# ---------------------------------------------------------------------------
# Parsing


def test_parse_hq_line_extracts_code_and_fields() -> None:
    parsed = _parse_hq_line(_LIVE_BODY.splitlines()[0])
    assert parsed is not None
    code, fields = parsed
    assert code == "sh600519"
    assert fields[0] == "贵州茅台"
    assert fields[30] == "2026-10-13"
    assert fields[31] == "15:34:59"


def test_parse_hq_line_rejects_empty_and_malformed() -> None:
    assert _parse_hq_line('var hq_str_badcode="";') is None
    assert _parse_hq_line("garbage") is None


def test_parse_ticker_maps_fields() -> None:
    parsed = _parse_hq_line(_LIVE_BODY.splitlines()[0])
    assert parsed is not None
    code, fields = parsed
    ticker = _parse_ticker(code, fields)
    assert ticker is not None
    assert ticker.exchange == "astock"
    assert ticker.symbol == "600519.SH"
    assert ticker.name == "贵州茅台"
    assert ticker.last_price == 1274.890
    assert ticker.bid == 1274.660
    assert ticker.ask == 1274.890
    assert ticker.volume_24h == 2657276.0
    # Field 2 — the exchange's official previous close, surfaced for the
    # detector's limit-price basis.
    assert ticker.prev_close == 1288.000
    # (1274.890 - 1288.000) / 1288.000 * 100
    assert ticker.change_24h_pct == pytest.approx(-1.017857, abs=1e-5)
    # 15:34:59 CST
    assert ticker.timestamp == _to_epoch_ms("2026-10-13", "15:34:59")


def test_parse_ticker_skips_pre_open_zero_price() -> None:
    parsed = _parse_hq_line(_LIVE_BODY.splitlines()[0])
    assert parsed is not None
    code, parts = parsed
    parts = list(parts)
    parts[3] = "0.000"  # last price not yet set
    assert _parse_ticker(code, parts) is None


def test_parse_ticker_raises_on_short_payload() -> None:
    with pytest.raises(ValueError):
        _parse_ticker("sh600519", ["贵州茅台", "1", "2"])


def test_parse_hq_response_returns_valid_symbols_only() -> None:
    tickers, seen = parse_hq_response(_LIVE_BODY)
    assert set(tickers) == {"600519.SH", "000001.SZ"}
    assert tickers["000001.SZ"].last_price == 11.570
    # Both live symbols are quotable, so nothing lands in the pre-open bucket.
    assert seen == set()


def test_parse_hq_response_flags_pre_open_as_seen_not_quotable() -> None:
    """A pre-open row (last=0) is 'seen' — healthy upstream, no ticker."""
    body = _LIVE_BODY.splitlines()[0].replace(
        ",1274.890,", ",0.000,", 1
    )  # field 3 (last) → 0
    tickers, seen = parse_hq_response(body)
    assert tickers == {}
    assert seen == {"600519.SH"}


def test_parse_hq_response_drops_malformed_rows() -> None:
    body = 'var hq_str_sh600519="only,three,fields";'
    tickers, seen = parse_hq_response(body)
    assert tickers == {}
    assert seen == set()  # malformed ≠ seen (seen means healthy upstream)


# ---------------------------------------------------------------------------
# Fetch / warmup


class _FakeResp:
    status_code = 200

    def __init__(self, text: str) -> None:
        self.text = text
        self.encoding = "ISO-8859-1"  # overwritten by the source
        self.raise_called = False

    def raise_for_status(self) -> None:
        self.raise_called = True


def test_fetch_tickers_rest_batches_and_decodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, headers: Any = None, timeout: Any = None) -> _FakeResp:
        calls.append({"url": url, "headers": headers})
        return _FakeResp(_LIVE_BODY)

    import selene_space_market.sources.astock_intraday_source as mod

    monkeypatch.setattr(mod.requests, "get", fake_get)
    src = AstockIntradaySource()
    events = asyncio.run(src.fetch_tickers_rest(["600519.SH", "000001.SZ"]))

    assert set(events) == {"600519.SH", "000001.SZ"}
    assert "sh600519" in calls[0]["url"]
    assert "sz000001" in calls[0]["url"]
    assert calls[0]["headers"]["Referer"] == "https://finance.sina.com.cn"


# ---------------------------------------------------------------------------
# Poll loop


async def _run_passes(src: AstockIntradaySource, *, passes: int = 1) -> None:
    """Drive ``_poll_loop`` for exactly *passes* iterations, no real waiting."""
    src._running = True
    done = {"n": 0}
    real_sleep = asyncio.sleep

    async def _sleep(_sec: float) -> None:
        done["n"] += 1
        if done["n"] >= passes:
            src._running = False
        await real_sleep(0)

    import selene_space_market.sources.astock_intraday_source as mod

    with patch.object(mod.asyncio, "sleep", side_effect=_sleep):
        task = asyncio.create_task(src._poll_loop())
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.CancelledError:
            pass


def test_poll_loop_publishes_and_dedups(monkeypatch: pytest.MonkeyPatch) -> None:
    import selene_space_market.sources.astock_intraday_source as mod

    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _FakeResp(_LIVE_BODY))
    src = AstockIntradaySource()
    received: list[Any] = []
    src.on_ticker(received.append)
    src._subscribed = {"600519.SH", "000001.SZ"}

    asyncio.run(_run_passes(src, passes=3))

    # Two symbols, first pass publishes both; identical snapshots afterwards
    # are suppressed, so exactly two events total.
    assert len(received) == 2
    assert {e.symbol for e in received} == {"600519.SH", "000001.SZ"}
    assert src.get_ticker("600519.SH") is not None


def test_poll_loop_publishes_again_when_price_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import selene_space_market.sources.astock_intraday_source as mod

    moved = _LIVE_BODY.replace("1274.890", "1275.550", 1)
    bodies = [_LIVE_BODY, moved]
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _FakeResp(bodies.pop(0)))
    src = AstockIntradaySource()
    received: list[Any] = []
    src.on_ticker(received.append)
    src._subscribed = {"600519.SH"}

    asyncio.run(_run_passes(src, passes=2))

    assert len(received) == 2
    assert received[0].last_price == 1274.890
    assert received[1].last_price == 1275.550


def test_poll_loop_publishes_on_bid_ask_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A quote-only change (same last price, moved bid/ask) must republish.

    This is the call-auction case: the indicative price firms up while the
    last trade is unchanged, so keying dedup on price alone would drop it.
    """
    import selene_space_market.sources.astock_intraday_source as mod

    # Field 6 is bid1; shift it without touching the last price (field 3).
    moved = _LIVE_BODY.replace(
        ",1274.660,1274.890,2657276", ",1274.700,1274.890,2657276", 1
    )
    assert moved != _LIVE_BODY
    bodies = [_LIVE_BODY, moved]
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _FakeResp(bodies.pop(0)))
    src = AstockIntradaySource()
    received: list[Any] = []
    src.on_ticker(received.append)
    src._subscribed = {"600519.SH"}

    asyncio.run(_run_passes(src, passes=2))

    assert len(received) == 2
    assert received[0].bid == 1274.660
    assert received[1].bid == 1274.700
    assert received[0].last_price == received[1].last_price  # price unchanged


def test_poll_loop_does_not_back_off_pre_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-open (last=0) symbol is healthy upstream: no backoff, no event.

    Regression guard: treating the auction's zero-price quote as a failure
    would rack up a backoff window and could leave the symbol still blocked
    when the auction eventually prints — defeating the whole point.
    """
    import selene_space_market.sources.astock_intraday_source as mod

    pre_open = _LIVE_BODY.splitlines()[0].replace(",1274.890,", ",0.000,", 1)
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _FakeResp(pre_open))
    src = AstockIntradaySource()
    received: list[Any] = []
    src.on_ticker(received.append)
    src._subscribed = {"600519.SH"}

    asyncio.run(_run_passes(src, passes=3))

    assert src._backoff.fail_count("600519.SH") == 0  # never marked failed
    assert received == []  # but nothing published either


def test_poll_loop_backs_off_failed_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    import selene_space_market.sources.astock_intraday_source as mod

    def boom(*a: Any, **k: Any) -> _FakeResp:
        raise ConnectionError("upstream down")

    monkeypatch.setattr(mod.requests, "get", boom)
    src = AstockIntradaySource()
    src._subscribed = {"600519.SH"}

    asyncio.run(_run_passes(src, passes=3))

    assert src._backoff.fail_count("600519.SH") >= 1


def test_poll_loop_publishes_when_only_volume_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trade at an unchanged price still moves cumulative volume.

    Price and book are identical here, so keying dedup on price/bid/ask alone
    would drop the tick — the volume field has to be part of the key.
    """
    import selene_space_market.sources.astock_intraday_source as mod

    moved = _LIVE_BODY.replace(",2657276,", ",2657277,", 1)  # field 8 (volume)
    assert moved != _LIVE_BODY
    bodies = [_LIVE_BODY, moved]
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _FakeResp(bodies.pop(0)))
    src = AstockIntradaySource()
    received: list[Any] = []
    src.on_ticker(received.append)
    src._subscribed = {"600519.SH"}

    asyncio.run(_run_passes(src, passes=2))

    assert len(received) == 2
    assert received[0].volume_24h == 2657276.0
    assert received[1].volume_24h == 2657277.0
    assert received[0].last_price == received[1].last_price  # price unchanged


def test_publish_survives_callback_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising event-bus subscriber must not kill the poll loop.

    ``_publish`` calls the callback synchronously in the poll task, so an
    unguarded raise would unwind through the batch loop and terminate polling
    for every symbol. The fetch itself succeeded, so the symbol must stay out
    of backoff too.
    """
    import selene_space_market.sources.astock_intraday_source as mod

    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _FakeResp(_LIVE_BODY))
    src = AstockIntradaySource()
    seen: list[str] = []

    def boom(event: Any) -> None:
        seen.append(event.symbol)
        raise RuntimeError("subscriber blew up")

    src.on_ticker(boom)
    src._subscribed = {"600519.SH", "000001.SZ"}

    # If the raise escaped, `_run_passes` would propagate it and fail here.
    asyncio.run(_run_passes(src, passes=3))

    # The second symbol in the batch still got its event despite the first
    # raising, and neither symbol is penalised for a successful fetch.
    assert set(seen) == {"600519.SH", "000001.SZ"}
    assert src._backoff.fail_count("600519.SH") == 0
    assert src._backoff.fail_count("000001.SZ") == 0


def test_poll_task_cleared_when_loop_crashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead loop must clear its handle so ``start``/``subscribe`` respawn it.

    Those two only create a task when ``_poll_task is None``; leaving a
    completed Task object in place would make every later restart a silent
    no-op — polling stops forever with ``is_running`` still True.
    """
    import selene_space_market.sources.astock_intraday_source as mod

    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _FakeResp(_LIVE_BODY))
    src = AstockIntradaySource()
    src._subscribed = {"600519.SH"}

    def boom_interval() -> float:
        raise RuntimeError("interval failed")

    # Crashes outside the per-batch guards, so only the loop's `finally` runs.
    monkeypatch.setattr(src, "_interval_for_now", boom_interval)

    async def scenario() -> None:
        task = asyncio.create_task(src._poll_loop())
        src._poll_task = task
        with pytest.raises(RuntimeError):
            await task
        assert src._poll_task is None

    src._running = True
    asyncio.run(scenario())


def test_poll_loop_warns_once_when_response_yields_no_quotes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A 200 body that no longer parses must be loud, and only once.

    Otherwise a silent Sina payload change looks exactly like "every symbol is
    an unknown code" — per-symbol misses with no batch-level signal.
    """
    import selene_space_market.sources.astock_intraday_source as mod

    monkeypatch.setattr(
        mod.requests, "get", lambda *a, **k: _FakeResp('var hq_str_other="x";')
    )
    src = AstockIntradaySource()
    src._subscribed = {"600519.SH"}

    with caplog.at_level(logging.WARNING):
        asyncio.run(_run_passes(src, passes=3))

    empty_warnings = [
        r for r in caplog.records if "returned no quotes" in r.getMessage()
    ]
    assert len(empty_warnings) == 1  # once per episode, not once per pass
    assert src._backoff.fail_count("600519.SH") >= 1


def test_poll_loop_does_not_warn_during_pre_open_auction(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Pre-open rows are healthy payload — they must not trip the empty-body warning.

    Regression guard: during the 9:15-9:25 call auction every symbol lands in
    ``seen`` and none in ``tickers``, so a check that only looked at ``tickers``
    would cry wolf at the exact moment operators are watching the open. ``seen``
    has to count as "upstream answered".
    """
    import selene_space_market.sources.astock_intraday_source as mod

    pre_open = _LIVE_BODY.splitlines()[0].replace(",1274.890,", ",0.000,", 1)
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _FakeResp(pre_open))
    src = AstockIntradaySource()
    src._subscribed = {"600519.SH"}

    with caplog.at_level(logging.WARNING):
        asyncio.run(_run_passes(src, passes=3))

    warnings = [r for r in caplog.records if "returned no quotes" in r.getMessage()]
    assert warnings == []


def test_off_hours_interval_used_outside_session() -> None:
    src = AstockIntradaySource()
    with patch(
        "selene_space_market.sources.astock_intraday_source.datetime"
    ) as fake_dt:
        fake_dt.now.return_value = datetime(2026, 10, 13, 22, 0)
        assert src._interval_for_now() == src._off_hours_interval


# ---------------------------------------------------------------------------
# Interface contract


def test_registers_as_intraday_not_spot() -> None:
    src = AstockIntradaySource()
    assert src.config.exchange == "astock"
    assert src.config.market_type == "intraday"


def test_kline_and_orderbook_are_no_ops() -> None:
    src = AstockIntradaySource()
    assert src.get_kline("600519.SH") is None
    assert src.get_klines("600519.SH") == []
    assert src.get_orderbook("600519.SH") is None


def test_subscribe_validates_symbol() -> None:
    src = AstockIntradaySource()
    with pytest.raises(ValueError):
        asyncio.run(src.subscribe("BTC/USDT"))


def test_get_symbols_sorted() -> None:
    src = AstockIntradaySource()
    asyncio.run(src.subscribe("600519.SH"))
    asyncio.run(src.subscribe("000001.SZ"))
    assert src.get_symbols() == ["000001.SZ", "600519.SH"]


# ---------------------------------------------------------------------------
# Outage tracking (S2)


def _drive_outage(
    src: AstockIntradaySource,
    *,
    script: list[str | None],
    monkeypatch: pytest.MonkeyPatch,
) -> list[Any]:
    """Drive the poll loop through a scripted sequence of fetch outcomes.

    Each script entry is either a response body (str) or ``None`` meaning
    "raise ConnectionError" (network down). The loop runs one pass per entry.
    Returns the episodes delivered to the gap sink.

    A failed pass puts every symbol into its ``PollBackoff`` window, so the next
    pass would skip them entirely. To keep one script entry == one *attempt*, the
    driver advances a fake monotonic clock past the backoff cap on every sleep —
    modelling "the window expired, we retried".
    """
    import selene_space_market.poll_backoff as poll_backoff_mod
    import selene_space_market.sources.astock_intraday_source as mod

    outcomes = list(script)

    def fake_get(*a: Any, **k: Any) -> _FakeResp:
        outcome = outcomes.pop(0) if outcomes else None
        if outcome is None:
            raise ConnectionError("network down")
        return _FakeResp(outcome)

    monkeypatch.setattr(mod.requests, "get", fake_get)

    class _Clock:
        now = 1000.0

        def monotonic(self) -> float:
            return self.now

    clock = _Clock()
    monkeypatch.setattr(poll_backoff_mod, "time", clock)

    episodes: list[Any] = []
    src.set_gap_sink(episodes.append)

    async def _drive() -> None:
        src._running = True
        done = {"n": 0}
        real_sleep = asyncio.sleep

        async def _sleep(_sec: float) -> None:
            done["n"] += 1
            clock.now += 400.0  # past _BACKOFF_CAP_SEC (300s)
            if done["n"] >= len(script):
                src._running = False
            await real_sleep(0)

        with patch.object(mod.asyncio, "sleep", side_effect=_sleep):
            task = asyncio.create_task(src._poll_loop())
            try:
                await asyncio.wait_for(task, timeout=5)
            except asyncio.CancelledError:
                pass

    asyncio.run(_drive())
    return episodes


def test_outage_records_one_episode_on_sink(monkeypatch: pytest.MonkeyPatch) -> None:
    """An outage (3 failed passes) then recovery yields exactly one episode.

    The sink sees the open (closed_at_ms None) AND the close — the plan's
    "persist at open, update at close" two-call contract, id-matched.
    """
    src = AstockIntradaySource()
    src._subscribed = {"600519.SH", "000001.SZ"}
    episodes = _drive_outage(
        src, script=[None, None, None, _LIVE_BODY], monkeypatch=monkeypatch
    )

    assert len(episodes) == 2
    opened, closed = episodes
    assert opened.closed_at_ms is None
    assert opened.symbols_affected == frozenset({"600519.SH", "000001.SZ"})
    assert closed.closed_at_ms is not None
    assert closed.opened_at_ms == opened.opened_at_ms
    assert closed.duration_sec is not None and closed.duration_sec >= 0.0


def test_outage_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    src = AstockIntradaySource()
    src._subscribed = {"600519.SH"}
    with caplog.at_level(logging.WARNING):
        _drive_outage(
            src, script=[None, None, None, _LIVE_BODY], monkeypatch=monkeypatch
        )

    opened = [
        r
        for r in caplog.records
        if "outage" in r.getMessage().lower() and "opened" in r.getMessage().lower()
    ]
    assert len(opened) == 1  # one summary, not one per failed pass


def test_recovery_republishes_a_changed_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first *changed* post-outage snapshot must publish.

    Dedup state is untouched during the outage, so a quote that moved while the
    network was down still differs from the last pre-outage tuple and fires.
    """
    pre = _LIVE_BODY
    post = _LIVE_BODY.replace("1274.890", "1299.990", 1)
    assert post != pre

    src = AstockIntradaySource()
    received: list[Any] = []
    src.on_ticker(received.append)
    src._subscribed = {"600519.SH"}

    _drive_outage(
        src,
        script=[pre, None, None, post],
        monkeypatch=monkeypatch,
    )
    # pre published, outage silent, changed post publishes again.
    assert [e.last_price for e in received] == [1274.890, 1299.990]


def test_identical_post_outage_snapshot_is_dedup_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unchanged snapshot after recovery does NOT republish.

    It carries no new information; suppressing it is correct and is not a hole.
    Pins the plan's §5(d) wording: the first *changed* snapshot is published.
    """
    src = AstockIntradaySource()
    received: list[Any] = []
    src.on_ticker(received.append)
    src._subscribed = {"600519.SH"}

    episodes = _drive_outage(
        src,
        script=[_LIVE_BODY, None, None, _LIVE_BODY],  # identical on recovery
        monkeypatch=monkeypatch,
    )
    assert len(episodes) == 2  # outage recorded
    assert len(received) == 1  # pre published once; identical post suppressed


def test_format_break_does_not_close_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 whose body yields no payload must NOT close the outage.

    Guards the source-level wiring of the tracker's ``healthy`` rule: transport
    success is not recovery. The format break is the *last* pass, so a correct
    implementation leaves the episode open (only the open was emitted); an
    implementation that closed on transport success would emit a close here.
    """
    src = AstockIntradaySource()
    src._subscribed = {"600519.SH"}
    format_break = 'var hq_str_other="x";'  # no valid hq_str_<board><code> line

    episodes = _drive_outage(
        src,
        script=[None, format_break],
        monkeypatch=monkeypatch,
    )

    # Only the open reached the sink — the empty 200 did not count as recovery.
    assert len(episodes) == 1
    assert episodes[0].closed_at_ms is None
    assert src._outage_tracker.open_episode() is not None  # still open


def test_no_outage_no_sink_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Healthy polling never opens an episode, so the sink stays silent."""
    monkeypatch.setattr(
        "selene_space_market.sources.astock_intraday_source.requests.get",
        lambda *a, **k: _FakeResp(_LIVE_BODY),
    )
    src = AstockIntradaySource()
    episodes: list[Any] = []
    src.set_gap_sink(episodes.append)
    src._subscribed = {"600519.SH"}

    asyncio.run(_run_passes(src, passes=3))

    assert episodes == []
    assert src._outage_tracker.open_episode() is None


def test_outage_bracket_uses_the_parsed_volume_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gap's volume bracket comes off the wire, not from a stub.

    The pre-gap pass and the post-gap pass carry different field-8 values for
    the same symbol; the delta the tracker reports must equal that difference.
    This is what proves the plumbing is live end-to-end — a bracket computed
    from anything other than the parsed payload would not reproduce it.
    """
    # Field 8 is the 9th comma-separated value; bump 600519.SH's 2657276 → ...676.
    pre = _LIVE_BODY
    post = _LIVE_BODY.replace(",2657276,", ",2657776,", 1)
    assert post != pre

    src = AstockIntradaySource()
    src._subscribed = {"600519.SH"}
    episodes = _drive_outage(
        src,
        script=[pre, None, None, post],
        monkeypatch=monkeypatch,
    )

    closed = episodes[-1]
    assert closed.closed_at_ms is not None
    assert closed.volume_symbols == 1
    # 000001.SZ never came back in this subscription's batch, so only 600519
    # counts — and its bracket is exactly the field-8 movement.
    assert closed.volume_delta == 500.0


def test_open_episode_snapshot_carries_the_pre_gap_volume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The episode emitted at *open* already knows the pre-gap volume.

    The open row is what a mid-outage restart leaves behind, so the "before"
    side has to be on it — otherwise that row could never be completed later.
    """
    src = AstockIntradaySource()
    src._subscribed = {"600519.SH"}
    episodes = _drive_outage(
        src,
        script=[_LIVE_BODY, None, _LIVE_BODY],
        monkeypatch=monkeypatch,
    )

    opened = episodes[0]
    assert opened.closed_at_ms is None
    assert opened.volume_before == 2_657_276.0
    assert opened.volume_after is None
    assert opened.volume_delta is None


def test_outage_without_a_pre_gap_tick_reports_no_bracket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source that goes dark before ever polling has no "before" to measure from.

    The bracket must be absent rather than zero: we genuinely do not know how
    much traded, and S2 does not fabricate.
    """
    src = AstockIntradaySource()
    src._subscribed = {"600519.SH"}
    episodes = _drive_outage(
        src,
        script=[None, None, _LIVE_BODY],
        monkeypatch=monkeypatch,
    )

    closed = episodes[-1]
    assert closed.closed_at_ms is not None
    assert closed.volume_before is None
    assert closed.volume_after is None
    assert closed.volume_delta is None
    assert closed.volume_symbols == 0


def test_stale_post_outage_timestamp_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Recovery whose venue timestamp lags wall clock must warn once per episode.

    Catches "reconnected but Sina serves stale snapshots". The warning is
    per-episode, not per-symbol (an 80-symbol batch must not emit 80 lines).
    """
    stale = _LIVE_BODY  # fixed 2026-10-13 15:34:59 far behind real "now"
    src = AstockIntradaySource()
    src._subscribed = {"600519.SH", "000001.SZ"}

    with caplog.at_level(logging.WARNING):
        _drive_outage(src, script=[None, None, stale], monkeypatch=monkeypatch)

    stale_warnings = [r for r in caplog.records if "stale" in r.getMessage().lower()]
    assert len(stale_warnings) == 1

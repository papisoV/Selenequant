"""Offline coverage for AstockAnomalyDetector — hand-built tickers, fake
context, no HTTP/SQLite: limit touch→hold→break→re-touch, limit-down→recovery,
N-day high/low once-per-session suppression, volume surge against fake daily
bars, the session gate, cross-day state reset, and listener isolation."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from selene_space_market.aggregators.astock_anomaly import (
    AstockAnomalyDetector,
)
from selene_space_types.market import AnomalyEvent, KlineEvent, TickerEvent

# 2026-10-12 is a Monday, 2026-10-13 Tuesday (both trading days for fixtures).
DAY1 = "2026-10-12"
DAY2 = "2026-10-13"
PREV_CLOSE = 10.0
UP_PRICE = 11.0  # 10.0 * 1.10, main-board limit
DOWN_PRICE = 9.0

DAY1_MS = 1791770400000  # 2026-10-12 10:00:00 +08:00
DAY2_MS = 1791856800000  # 2026-10-13 10:00:00 +08:00
DAY1_PREOPEN_MS = 1791768300000  # 2026-10-12 09:25:00 +08:00 (竞价, before open)


def _fixed_limit_up(prev_close: float, code: str, name: str = "") -> Decimal:
    return Decimal(str(prev_close)) * Decimal("1.10")


def _fixed_limit_down(prev_close: float, code: str, name: str = "") -> Decimal:
    return Decimal(str(prev_close)) * Decimal("0.90")


def _ticker(
    price: float,
    *,
    ts: int = DAY1_MS,
    symbol: str = "600519.SH",
    volume: float = 1000.0,
    name: str = "贵州茅台",
    prev_close: float | None = None,
) -> TickerEvent:
    return TickerEvent(
        exchange="astock",
        symbol=symbol,
        timestamp=ts,
        last_price=price,
        bid=price,
        ask=price,
        volume_24h=volume,
        change_24h_pct=0.0,
        name=name,
        prev_close=prev_close,
    )


class FakeContext:
    """Minimal context: one symbol, one prev_close, configurable daily bars."""

    def __init__(
        self,
        prev_close: float | None = PREV_CLOSE,
        bars: list[KlineEvent] | None = None,
        name: str = "贵州茅台",
    ) -> None:
        self._prev_close = prev_close
        self._bars = bars or []
        self._name = name
        self.bars_requested = 0

    def prev_close(self, symbol: str) -> float | None:
        return self._prev_close

    def name(self, symbol: str) -> str:
        return self._name

    def prior_daily_bars(self, symbol: str, n: int) -> list[KlineEvent]:
        self.bars_requested += 1
        return self._bars[:n]


def _bar(
    close: float, *, high: float | None = None, volume: float = 1_000_000.0
) -> KlineEvent:
    return KlineEvent(
        exchange="astock",
        market_type="spot",
        symbol="600519.SH",
        timestamp=DAY1_MS,
        interval="1d",
        open=close,
        high=high if high is not None else close + 0.01,
        low=close - 0.01,
        close=close,
        volume=volume,
        closed=True,
    )


def _detector(
    context: FakeContext | None = None, **kwargs: Any
) -> AstockAnomalyDetector:
    return AstockAnomalyDetector(
        context=context or FakeContext(),
        limit_up_price=_fixed_limit_up,
        limit_down_price=_fixed_limit_down,
        **kwargs,
    )


class TestEdgeTriggered:
    def test_limit_up_hold_break_re_limit(self) -> None:
        det = _detector()
        events = []
        for price in (10.5, UP_PRICE, UP_PRICE, 10.8, UP_PRICE):
            events.extend(det.process(_ticker(price)))
        kinds = [e.kind for e in events]
        # touch fires once on entering limit, break fires on leaving,
        # re-touch fires again — the whole cycle in one session.
        assert kinds == ["limit_up", "broken_limit_up", "limit_up"]

    def test_limit_down_then_recovery(self) -> None:
        det = _detector()
        events = det.process(_ticker(DOWN_PRICE)) + det.process(_ticker(9.5))
        assert [e.kind for e in events] == ["limit_down", "limit_down_recovery"]

    def test_stale_ticker_below_limit_does_not_refire(self) -> None:
        det = _detector()
        assert [e.kind for e in det.process(_ticker(UP_PRICE))] == ["limit_up"]
        # holding at limit: no duplicate edge
        assert det.process(_ticker(UP_PRICE)) == []


class TestLevelTriggered:
    # These exercise the level mechanics, not the baseline-depth guard, so they
    # lower min_baseline_days to keep their 1-bar fixtures meaningful.
    def test_n_day_high_and_low_fire_once_per_session(self) -> None:
        # Bar high 10.9 keeps ordinary 10.5/9.6 ticks inside the range so only
        # deliberate extremes flag; bar low 9.5 stays above the limit-down
        # price so the day-2 low tick does not double as a limit-down edge.
        bars = [_bar(9.0, high=10.8, volume=1_000_000.0)]
        bars[0] = bars[0].model_copy(update={"low": 9.5})
        context = FakeContext(bars=bars)
        det = _detector(context, min_baseline_days=1)
        first = det.process(_ticker(9.6))
        assert first == []
        # 10.9 clears the prior high (10.8) but stays below the limit-up
        # price (11.0), so exactly one kind fires.
        assert [e.kind for e in det.process(_ticker(10.9))] == ["n_day_high"]
        assert det.process(_ticker(9.8)) == []  # same session: suppressed
        second = det.process(_ticker(9.2, ts=DAY2_MS))
        assert [e.kind for e in second] == ["n_day_low"]  # new session: fires

    def test_volume_surge_against_fake_bars(self) -> None:
        # mean daily volume 1M; at 10:00 the elapsed fraction is 30/330,
        # so the surge threshold is 3.0 * 1M * 30/330 = ~272_727.
        bars = [_bar(10.0, high=10.6, volume=1_000_000.0)]
        det = _detector(FakeContext(bars=bars), min_baseline_days=1)
        calm = det.process(_ticker(10.5, volume=100_000.0))
        assert calm == []
        surge = det.process(_ticker(10.5, volume=400_000.0))
        assert [e.kind for e in surge] == ["volume_surge"]
        # once per session
        assert det.process(_ticker(10.5, volume=900_000.0)) == []

    def test_no_surge_before_session_open(self) -> None:
        bars = [_bar(10.0, high=10.6, volume=1_000_000.0)]
        det = _detector(FakeContext(bars=bars), min_baseline_days=1)
        # 09:25 pre-open: fraction 0 — must not fire on any volume.
        assert det.process(_ticker(10.5, ts=DAY1_PREOPEN_MS, volume=5_000_000.0)) == []


class TestBaselineGuard:
    """A "20-day high" needs 20 days of history. Below the floor the level and
    volume kinds stay silent instead of firing on an absent baseline; the edge
    kinds (limit touch/break) carry no such dependency."""

    def _shallow_context(self, n_bars: int) -> FakeContext:
        return FakeContext(
            bars=[_bar(10.0, high=10.2, volume=1_000_000.0) for _ in range(n_bars)]
        )

    def test_single_bar_history_suppresses_level_and_volume_kinds(self) -> None:
        det = _detector(self._shallow_context(1))  # default floor = 20
        # price far above the sole bar's high, volume far above its mean
        events = det.process(_ticker(10.99, volume=9_000_000.0))
        assert [e.kind for e in events] == []

    def test_edge_kinds_still_fire_with_shallow_history(self) -> None:
        # The guard must not touch limit touch/break — those read prev_close,
        # not the N-day window.
        det = _detector(self._shallow_context(1))
        assert [e.kind for e in det.process(_ticker(UP_PRICE))] == ["limit_up"]

    def test_floor_boundary(self) -> None:
        # volume kept under the surge threshold so only the level kind is under
        # test (mean 1M, elapsed 30/330 → threshold ≈ 272k).
        below = _detector(self._shallow_context(19), min_baseline_days=20)
        assert below.process(_ticker(10.99, volume=1_000.0)) == []
        at = _detector(self._shallow_context(20), min_baseline_days=20)
        assert [e.kind for e in at.process(_ticker(10.99, volume=1_000.0))] == [
            "n_day_high"
        ]

    def test_guard_is_configurable_to_zero(self) -> None:
        # min_baseline_days=0 restores the pre-guard behaviour (any history).
        det = _detector(self._shallow_context(1), min_baseline_days=0)
        assert [e.kind for e in det.process(_ticker(10.99, volume=1_000.0))] == [
            "n_day_high"
        ]


class TestSessionGate:
    def test_closed_gate_suppresses_everything(self) -> None:
        det = _detector(is_session_open=lambda: False)
        assert det.process(_ticker(UP_PRICE)) == []
        assert det.process(_ticker(9.0)) == []
        assert det.get_recent() == []

    def test_open_gate_passes(self) -> None:
        det = _detector(is_session_open=lambda: True)
        assert [e.kind for e in det.process(_ticker(UP_PRICE))] == ["limit_up"]


class TestCrossDayReset:
    def test_state_resets_across_day_boundary(self) -> None:
        det = _detector()
        # Day 1: end the session while at limit-up.
        assert [e.kind for e in det.process(_ticker(UP_PRICE))] == ["limit_up"]
        # Day 2 first tick at limit again: state was reset, edge must refire.
        assert [e.kind for e in det.process(_ticker(UP_PRICE, ts=DAY2_MS))] == [
            "limit_up"
        ]

    def test_emitted_set_resets_across_day_boundary(self) -> None:
        bars = [_bar(9.0, high=9.5)]
        context = FakeContext(bars=bars)
        det = _detector(context, min_baseline_days=1)
        assert [e.kind for e in det.process(_ticker(9.6))] == ["n_day_high"]
        # Day 2: the once-per-session suppression must not carry over.
        assert [e.kind for e in det.process(_ticker(9.6, ts=DAY2_MS))] == ["n_day_high"]


class TestFanout:
    def test_listener_receives_events(self) -> None:
        det = _detector()
        seen: list[AnomalyEvent] = []
        det.on_anomaly(seen.append)
        det.process(_ticker(UP_PRICE))
        assert [e.kind for e in seen] == ["limit_up"]

    def test_listener_exception_swallowed(self) -> None:
        det = _detector()
        seen: list[AnomalyEvent] = []
        det.on_anomaly(seen.append)

        def boom(event: AnomalyEvent) -> None:
            raise RuntimeError("listener blew up")

        det.on_anomaly(boom)
        events = det.process(_ticker(UP_PRICE))
        # the failing listener did not lose the event or kill processing
        assert [e.kind for e in events] == ["limit_up"]
        assert [e.kind for e in seen] == ["limit_up"]


class TestMissingContext:
    def test_no_prev_close_no_events(self) -> None:
        det = _detector(FakeContext(prev_close=None))
        assert det.process(_ticker(UP_PRICE)) == []


class TestPrevCloseSource:
    """The venue's own previous close (ticker.prev_close) is the limit basis;
    the daily-bar context is only a fallback. FakeContext's prev_close is 10.0
    (limit-up 11.0) while the ticker carries 12.0 (limit-up 13.2), so the two
    bases are distinguishable by which price fires."""

    def test_ticker_prev_close_wins_over_context(self) -> None:
        det = _detector(FakeContext(prev_close=10.0))
        # Under the context basis (10.0) an 11.0 tick is a limit-up; under the
        # ticker basis (12.0) it is not. It must NOT fire → ticker basis won.
        assert det.process(_ticker(11.0, prev_close=12.0)) == []
        # 13.2 is exactly +10% of the ticker's 12.0 → fires, carrying 12.0.
        (event,) = det.process(_ticker(13.2, prev_close=12.0))
        assert event.kind == "limit_up"
        assert event.prev_close == 12.0

    def test_context_used_when_ticker_carries_none(self) -> None:
        # Binance-style tickers publish no prev close; the context still drives.
        det = _detector(FakeContext(prev_close=10.0))
        (event,) = det.process(_ticker(UP_PRICE, prev_close=None))
        assert event.kind == "limit_up"
        assert event.prev_close == 10.0

    def test_context_used_when_ticker_prev_close_non_positive(self) -> None:
        # A degenerate 0/negative field must not zero out the limit basis.
        det = _detector(FakeContext(prev_close=10.0))
        (event,) = det.process(_ticker(UP_PRICE, prev_close=0.0))
        assert event.kind == "limit_up"
        assert event.prev_close == 10.0

    def test_no_basis_at_all_no_events(self) -> None:
        det = _detector(FakeContext(prev_close=None))
        assert det.process(_ticker(UP_PRICE, prev_close=None)) == []


class TestGetRecent:
    def test_get_recent_bounded(self) -> None:
        det = _detector()
        for price in (UP_PRICE, 10.5, UP_PRICE):
            det.process(_ticker(price))
        recent = det.get_recent(limit=1)
        assert len(recent) == 1
        assert recent[0].kind == "limit_up"


class TestNamePropagation:
    """The venue name enters via the ticker (Sina field 0 → TickerEvent.name)
    and must survive onto the AnomalyEvent; a context-only provider is the
    fallback."""

    def test_ticker_name_wins_over_empty_context_name(self) -> None:
        det = _detector(FakeContext(name=""))
        (event,) = det.process(_ticker(UP_PRICE, name="ST 退市海润"))
        assert event.name == "ST 退市海润"

    def test_falls_back_to_context_name_when_ticker_blank(self) -> None:
        det = _detector(FakeContext(name="context名"))
        (event,) = det.process(_ticker(UP_PRICE, name=""))
        assert event.name == "context名"

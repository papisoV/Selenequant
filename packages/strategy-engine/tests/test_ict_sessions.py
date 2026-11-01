"""Tests for the ICT killzone gate and premium/discount dealing-range zoning.

Both detectors sit in front of the existing structure layer: a killzone says
*when* price is worth watching, premium/discount says *where in the range* an
entry is chasing. Locks here cover the three things a silent failure would
hide — the signal encoding, the causality of the range endpoints, and the
warm-up contract inherited from :class:`Indicator`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from selene_space_strategy.indicators.ict import FVG
from selene_space_strategy.indicators.premium_discount import PremiumDiscount
from selene_space_strategy.indicators.registry import indicator_registry
from selene_space_strategy.indicators.sessions import KILLZONES, KillzoneIndicator
from selene_space_strategy.models import EngineStrategyConfig


def _df(highs, lows, closes=None, opens=None, index=None):
    """OHLC frame with an optional index; flat open/close by default."""
    if opens is None:
        opens = list(lows)
    if closes is None:
        closes = [(h + lo) / 2 for h, lo in zip(highs, lows)]
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes},
        index=index,
    )


def _hourly(n: int, start: str = "2026-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="1h", tz="UTC")


def _random_frame(n: int = 400, seed: int = 7, tz: str | None = "UTC") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + rng.normal(0, 1, n).cumsum()
    index = pd.date_range("2026-01-01", periods=n, freq="1h", tz=tz)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + np.abs(rng.normal(0, 0.6, n)),
            "low": close - np.abs(rng.normal(0, 0.6, n)),
            "close": close,
            "volume": rng.uniform(100, 1000, n),
        },
        index=index,
    )


# ---------------------------------------------------------------------------
# Killzone
# ---------------------------------------------------------------------------


class TestKillzoneEncoding:
    def test_signal_is_binary_and_warm_up_free(self) -> None:
        """A gate is knowable from bar 0: no NaN, only 0.0 / 1.0."""
        frame = _random_frame(n=48)
        result = KillzoneIndicator(period=1, tz_offset_hours=0).calculate(frame)
        assert set(result.unique()) <= {0.0, 1.0}
        assert not result.isna().any()
        assert len(result) == len(frame)

    def test_hours_follow_the_offset_not_utc(self) -> None:
        """A single UTC hour maps to exactly one local hour per offset."""
        frame = _random_frame(n=24)
        for offset in (-5, 0, 3):
            result = KillzoneIndicator(
                period=1, sessions="london", tz_offset_hours=offset
            ).calculate(frame)
            by_hour = result.groupby(frame.index.hour).first()
            start, end = KILLZONES["london"]
            expected = {((h + offset) % 24) for h in range(24)}
            inside = {h for h in expected if start <= (h + offset) % 24 < end}
            actual = {int(h) for h, v in by_hour.items() if v == 1.0}
            assert actual == inside, f"offset {offset}"

    def test_offset_is_not_applied_twice_on_aware_index(self) -> None:
        """A tz-aware index is normalised to UTC before the offset shifts it."""
        frame = _random_frame(n=24)
        utc = KillzoneIndicator(period=1, tz_offset_hours=-5).calculate(frame)
        tokyo = frame.copy()
        tokyo.index = frame.index.tz_convert("Asia/Tokyo")
        shifted = KillzoneIndicator(period=1, tz_offset_hours=-5).calculate(tokyo)
        assert utc.tolist() == shifted.tolist()

    def test_overlapping_sessions_union(self) -> None:
        """Bar inside either enabled window reads 1.0."""
        frame = _random_frame(n=200)
        broad = KillzoneIndicator(
            period=1, sessions="london,new_york", tz_offset_hours=0
        ).calculate(frame)
        london = KillzoneIndicator(
            period=1, sessions="london", tz_offset_hours=0
        ).calculate(frame)
        ny = KillzoneIndicator(
            period=1, sessions="new_york", tz_offset_hours=0
        ).calculate(frame)
        union = ((london + ny) > 0).astype(float)
        assert broad.tolist() == union.tolist()


class TestKillzoneValidation:
    def test_unknown_session_raises(self) -> None:
        with pytest.raises(ValueError, match="tokyo"):
            KillzoneIndicator(period=1, sessions="tokyo")

    def test_empty_session_list_raises(self) -> None:
        with pytest.raises(ValueError, match="At least one"):
            KillzoneIndicator(period=1, sessions="  ,  ")

    def test_windows_are_exposed_without_mutating_internal_state(self) -> None:
        indicator = KillzoneIndicator(period=1, sessions="london,new_york")
        windows = indicator.windows
        assert [w.name for w in windows] == ["london", "new_york"]
        windows.clear()
        assert len(indicator.windows) == 2

    def test_repr_is_inherited(self) -> None:
        assert "KillzoneIndicator" in repr(KillzoneIndicator(period=1))


class TestKillzoneClockSources:
    """Time comes from the index, else from the runtime's ``timestamp`` column.

    A RangeIndex frame with no timestamp column cannot be clocked. Emitting
    zeros there would read as "never in a killzone" — a claim about the
    market — so the whole series is NaN and ``clock_source`` says why.
    """

    def test_range_index_without_timestamp_column_is_unknown(self) -> None:
        frame = _random_frame(n=20).reset_index(drop=True)
        indicator = KillzoneIndicator(period=1)
        result = indicator.calculate(frame)
        assert result.isna().all()
        assert indicator.clock_source is None
        assert result.index.equals(frame.index)

    def test_timestamp_column_milliseconds_is_used(self) -> None:
        """The StrategyRuntime path: RangeIndex + epoch-ms ``timestamp``."""
        source = _random_frame(n=48)
        frame = source.reset_index(drop=True)
        frame["timestamp"] = (source.index.view("int64") // 1_000_000).astype("int64")

        indicator = KillzoneIndicator(period=1, sessions="london", tz_offset_hours=0)
        from_column = indicator.calculate(frame)
        assert indicator.clock_source == "timestamp"

        by_index = KillzoneIndicator(
            period=1, sessions="london", tz_offset_hours=0
        ).calculate(source)
        assert from_column.tolist() == by_index.tolist()

    def test_timestamp_column_seconds_is_inferred(self) -> None:
        source = _random_frame(n=48)
        frame = source.reset_index(drop=True)
        frame["timestamp"] = (source.index.view("int64") // 1_000_000_000).astype(
            "int64"
        )

        indicator = KillzoneIndicator(period=1, sessions="london", tz_offset_hours=0)
        from_column = indicator.calculate(frame)
        by_index = KillzoneIndicator(
            period=1, sessions="london", tz_offset_hours=0
        ).calculate(source)
        assert from_column.tolist() == by_index.tolist()

    def test_ordinal_timestamp_column_is_not_guessed(self) -> None:
        """Small integers are bar ordinals, not epochs — do not invent a clock."""
        frame = _random_frame(n=20).reset_index(drop=True)
        frame["timestamp"] = range(len(frame))
        indicator = KillzoneIndicator(period=1)
        assert indicator.calculate(frame).isna().all()
        assert indicator.clock_source is None

    def test_unparseable_timestamp_column_is_not_guessed(self) -> None:
        frame = _random_frame(n=20).reset_index(drop=True)
        frame["timestamp"] = ["not-a-time"] * len(frame)
        indicator = KillzoneIndicator(period=1)
        assert indicator.calculate(frame).isna().all()

    def test_partially_null_timestamp_column_is_not_guessed(self) -> None:
        frame = _random_frame(n=20).reset_index(drop=True)
        stamps = pd.Series(np.arange(len(frame)) * 3_600_000 + 1_700_000_000_000)
        stamps.iloc[3] = np.nan
        frame["timestamp"] = stamps
        indicator = KillzoneIndicator(period=1)
        assert indicator.calculate(frame).isna().all()

    def test_index_wins_over_column_when_both_present(self) -> None:
        frame = _random_frame(n=48)
        frame["timestamp"] = np.arange(len(frame))  # deliberately bogus ordinal
        indicator = KillzoneIndicator(period=1, sessions="london", tz_offset_hours=0)
        result = indicator.calculate(frame)
        assert indicator.clock_source == "index"
        assert not result.isna().any()

    @pytest.mark.parametrize("scale", [1_000, 1_000_000])
    def test_sub_millisecond_epoch_does_not_raise(self, scale: int) -> None:
        """Micro/nanosecond epochs overflow pandas' ns range.

        The warm-up path in ``strategy_runner`` has no exception guard, so a
        raise here would abort a deployment rather than fail one bar.
        """
        frame = _random_frame(n=12).reset_index(drop=True)
        base = 1_700_000_000_000
        frame["timestamp"] = (
            np.arange(len(frame), dtype=np.int64) * scale + base * scale
        )
        indicator = KillzoneIndicator(period=1)
        result = indicator.calculate(frame)
        assert result.isna().all()
        assert indicator.clock_source is None

    def test_absurd_epoch_magnitude_is_not_wrapped(self) -> None:
        """1e20 wraps to a negative int64 and would read as an ancient date."""
        frame = _random_frame(n=12).reset_index(drop=True)
        frame["timestamp"] = np.full(len(frame), 1e20)
        indicator = KillzoneIndicator(period=1)
        assert indicator.calculate(frame).isna().all()
        assert indicator.clock_source is None

    def test_datetime_column_with_nat_is_not_partially_clocked(self) -> None:
        """A NaT row would read as "outside the window" rather than unknown."""
        frame = _random_frame(n=12).reset_index(drop=True)
        stamps = pd.Series(
            pd.to_datetime(
                np.arange(len(frame)) * 3_600_000 + 1_700_000_000_000, unit="ms"
            )
        )
        stamps.iloc[4] = pd.NaT
        frame["timestamp"] = stamps
        indicator = KillzoneIndicator(period=1)
        assert indicator.calculate(frame).isna().all()
        assert indicator.clock_source is None


# ---------------------------------------------------------------------------
# Premium / discount
# ---------------------------------------------------------------------------


class TestPremiumDiscountEncoding:
    def test_three_states_and_nan_warm_up(self) -> None:
        frame = _random_frame(n=300)
        result = PremiumDiscount(period=10).calculate(frame)
        assert set(result.dropna().unique()) <= {-1.0, 0.0, 1.0}
        assert result.isna().any(), "warm-up must precede the first swing pair"
        assert result.notna().any(), "post-warm-up bars must be labelled"

    def test_all_three_zones_are_reachable(self) -> None:
        """A detector that never leaves equilibrium would still pass a
        subset check, so assert each state actually occurs."""
        frame = _random_frame(n=400)
        result = PremiumDiscount(period=10).calculate(frame)
        for state in (-1.0, 0.0, 1.0):
            assert (result == state).any(), f"state {state} never emitted"

    def test_upper_third_is_premium_lower_third_is_discount(self) -> None:
        """Pin the encoding against the range the detector itself reports."""
        frame = _random_frame(n=300)
        indicator = PremiumDiscount(period=10)
        result = indicator.calculate(frame)
        latest = indicator.get_latest_range()
        assert latest is not None
        close = float(frame["close"].iloc[-1])
        if close >= latest["premium_from"]:
            assert result.iloc[-1] == -1.0
        elif close <= latest["discount_to"]:
            assert result.iloc[-1] == 1.0
        else:
            assert result.iloc[-1] == 0.0

    def test_equilibrium_sits_at_the_midpoint(self) -> None:
        frame = _random_frame(n=200)
        indicator = PremiumDiscount(period=10)
        indicator.calculate(frame)
        latest = indicator.get_latest_range()
        assert latest is not None
        expected = (latest["low"] + latest["high"]) / 2
        assert latest["equilibrium"] == pytest.approx(expected)

    def test_range_endpoints_are_real_swing_extremes(self) -> None:
        frame = _random_frame(n=200)
        indicator = PremiumDiscount(period=10)
        indicator.calculate(frame)
        latest = indicator.get_latest_range()
        assert latest is not None
        assert latest["high"] >= latest["low"]
        assert latest["high"] <= float(frame["high"].max())
        assert latest["low"] >= float(frame["low"].min())


class TestPremiumDiscountCausality:
    def test_prefix_recompute_matches_full_series(self) -> None:
        """No label may depend on data past its own bar."""
        frame = _random_frame(n=300)
        full = PremiumDiscount(period=10).calculate(frame)
        for cut in (80, 150, 220):
            prefix = PremiumDiscount(period=10).calculate(frame.iloc[:cut])
            expected = full.iloc[:cut]
            both_nan = prefix.isna() & expected.isna()
            assert ((prefix == expected) | both_nan).all(), f"cut={cut}"

    def test_latest_range_is_none_before_warm_up(self) -> None:
        frame = _random_frame(n=15)
        indicator = PremiumDiscount(period=10)
        indicator.calculate(frame)
        assert indicator.get_latest_range() is None

    def test_no_lookahead_on_degenerate_range(self) -> None:
        """A flat frame has no span; it must report "no reading", not equilibrium.

        0.0 would be indistinguishable from equilibrium and would satisfy a
        ``>= 0`` exit rule, closing a position on unusable data.
        """
        frame = _df(
            highs=[10.0] * 40,
            lows=[10.0] * 40,
            closes=[10.0] * 40,
            index=_hourly(40),
        )
        indicator = PremiumDiscount(period=5)
        result = indicator.calculate(frame)
        assert not np.isinf(result.dropna()).any()
        assert result.isna().all()
        assert indicator.get_latest_range() is None


class TestPremiumDiscountValidation:
    @pytest.mark.parametrize(
        "premium, discount",
        [(0.3, 0.4), (1.0, 0.3), (0.7, 0.0), (0.5, 0.5), (0.6, -0.1)],
    )
    def test_bad_thresholds_raise(self, premium: float, discount: float) -> None:
        with pytest.raises(ValueError):
            PremiumDiscount(
                period=10, premium_threshold=premium, discount_threshold=discount
            )

    def test_default_thresholds_are_thirds(self) -> None:
        indicator = PremiumDiscount(period=10)
        assert indicator._discount == pytest.approx(0.3333)
        assert indicator._premium == pytest.approx(0.6667)


# ---------------------------------------------------------------------------
# Registry and compiler plumbing
# ---------------------------------------------------------------------------


class TestRegistrationAndPlumbing:
    def test_both_detectors_are_registered(self) -> None:
        assert indicator_registry.has("killzone")
        assert indicator_registry.has("premium_discount")

    def test_killzone_constructs_from_yaml_params(self) -> None:
        """``sessions``/``tz_offset_hours`` must survive compilation."""
        from selene_space_strategy.compiler import _constructor_kwargs

        factory = indicator_registry.get("killzone")
        kwargs = _constructor_kwargs(
            factory,
            "killzone",
            {"period": 1, "sessions": "london", "tz_offset_hours": -5},
        )
        assert kwargs == {
            "period": 1,
            "sessions": "london",
            "tz_offset_hours": -5,
        }
        instance = factory(**kwargs)
        assert isinstance(instance, KillzoneIndicator)
        assert [w.name for w in instance.windows] == ["london"]

    def test_premium_discount_constructs_from_yaml_params(self) -> None:
        from selene_space_strategy.compiler import _constructor_kwargs

        factory = indicator_registry.get("premium_discount")
        kwargs = _constructor_kwargs(
            factory,
            "premium_discount",
            {"period": 10, "premium_threshold": 0.7, "discount_threshold": 0.3},
        )
        assert kwargs["premium_threshold"] == 0.7
        assert kwargs["discount_threshold"] == 0.3
        assert isinstance(kwargs["period"], int)

    def test_fvg_min_gap_atr_survives_compilation(self) -> None:
        """Regression: the old whitelist dropped every non-period param."""
        from selene_space_strategy.compiler import _constructor_kwargs

        factory = indicator_registry.get("fvg")
        kwargs = _constructor_kwargs(factory, "fvg", {"period": 3, "min_gap_atr": 0.5})
        assert kwargs == {"period": 3, "min_gap_atr": 0.5}
        instance = factory(**kwargs)
        assert isinstance(instance, FVG)
        assert instance._min_gap_atr == 0.5

    def test_float_param_declared_as_int_is_coerced(self) -> None:
        """YAML writes ``3`` for a float param; the constructor must see 3.0."""
        from selene_space_strategy.compiler import _constructor_kwargs

        factory = indicator_registry.get("fvg")
        kwargs = _constructor_kwargs(factory, "fvg", {"period": 3, "min_gap_atr": 1})
        assert kwargs["min_gap_atr"] == 1.0
        assert isinstance(kwargs["min_gap_atr"], float)

    def test_float_window_coerced_when_default_is_absent(self) -> None:
        """Regression: ``sma.period`` declares no default, only ``int``.

        Coercion used to key off the declared default alone, so a param with
        no default fell through uncoerced and ``20.0`` reached
        ``rolling(period)``; pandas only rejects it once the first bar is
        computed, long after compilation looked fine.
        """
        from selene_space_strategy.compiler import _constructor_kwargs

        for name in ("sma", "ema", "atr", "close"):
            factory = indicator_registry.get(name)
            kwargs = _constructor_kwargs(factory, name, {"period": 20.0})
            assert kwargs["period"] == 20
            assert isinstance(kwargs["period"], int), name

    def test_float_window_coerced_when_default_is_none(self) -> None:
        """``adx.adx_period`` is declared ``int | None = None``, same trap."""
        from selene_space_strategy.compiler import _constructor_kwargs

        factory = indicator_registry.get("adx")
        kwargs = _constructor_kwargs(factory, "adx", {"period": 14, "adx_period": 20.0})
        assert kwargs["adx_period"] == 20
        assert isinstance(kwargs["adx_period"], int)

    def test_no_param_rule_keeps_the_legacy_period(self) -> None:
        """A param-less rule is built with period=14, as it always has been.

        This is *not* the constructor default for ``bbands`` (20). Changing it
        would silently rewrite the signals of shipped templates, so the legacy
        value is pinned here until those templates are reworked.
        """
        from selene_space_strategy.compiler import _constructor_kwargs

        factory = indicator_registry.get("bbands")
        assert _constructor_kwargs(factory, "bbands", {}) == {}
        instance = factory(period=14)
        assert instance._period == 14


# ---------------------------------------------------------------------------
# Runtime integration
# ---------------------------------------------------------------------------


def _hourly_events(close: np.ndarray, start_ms: int) -> list:
    from selene_space_types.market import KlineEvent

    return [
        KlineEvent(
            exchange="test",
            symbol="BTC/USDT",
            timestamp=start_ms + i * 3_600_000,
            interval="1h",
            open=float(price),
            high=float(price) + 1.0,
            low=float(price) - 1.0,
            close=float(price),
            volume=1000.0,
            closed=True,
        )
        for i, price in enumerate(close)
    ]


class TestKillzoneUnderRuntime:
    """The production path builds a RangeIndex frame with a ``timestamp`` column.

    ``StrategyRuntime._indicator_frame`` drops the index and keeps time only
    in that column, so a detector that reads the index alone is dead code in
    every backtest. These tests drive ``on_kline`` directly rather than
    ``calculate`` for exactly that reason.
    """

    def _config(self, extra_params: dict | None = None) -> EngineStrategyConfig:
        from selene_space_strategy.models import (
            ConditionRule,
            EntryRules,
            ExitRules,
        )

        params = {"period": 1, "sessions": "london", "tz_offset_hours": 0}
        params.update(extra_params or {})
        entry = ConditionRule(
            indicator="killzone", params=params, operator="greater_than", comparator=0.5
        )
        # Validator rejects an entry side with no matching exit rule.
        exit_rule = ConditionRule(
            indicator="killzone", params=params, operator="less_than", comparator=0.5
        )
        return EngineStrategyConfig(
            id="runtime-killzone",
            name="runtime killzone",
            type="ict",
            symbols=["BTC/USDT"],
            interval="1h",
            parsed_entry_rules=EntryRules(long=[entry], short=[]),
            parsed_exit_rules=ExitRules(close_long=[exit_rule], close_short=[]),
        )

    def test_entries_fire_only_inside_the_window(self) -> None:
        from selene_space_strategy.compiler import compile
        from selene_space_strategy.runtime import StrategyRuntime
        from selene_space_types.strategy import StrategyDirection

        compiled = compile(self._config())
        runtime = StrategyRuntime(
            compiled, max_bars=200, direction=StrategyDirection.LONG_ONLY
        )
        # 2025-01-01T00:00Z is midnight; London with offset 0 is 02:00-05:00.
        start_ms = 1_735_689_600_000
        close = np.full(96, 100.0)
        fired: list[tuple[int, str]] = []
        for i, event in enumerate(_hourly_events(close, start_ms)):
            sig = runtime.on_kline(event)
            if sig is not None:
                # Signal.timestamp is wall clock (time.time()), not bar time —
                # the bar's own hour is the ordinal i.
                fired.append((i % 24, sig.reason))

        entries = [(hour, r) for hour, r in fired if "Long entry" in r]
        assert entries, "killzone never opened a position under the runtime"
        assert all(2 <= hour < 5 for hour, _ in entries), entries

        exits = [(hour, r) for hour, r in fired if "Close long" in r]
        assert exits, "positions never closed"
        assert all(not (2 <= hour < 5) for hour, _ in exits), exits

    def test_unclocked_feed_blocks_every_entry(self) -> None:
        """No usable clock -> NaN column -> the runtime's gate refuses to trade.

        Falling back to zeros instead would quietly run the strategy with no
        session filter at all, which is the failure this guards.
        """
        from selene_space_strategy.compiler import compile
        from selene_space_strategy.runtime import StrategyRuntime
        from selene_space_types.strategy import StrategyDirection

        compiled = compile(self._config())
        runtime = StrategyRuntime(
            compiled, max_bars=200, direction=StrategyDirection.LONG_ONLY
        )
        # Ordinal timestamps: plausible-looking, but not epochs.
        close = np.full(96, 100.0)
        signals = [
            sig
            for i, event in enumerate(_hourly_events(close, 0))
            if (sig := runtime.on_kline(event.model_copy(update={"timestamp": i})))
            is not None
        ]
        assert signals == []

    def test_param_reaches_the_live_instance(self) -> None:
        """The compiled instance must carry the YAML's values, not defaults."""
        from selene_space_strategy.compiler import compile

        compiled = compile(self._config({"sessions": "asia", "tz_offset_hours": 3}))
        (indicator,) = compiled.indicators.values()
        assert isinstance(indicator, KillzoneIndicator)
        assert [w.name for w in indicator.windows] == ["asia"]
        assert indicator._tz_offset_hours == 3

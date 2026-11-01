"""决策信号 outcome 编排 —— 诚实拒答、幂等重评、逐 horizon、统计口径(📋8)。

本文件锁的是**编排层**的行为(取数 → 分类 → 落账),不是纯函数本身
(那在 ``packages/backtest-engine/tests/test_signal_outcome.py``)。
最重要的一条:数据不足时写的是 ``unable`` 而**不是** ``miss``。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.core.decision_signal_outcome import (
    DecisionSignalOutcomeService,
    _aggregate,
    _anchor_date,
    _bars_after,
    _close_on_or_before,
    _horizons_for_signal,
    _needs_recompute,
    _parse_date,
)
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.decision_signals import (
    DecisionSignalRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

ENGINE = "decision-signal-v1"
ANCHOR = "2025-09-01"


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as mgr:
        yield mgr


def _bars(
    closes: list[float], *, anchor_close: float | None = 100.0
) -> list[dict]:
    """锚点日(09-01)一根 + 之后逐日一根。

    前向收益的起点是**锚点当日收盘**(DSA 同此,``get_daily_on_date``),故
    fixture 默认把锚点那根也放进去;``anchor_close=None`` 则只给锚点之后,
    用来造"无锚价"场景。
    """
    bars: list[dict] = []
    if anchor_close is not None:
        bars.append(
            {"date": ANCHOR, "close": anchor_close, "high": anchor_close, "low": anchor_close}
        )
    bars.extend(
        {"date": f"2025-09-{2 + i:02d}", "close": c, "high": c, "low": c}
        for i, c in enumerate(closes)
    )
    return bars


def _service(db, rows: list[dict], **kw) -> DecisionSignalOutcomeService:
    return DecisionSignalOutcomeService(db, fetch_daily=lambda s: rows, **kw)


async def _seed(db, *, action: str = "sell", horizon=None, **overrides) -> int:
    signal = {
        "stock_code": "600519",
        "market": "cn",
        "source_type": "report",
        "source_report_id": "rep-001",
        "trigger_source": "post_market",
        "action": action,
        "plan_quality": "ok",
        "status": "active",
        "horizon": horizon,
        "created_at": f"{ANCHOR}T00:00:00+00:00",
    }
    signal.update(overrides.pop("signal", {}))
    for key, value in overrides.items():
        signal[key] = value
    signal_id, _ = await DecisionSignalRepository(db).create_if_absent(signal)
    return signal_id


# -- 诚实拒答:本批最硬的契约 -------------------------------------------------------


@pytest.mark.asyncio
async def test_insufficient_bars_lands_as_unable_not_miss(db):
    # 只有 2 根 bar,5d 窗口要 5 根 → 必须 unable(数据不足),绝不能记 miss。
    signal_id = await _seed(db, horizon="5d")
    counters = await _service(db, _bars([100.0, 101.0])).run()
    outcome = await DecisionSignalRepository(db).get_outcome(signal_id, "5d", ENGINE)
    assert outcome["eval_status"] == "unable"
    assert outcome["unable_reason"] == "insufficient_forward_bars"
    assert outcome["outcome"] is None  # 没判就没有判定值
    assert counters["unable"] == 1
    assert counters["completed"] == 0


@pytest.mark.asyncio
async def test_fetch_failure_writes_nothing(db):
    def boom(_symbol: str):
        raise RuntimeError("network down")

    signal_id = await _seed(db, horizon="5d")
    service = DecisionSignalOutcomeService(db, fetch_daily=boom)
    counters = await service.run()
    assert counters["skipped_no_data"] == 1
    assert await DecisionSignalRepository(db).list_outcomes(signal_id) == []


@pytest.mark.asyncio
async def test_missing_anchor_price_is_unable(db):
    # 锚点日之前(含当日)没有任何 bar → 没有起点价,不能瞎猜一个当锚。
    signal_id = await _seed(db, horizon="5d")
    counters = await _service(db, _bars([100.0] * 5, anchor_close=None)).run()
    outcome = await DecisionSignalRepository(db).get_outcome(signal_id, "5d", ENGINE)
    assert outcome["eval_status"] == "unable"
    assert outcome["unable_reason"] == "missing_anchor_price"
    assert counters["unable"] == 1


@pytest.mark.asyncio
async def test_watch_action_is_unable_without_reading_prices(db):
    calls: list[str] = []

    def tracking(_symbol: str):
        calls.append(_symbol)
        return _bars([100.0] * 10)

    signal_id = await _seed(db, action="watch", horizon="5d")
    await DecisionSignalOutcomeService(db, fetch_daily=tracking).run()
    outcome = await DecisionSignalRepository(db).get_outcome(signal_id, "5d", ENGINE)
    assert outcome["eval_status"] == "unable"
    assert outcome["unable_reason"] == "non_directional_action"
    assert calls == []  # 非方向 action 根本不该去取行情


# -- 正常判定 --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sell_signal_that_falls_is_a_hit(db):
    # not_up 语义:卖出信号 + 价格下跌 = 判对。这条锁住本批相对 P5 的核心差异。
    signal_id = await _seed(db, action="sell", horizon="5d")
    await _service(db, _bars([100.0, 95.0, 93.0, 90.0, 88.0])).run()
    outcome = await DecisionSignalRepository(db).get_outcome(signal_id, "5d", ENGINE)
    assert outcome["eval_status"] == "completed"
    assert outcome["outcome"] == "hit"
    assert outcome["direction_correct"] == 1


@pytest.mark.asyncio
async def test_buy_signal_that_rises_is_a_hit(db):
    signal_id = await _seed(db, action="buy", horizon="5d", source_report_id="rep-buy")
    await _service(db, _bars([100.0, 102.0, 104.0, 106.0, 108.0])).run()
    outcome = await DecisionSignalRepository(db).get_outcome(signal_id, "5d", ENGINE)
    assert outcome["outcome"] == "hit"


@pytest.mark.asyncio
async def test_anchor_price_uses_the_bar_on_the_anchor_date(db):
    # 锚点当日有 bar → 用它收盘价当起点(不是锚点后第一根)。
    signal_id = await _seed(db, action="buy", horizon="1d", source_report_id="rep-a")
    bars = [
        {"date": ANCHOR, "close": 50.0, "high": 50.0, "low": 50.0},
        {"date": "2025-09-02", "close": 60.0, "high": 60.0, "low": 60.0},
    ]
    await _service(db, bars).run()
    outcome = await DecisionSignalRepository(db).get_outcome(signal_id, "1d", ENGINE)
    assert outcome["start_price"] == pytest.approx(50.0)
    assert outcome["stock_return_pct"] == pytest.approx(20.0)
    assert outcome["outcome"] == "hit"


@pytest.mark.asyncio
async def test_snapshot_dimensions_are_frozen_into_the_outcome(db):
    signal_id = await _seed(
        db, action="sell", horizon="5d", market_phase="postmarket", source_agent="analyst-x"
    )
    await _service(db, _bars([100.0, 90.0, 90.0, 90.0, 90.0])).run()
    outcome = await DecisionSignalRepository(db).get_outcome(signal_id, "5d", ENGINE)
    assert outcome["action"] == "sell"
    assert outcome["market"] == "cn"
    assert outcome["market_phase"] == "postmarket"
    assert outcome["source_agent"] == "analyst-x"
    assert outcome["anchor_date"] == ANCHOR


# -- 逐 horizon ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_without_horizon_evaluates_all_four(db):
    signal_id = await _seed(db, action="sell", horizon=None)
    await _service(db, _bars([100.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0])).run()
    outcomes = await DecisionSignalRepository(db).list_outcomes(signal_id)
    assert {o["horizon"] for o in outcomes} == {"1d", "3d", "5d", "10d"}


@pytest.mark.asyncio
async def test_signal_with_horizon_evaluates_only_that_one(db):
    signal_id = await _seed(db, action="sell", horizon="3d")
    await _service(db, _bars([100.0] * 10)).run()
    outcomes = await DecisionSignalRepository(db).list_outcomes(signal_id)
    assert [o["horizon"] for o in outcomes] == ["3d"]


@pytest.mark.asyncio
async def test_unsupported_horizon_signal_is_skipped_entirely(db):
    signal_id = await _seed(db, action="sell", horizon="swing")
    await _service(db, _bars([100.0] * 10)).run()
    assert await DecisionSignalRepository(db).list_outcomes(signal_id) == []


# -- 幂等 / 重评 ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_after_completion_does_not_duplicate(db):
    signal_id = await _seed(db, action="sell", horizon="5d")
    service = _service(db, _bars([100.0, 90.0, 90.0, 90.0, 90.0]))
    await service.run()
    counters = await service.run()
    repo = DecisionSignalRepository(db)
    assert await repo.count_outcomes(signal_id) == 1
    assert counters["completed"] == 0  # 已完成的行不再重评


@pytest.mark.asyncio
async def test_retryable_unable_is_recomputed_when_data_arrives(db):
    # 第一轮:bar 不够 → unable。第二轮:bar 到齐 → 应重评成 completed。
    signal_id = await _seed(db, action="sell", horizon="5d")
    await _service(db, _bars([100.0, 101.0])).run()
    repo = DecisionSignalRepository(db)
    assert (await repo.get_outcome(signal_id, "5d", ENGINE))["unable_reason"] == (
        "insufficient_forward_bars"
    )
    counters = await _service(db, _bars([100.0, 90.0, 90.0, 90.0, 90.0])).run()
    after = await repo.get_outcome(signal_id, "5d", ENGINE)
    assert after["eval_status"] == "completed"
    assert after["outcome"] == "hit"
    assert after["unable_reason"] is None  # 重评清掉旧因由
    assert counters["retried"] == 1


@pytest.mark.asyncio
async def test_terminal_unable_is_not_recomputed(db):
    # non_directional_action 不可重试 —— 重试一万次也是同一结论。
    signal_id = await _seed(db, action="alert", horizon="5d")
    service = _service(db, _bars([100.0] * 10))
    await service.run()
    counters = await service.run()
    assert counters["retried"] == 0
    repo = DecisionSignalRepository(db)
    assert await repo.count_outcomes(signal_id) == 1


@pytest.mark.asyncio
async def test_archived_signal_is_not_scanned(db):
    signal_id = await _seed(db, action="sell", horizon="5d", source_report_id="rep-arch")
    await DecisionSignalRepository(db).set_status(signal_id, "archived")
    counters = await _service(db, _bars([100.0] * 10)).run()
    assert counters["signals_scanned"] == 0


@pytest.mark.asyncio
async def test_one_bad_signal_does_not_abort_the_run(db):
    # 一只票取数炸了,不该拖垮整轮 —— 另一只票照样要判完(同 P5 的逐条容错)。
    good_id = await _seed(db, action="sell", horizon="5d", source_report_id="rep-good")
    await _seed(
        db, action="sell", horizon="5d", source_report_id="rep-bad", stock_code="BOOM"
    )

    def fetch(symbol: str):
        if symbol == "BOOM":
            raise RuntimeError("upstream exploded")
        return _bars([100.0, 90.0, 90.0, 90.0, 90.0])

    counters = await DecisionSignalOutcomeService(db, fetch_daily=fetch).run()
    assert counters["signals_scanned"] == 2
    assert counters["skipped_no_data"] == 1
    outcome = await DecisionSignalRepository(db).get_outcome(good_id, "5d", ENGINE)
    assert outcome["eval_status"] == "completed"


@pytest.mark.asyncio
async def test_maturity_gate_skips_fresh_signals(db):
    # 锚点是"今天" → 距成熟线 30 天还差得远,不该去取数(粗筛,省 IO)。
    from datetime import UTC, datetime

    today = datetime.now(UTC).isoformat(timespec="seconds")
    signal_id = await _seed(db, action="sell", horizon="5d", created_at=today)
    counters = await _service(db, _bars([100.0] * 10), maturity_days=30).run()
    assert counters["skipped_not_mature"] == 1
    assert await DecisionSignalRepository(db).list_outcomes(signal_id) == []


# -- 统计 ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_aggregates_hit_rate(db):
    sell_id = await _seed(db, action="sell", horizon="5d", source_report_id="rep-s1")
    buy_id = await _seed(db, action="buy", horizon="5d", source_report_id="rep-b1")
    service = _service(db, _bars([100.0, 90.0, 90.0, 90.0, 90.0]))
    await service.run()
    _ = sell_id, buy_id
    stats = await service.stats()
    assert stats["total"] == 2
    assert stats["hit"] == 1  # sell 命中(跌)
    assert stats["miss"] == 1  # buy 未命中(跌)
    assert stats["hit_rate_pct"] == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_stats_exclude_archived_signals(db):
    kept = await _seed(db, action="sell", horizon="5d", source_report_id="rep-keep")
    gone = await _seed(db, action="sell", horizon="5d", source_report_id="rep-gone")
    service = _service(db, _bars([100.0, 90.0, 90.0, 90.0, 90.0]))
    await service.run()
    await DecisionSignalRepository(db).set_status(gone, "archived")
    stats = await service.stats()
    assert stats["total"] == 1
    _ = kept


# -- 纯辅助函数 ------------------------------------------------------------------


@pytest.mark.parametrize(
    "horizon,expected",
    [
        ("5d", ["5d"]),
        ("5D", ["5d"]),
        (None, ["1d", "3d", "5d", "10d"]),
        ("", ["1d", "3d", "5d", "10d"]),
        ("swing", []),
    ],
)
def test_horizons_for_signal(horizon, expected):
    assert _horizons_for_signal({"horizon": horizon}) == expected


def test_needs_recompute_only_for_retryable_reasons():
    assert _needs_recompute(
        {"eval_status": "unable", "unable_reason": "insufficient_forward_bars"}
    )
    assert not _needs_recompute(
        {"eval_status": "unable", "unable_reason": "non_directional_action"}
    )
    assert not _needs_recompute({"eval_status": "completed", "outcome": "hit"})


def test_anchor_date_prefers_session_date_over_created_at():
    signal = {
        "created_at": "2025-09-10T00:00:00+00:00",
        "metadata_json": '{"market_phase_summary": {"session_date": "2025-08-30"}}',
    }
    assert _anchor_date(signal).isoformat() == "2025-08-30"


def test_anchor_date_falls_back_to_created_at():
    assert _anchor_date({"created_at": "2025-09-10T00:00:00+00:00"}).isoformat() == (
        "2025-09-10"
    )


def test_anchor_date_is_none_without_usable_input():
    assert _anchor_date({"created_at": None}) is None
    assert _anchor_date({"created_at": "not-a-date"}) is None


def test_parse_date_handles_types():
    from datetime import date

    assert _parse_date(date(2025, 9, 1)) == date(2025, 9, 1)
    assert _parse_date("2025-09-01T12:00:00+00:00") == date(2025, 9, 1)
    assert _parse_date("") is None
    assert _parse_date(None) is None


def test_bars_after_is_strictly_after_and_sorted():
    bars = [
        {"date": "2025-09-03", "close": 3.0},
        {"date": "2025-09-01", "close": 1.0},
        {"date": "2025-09-02", "close": 2.0},
    ]
    out = _bars_after(bars, "2025-09-01")
    assert [b["close"] for b in out] == [2.0, 3.0]


def test_close_on_or_before_prefers_exact_anchor():
    bars = [
        {"date": "2025-08-31", "close": 9.0},
        {"date": "2025-09-01", "close": 10.0},
        {"date": "2025-09-02", "close": 11.0},
    ]
    assert _close_on_or_before(bars, "2025-09-01") == pytest.approx(10.0)


def test_close_on_or_before_falls_back_to_prior_bar():
    bars = [{"date": "2025-08-31", "close": 9.0}, {"date": "2025-09-02", "close": 11.0}]
    assert _close_on_or_before(bars, "2025-09-01") == pytest.approx(9.0)


def test_close_on_or_before_is_none_without_any_prior_bar():
    bars = [{"date": "2025-09-02", "close": 11.0}]
    assert _close_on_or_before(bars, "2025-09-01") is None


def test_aggregate_ignores_unable_and_neutral_in_hit_rate():
    stats = _aggregate(
        [
            {"outcome": "hit", "stock_return_pct": 5.0},
            {"outcome": "miss", "stock_return_pct": -3.0},
            {"outcome": "neutral", "stock_return_pct": 0.5},
        ]
    )
    assert stats["hit_rate_pct"] == pytest.approx(50.0)  # 1/(1+1),neutral 不入分母
    assert stats["neutral"] == 1


def test_aggregate_without_directional_verdicts_has_no_hit_rate():
    stats = _aggregate([{"outcome": "neutral", "stock_return_pct": 0.0}])
    assert stats["hit_rate_pct"] is None

"""反事实结算:无策略 mission 的第二条判定腿。

用合成记录 + 临时 klines.db 跑真链路,钉住四件事:

1. **未结算不写账** —— K 线不足 horizon 时不落任何证据(幂等键不能被
   "还没走完"用掉,否则走完那天永远补不上);
2. **方向词不认识就不猜** —— ``unscorable`` 不写证据;
3. **动量同向只算 mild** —— 顺势猜对不是独立证据;
4. **幂等** —— 同一 (记录, horizon) 重跑不重复入账。

外加 ``anchor_ms_of`` / ``load_forward_bars`` 的边界:锚点那根不能被算进
"之后"的 K 线。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio

from app.core.counterfactual_settlement import (
    anchor_ms_of,
    horizon_for,
    load_forward_bars,
    momentum_direction,
    render_fact,
    resolve_mission_record,
    settle_mission,
    signal_for,
)

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.mission_thesis import MissionThesisRepository
from selene_space_storage.repositories.research_missions import (
    ResearchMissionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

BASE_MS = 1_787_000_000_000  # 任意锚点;固定值使合成 bar 可复现
HOUR_MS = 3_600_000


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


@pytest.fixture
def klines_db(tmp_path: Path) -> Path:
    """一条只含 BTC/USDT 1h 的 klines.db,锚点之后有 30 根 bar。"""
    path = tmp_path / "klines.db"
    con = sqlite3.connect(str(path))
    con.execute(
        """
        CREATE TABLE klines (
            timestamp INTEGER, open REAL, high REAL, low REAL, close REAL,
            volume REAL, symbol TEXT, interval TEXT, exchange TEXT, market_type TEXT
        )
        """
    )
    # 锚点那根(close=100)之后每根 +1,所以第 24 根 = 124 → 做多判对。
    rows = []
    for i in range(-5, 31):
        close = 100.0 if i == 0 else 100.0 + i
        rows.append(
            (
                BASE_MS + i * HOUR_MS,
                close,
                close,
                close,
                close,
                1.0,
                "BTC/USDT",
                "1h",
                "binance",
                "futures",
            )
        )
    con.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return path


def _record(
    *,
    direction: str | None = "long",
    last_close: float = 100.0,
    bars_up: bool = True,
    window_end: int = BASE_MS,
) -> dict:
    """一条带 next_period 的最小决策记录(形状取自真实库)。"""
    closes = [100.0 + (i * 0.5 if bars_up else -i * 0.5) for i in range(120)]
    return {
        "meta": {"input_id": "input-abc", "timestamp": "2026-08-01T00:00:00+00:00"},
        "input_data": {
            "symbol": "BTC/USDT",
            "interval": "1h",
            "summary": {
                "symbol": "BTC/USDT",
                "interval": "1h",
                "window_end_iso": window_end,
                "last_close": last_close,
                "bars": [{"k": i + 1, "c": c} for i, c in enumerate(closes)],
            },
        },
        "stage1": {
            "diagnosis_or_decision": {"next_period": {"direction": direction}}
            if direction is not None
            else {}
        },
    }


async def _mission_with_record(db, record: dict) -> dict:
    repo = ResearchMissionRepository(db)
    mission_id = await repo.create(
        "BTC PA 心跳诊断",
        "BTC/USDT PA 心跳诊断待决策",
        "market",
        "BTC/USDT",
        created_by="selene",
        context={"record": record, "interval": "1h", "market_type": "futures"},
    )
    mission = await repo.get(mission_id)
    return dict(mission) if mission else {}


# ---------------------------------------------------------------- 纯函数


def test_anchor_reads_epoch_ms_from_window_end_iso() -> None:
    """``window_end_iso`` 名字骗人,存的是 epoch ms —— 断言走 ms 分支。"""
    assert anchor_ms_of(_record(window_end=BASE_MS)) == BASE_MS


def test_anchor_falls_back_to_last_kline_timestamp() -> None:
    record = _record()
    record["input_data"].pop("summary")
    record["input_data"]["klines"] = [{"timestamp": BASE_MS, "close": 100.0}]
    assert anchor_ms_of(record) == BASE_MS


def test_forward_bars_skip_the_anchor_bar(klines_db: Path) -> None:
    """锚点那根 close=100 不能算进"之后";第一根已是 101。"""
    bars, reason = load_forward_bars(
        "BTC/USDT", "1h", BASE_MS, horizon=24, db_path=klines_db
    )
    assert reason == ""
    assert bars is not None
    assert bars[0]["close"] == 101.0
    assert bars[23]["close"] == 124.0


def test_forward_bars_short_window_reports_shortfall(klines_db: Path) -> None:
    bars, reason = load_forward_bars(
        "BTC/USDT", "1h", BASE_MS, horizon=999, db_path=klines_db
    )
    assert bars is None
    assert "不足 horizon=999" in reason


def test_forward_bars_unreadable_is_not_reported_as_shortfall(tmp_path: Path) -> None:
    """库读不了 ≠ K 线不够 —— 两种原因必须能分辨(吞异常指错宾语的变体)。"""
    missing = tmp_path / "nope.db"
    bars, reason = load_forward_bars(
        "BTC/USDT", "1h", BASE_MS, horizon=24, db_path=missing
    )
    assert bars is None
    assert "读取失败" in reason
    assert "不足 horizon" not in reason


def test_momentum_direction_reads_summary_bars() -> None:
    assert momentum_direction(_record(bars_up=True)) == "up"
    assert momentum_direction(_record(bars_up=False)) == "down"


def test_signal_for_untested_when_unsettled() -> None:
    assert signal_for({"settled": False}, "up") == "untested"


def test_signal_for_mild_when_momentum_agrees() -> None:
    verdict = {"settled": True, "direction": "long", "hit": True}
    assert signal_for(verdict, "up") == "mildly_confirming"
    assert signal_for(verdict, "down") == "confirming"


def test_signal_for_weakening_on_miss() -> None:
    verdict = {"settled": True, "direction": "long", "hit": False}
    assert signal_for(verdict, "up") == "weakening"


def test_signal_for_neutral_is_not_a_hit() -> None:
    verdict = {"settled": True, "direction": "neutral", "hit": None}
    assert signal_for(verdict, "up") == "neutral"


def test_signal_for_inside_band_is_neutral_not_a_hit() -> None:
    """0.03% 的跌幅按符号算"做空命中",但落在中性带内 —— 横盘不是判对。

    实测 ``mis-49a4c5fc8d91``(BTC short 24 根后 -0.03%)就是这一点。
    """
    verdict = {
        "settled": True,
        "direction": "short",
        "hit": True,
        "pnl_pct": -0.0003,
    }
    assert signal_for(verdict, "down") == "neutral"


def test_signal_for_outside_band_counts_normally() -> None:
    # 4.58% —— 与实测 ``mis-7b515a6c53cd``(ETH long,+4.58%)同量级,越出 2% 带。
    verdict = {
        "settled": True,
        "direction": "long",
        "hit": True,
        "pnl_pct": 0.0458,
    }
    assert signal_for(verdict, "up") == "mildly_confirming"
    assert signal_for(verdict, "down") == "confirming"


def test_band_boundary_is_inclusive() -> None:
    """恰好 2% 视为走出方向(与 ``classify_outcome`` 的 ``>=`` 一致)。"""
    verdict = {
        "settled": True,
        "direction": "short",
        "hit": True,
        "pnl_pct": -0.02,
    }
    assert signal_for(verdict, "down") == "mildly_confirming"
    verdict_just_inside = {**verdict, "pnl_pct": -0.0199}
    assert signal_for(verdict_just_inside, "down") == "neutral"


def test_render_fact_carries_numbers_and_baseline() -> None:
    verdict = {
        "direction": "long",
        "decision_close": 100.0,
        "settled_close": 124.0,
        "horizon": 24,
        "pnl_pct": 0.24,
        "hit": True,
        "settled": True,
    }
    fact = render_fact(
        symbol="BTC/USDT",
        interval="1h",
        verdict=verdict,
        baseline="up",
        source="next_period",
    )
    assert "BTC/USDT 1h" in fact
    assert "+24.00%" in fact
    assert "动量基线 up" in fact


# ---------------------------------------------------------------- 真链路


@pytest.mark.asyncio
async def test_settle_writes_evidence_for_a_settled_forecast(
    db, klines_db: Path
) -> None:
    mission = await _mission_with_record(db, _record(direction="long"))
    outcome = await settle_mission(
        db, mission, symbol="BTC/USDT", interval="1h", db_path=klines_db
    )
    assert outcome.ran is True
    # 做多 + 上涨 24% → 判对;基线也是 up → mildly_confirming
    assert outcome.signal == "mildly_confirming"
    assert outcome.evidence_id

    repo = MissionThesisRepository(db)
    pillars = await repo.list_pillars(mission["id"])
    assert len(pillars) == 1
    assert pillars[0]["provenance"] == "draft"


@pytest.mark.asyncio
async def test_settle_is_idempotent(db, klines_db: Path) -> None:
    mission = await _mission_with_record(db, _record(direction="long"))
    first = await settle_mission(
        db, mission, symbol="BTC/USDT", interval="1h", db_path=klines_db
    )
    second = await settle_mission(
        db, mission, symbol="BTC/USDT", interval="1h", db_path=klines_db
    )
    assert first.ran is True
    assert second.ran is False
    assert "幂等" in second.reason
    rows = await db.execute(
        "SELECT COUNT(*) AS n FROM mission_evidence WHERE mission_id = ?",
        (mission["id"],),
    )
    assert int(rows[0]["n"]) == 1


@pytest.mark.asyncio
async def test_settle_writes_nothing_when_horizon_not_reached(
    db, klines_db: Path
) -> None:
    """锚点挪到最新一根附近 → 前向不足 → 不写证据,幂等键不消耗。"""
    late = BASE_MS + 20 * HOUR_MS
    mission = await _mission_with_record(db, _record(direction="long", window_end=late))
    outcome = await settle_mission(
        db, mission, symbol="BTC/USDT", interval="1h", db_path=klines_db
    )
    assert outcome.ran is False
    assert "不足 horizon" in outcome.reason
    rows = await db.execute(
        "SELECT COUNT(*) AS n FROM mission_evidence WHERE mission_id = ?",
        (mission["id"],),
    )
    assert int(rows[0]["n"]) == 0


@pytest.mark.asyncio
async def test_settle_writes_nothing_for_unknown_direction_word(
    db, klines_db: Path
) -> None:
    mission = await _mission_with_record(db, _record(direction="moonwards"))
    outcome = await settle_mission(
        db, mission, symbol="BTC/USDT", interval="1h", db_path=klines_db
    )
    assert outcome.ran is False
    assert "词表" in outcome.reason
    rows = await db.execute(
        "SELECT COUNT(*) AS n FROM mission_evidence WHERE mission_id = ?",
        (mission["id"],),
    )
    assert int(rows[0]["n"]) == 0


@pytest.mark.asyncio
async def test_settle_reports_missing_record_honestly(db, klines_db: Path) -> None:
    repo = ResearchMissionRepository(db)
    mission_id = await repo.create(
        "空 mission", "没有记录", "market", "BTC/USDT", created_by="selene"
    )
    mission = await repo.get(mission_id)
    outcome = await settle_mission(
        db, mission, symbol="BTC/USDT", interval="1h", db_path=klines_db
    )
    assert outcome.ran is False
    assert "没有带方向承诺的记录" in outcome.reason


@pytest.mark.asyncio
async def test_settle_finds_record_via_diagnosis_pointer(db, klines_db: Path) -> None:
    """PA 心跳写的是指针(record_id = analysis_records.input_id),不是内联记录。"""
    from selene_space_storage.repositories.analysis_records import (
        AnalysisRecordRepository,
    )

    record = _record(direction="short")
    await AnalysisRecordRepository(db).save(record)

    repo = ResearchMissionRepository(db)
    mission_id = await repo.create(
        "BTC 心跳",
        "BTC/USDT PA 心跳诊断待决策",
        "market",
        "BTC/USDT",
        created_by="selene",
        context={
            "interval": "1h",
            "latest_diagnosis": {"record_id": "input-abc"},
        },
    )
    mission = await repo.get(mission_id)
    found, analysis_id = await resolve_mission_record(db, mission)
    assert found is not None
    assert analysis_id == "input-abc"

    outcome = await settle_mission(
        db, mission, symbol="BTC/USDT", interval="1h", db_path=klines_db
    )
    # short 但市场涨了 24% → 判错 → weakening
    assert outcome.ran is True
    assert outcome.signal == "weakening"


def test_horizon_defaults_by_interval() -> None:
    assert horizon_for("1h") == 24
    assert horizon_for("4h") == 6
    assert horizon_for("1d") == 5
    assert horizon_for("2h") == 24  # 未知周期走默认,不抛


# ---------------------------------------------------------------- 问题侧扫描


@pytest.mark.asyncio
async def test_settle_decisions_skips_windows_not_yet_due(db, klines_db: Path) -> None:
    """窗口没到 = 还没到能结算的时候,不能写任何观测。"""
    from selene_space_storage.repositories.research_graph import (
        DecisionRepository,
        ResearchQuestionRepository,
    )

    qid = await ResearchQuestionRepository(db).create(
        title="BTC 方向能不能持续", body="", anchor={"symbols": ["BTC/USDT"]}
    )
    future = "2099-01-01T00:00:00+00:00"
    await DecisionRepository(db).record(
        question_id=qid,
        verdict="hold",
        rationale="先看着",
        window_start="2026-01-01T00:00:00+00:00",
        window_end=future,
    )
    from app.core.counterfactual_settlement import settle_decisions

    counters = await settle_decisions(db, db_path=klines_db)
    assert counters["scanned"] == 1
    assert counters["not_due"] == 1
    assert counters["settled"] == 0
    rows = await db.execute("SELECT COUNT(*) AS n FROM research_evidence")
    assert int(rows[0]["n"]) == 0


@pytest.mark.asyncio
async def test_settle_decisions_settles_an_overdue_decision(
    db, klines_db: Path
) -> None:
    from selene_space_storage.repositories.analysis_records import (
        AnalysisRecordRepository,
    )
    from selene_space_storage.repositories.research_graph import (
        DecisionRepository,
        ResearchQuestionRepository,
    )

    await AnalysisRecordRepository(db).save(_record(direction="long"))
    qid = await ResearchQuestionRepository(db).create(
        title="BTC 方向", body="", anchor={"symbols": ["BTC/USDT"]}
    )
    decision_id = await DecisionRepository(db).record(
        question_id=qid,
        verdict="long",
        rationale="做多",
        window_start="2020-01-01T00:00:00+00:00",
        window_end="2020-02-01T00:00:00+00:00",
    )
    from app.core.counterfactual_settlement import settle_decisions

    counters = await settle_decisions(db, db_path=klines_db)
    assert counters["scanned"] == 1
    assert counters["settled"] == 1

    rows = await db.execute(
        "SELECT source_ref FROM research_evidence WHERE source_ref LIKE 'counterfactual:%'"
    )
    assert len(rows) == 1
    # 重复扫描不再写第二条(幂等)
    again = await settle_decisions(db, db_path=klines_db)
    assert again["settled"] == 0
    rows = await db.execute(
        "SELECT COUNT(*) AS n FROM research_evidence WHERE source_ref LIKE 'counterfactual:%'"
    )
    assert int(rows[0]["n"]) == 1

    # 扫描本身**不**翻转 outcome_status —— 那是 observe_outcome 的职责
    decisions = await DecisionRepository(db).list_decisions(question_id=qid)
    target = [d for d in decisions if d["id"] == decision_id][0]
    assert target["outcome_status"] == "outcome_pending"

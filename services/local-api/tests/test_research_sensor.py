"""① 传感器:只在**已有**问题上落观测,绝不新开 mission。

钉住四件事:

1. **不开新东西** —— 跑完 ``research_missions`` 行数不变(计划给的验收判据);
2. **阈值随序列自己走** —— 同一套代码在低波动序列上不触发(地板生效),
   在高波动序列上才触发;固定百分比做不到这一点;
3. **幂等** —— 同一窗口重放不重复入账;
4. **不猜** —— anchor 没写周期的问题不被默认成 1h,也不产生任何证据。
"""

from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from app.core.research_sensor import (
    KIND_MARKET_MOVE,
    MIN_MOVE_PCT,
    MIN_SAMPLES,
    inspect_series,
    market_move_round,
    percentile_threshold,
    source_ref_of,
)

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph import (
    ResearchEdgeRepository,
    ResearchQuestionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
START_MS = 1_700_000_000_000


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


def _klines_db(
    path: Path,
    series: dict[tuple[str, str], tuple[list[float], list[int]]],
) -> Path:
    """``{(symbol, interval): (closes, stamps)}`` → 一个真 sqlite 文件。"""
    con = sqlite3.connect(str(path))
    con.execute(
        """
        CREATE TABLE klines (
            timestamp INTEGER, open REAL, high REAL, low REAL, close REAL,
            volume REAL, symbol TEXT, interval TEXT, exchange TEXT, market_type TEXT
        )
        """
    )
    rows = []
    for (symbol, interval), (closes, stamps) in series.items():
        for close, stamp in zip(closes, stamps, strict=True):
            rows.append(
                (
                    stamp,
                    close,
                    close,
                    close,
                    close,
                    1.0,
                    symbol,
                    interval,
                    "binance",
                    "futures",
                )
            )
    con.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return path


def _volatile(n: int = 400, *, amplitude: float = 4.0, step_ms: int = HOUR_MS):
    """围绕 100 的周期波动 + 最后一根跳升 —— 让 p99 落在可触发的量级。"""
    closes = [100.0 + amplitude * math.sin(i / 2.0) for i in range(n)]
    closes.append(closes[-1] + 20.0)
    stamps = [START_MS + i * step_ms for i in range(len(closes))]
    return closes, stamps


def _quiet(n: int = 400, *, step_ms: int = HOUR_MS):
    """窄幅横盘 —— p99 低于地板,应当不触发。"""
    closes = [100.0 + 0.2 * math.sin(i / 2.0) for i in range(n)]
    stamps = [START_MS + i * step_ms for i in range(len(closes))]
    return closes, stamps


async def _question(db, title: str, anchor: dict, *, status: str = "open") -> str:
    repo = ResearchQuestionRepository(db)
    question_id = await repo.create(title, anchor=anchor, created_by="director")
    if status != "open":
        await repo.close(question_id, status, note="test")
    return str(question_id)


async def _count(db, table: str) -> int:
    rows = await db.execute(f"SELECT COUNT(*) AS n FROM {table}")
    return int(rows[0]["n"])


# ---------------------------------------------------------------- 纯函数


def test_percentile_threshold_uses_nearest_rank() -> None:
    moves = [float(i) for i in range(1, 101)]  # 1..100
    # 99 分位落在接近最大的那一端,而不是平均值
    assert percentile_threshold(moves, 0.99) == pytest.approx(99.0, abs=1.5)
    assert percentile_threshold([], 0.99) is None


def test_source_ref_separates_intervals_on_the_same_bar_time() -> None:
    """1d 收盘与 1h 收盘可以是同一个毫秒 —— 键必须能区分它们。

    不带 interval 时两条不同窗口会撞同一个 key,第二条被当成"已入账"静默
    丢掉。这条测试锁死那个扩展。
    """
    same_end = START_MS
    a = source_ref_of(KIND_MARKET_MOVE, "BTC/USDT", "1h", same_end)
    b = source_ref_of(KIND_MARKET_MOVE, "BTC/USDT", "1d", same_end)
    assert a != b
    assert "1h" in a and "1d" in b


# ---------------------------------------------------------------- 判定


def test_quiet_series_never_reaches_the_floor(tmp_path: Path) -> None:
    closes, stamps = _quiet()
    path = _klines_db(tmp_path / "k.db", {("BTC/USDT", "1h"): (closes, stamps)})

    window, reason = inspect_series(path, "BTC/USDT", "1h")

    assert window is None
    assert "地板" in reason, reason
    assert str(MIN_MOVE_PCT) in reason


def test_volatile_series_fires_on_its_own_top_percentile(tmp_path: Path) -> None:
    closes, stamps = _volatile()
    path = _klines_db(tmp_path / "k.db", {("BTC/USDT", "1h"): (closes, stamps)})

    window, reason = inspect_series(path, "BTC/USDT", "1h")

    assert reason == ""
    assert window is not None
    assert window.direction == "up"
    assert window.move_pct > window.threshold_pct
    assert window.threshold_pct >= MIN_MOVE_PCT
    assert window.samples >= MIN_SAMPLES
    assert window.window_end_ms == stamps[-1]
    assert window.window_start_ms == stamps[-1 - window.horizon]


def test_short_history_reports_shortfall_instead_of_guessing(tmp_path: Path) -> None:
    closes, stamps = _volatile(n=60)
    path = _klines_db(tmp_path / "k.db", {("BTC/USDT", "1h"): (closes, stamps)})

    window, reason = inspect_series(path, "BTC/USDT", "1h")

    assert window is None
    assert "不足" in reason


def test_missing_series_is_unavailable_not_silent(tmp_path: Path) -> None:
    path = _klines_db(tmp_path / "k.db", {("BTC/USDT", "1h"): _volatile()})

    window, reason = inspect_series(path, "ETH/USDT", "1h")

    assert window is None
    # 空序列与"读了但不够"必须是不同的话术,否则排障时无法区分
    assert "只有 0 根" in reason


# ---------------------------------------------------------------- 一轮


@pytest.mark.asyncio
async def test_round_writes_evidence_without_opening_anything(
    db, tmp_path: Path
) -> None:
    closes, stamps = _volatile()
    path = _klines_db(tmp_path / "k.db", {("BTC/USDT", "1h"): (closes, stamps)})
    question_id = await _question(
        db, "BTC 1h 这波能否延续", {"symbols": ["BTC/USDT"], "intervals": ["1h"]}
    )
    missions_before = await _count(db, "research_missions")
    questions_before = await _count(db, "research_questions")

    counts = await market_move_round(db, db_path=path)

    assert counts["written"] == 1, counts
    assert counts["fired"] == 1
    assert counts["questions"] == 1
    # 硬规矩一:绝不新开 mission / question
    assert await _count(db, "research_missions") == missions_before
    assert await _count(db, "research_questions") == questions_before

    rows = await db.execute("SELECT * FROM research_evidence")
    assert len(rows) == 1
    assert rows[0]["mode"] == "RECORDED"
    assert rows[0]["source_ref"].startswith("sensor:market_move:BTC/USDT:1h:")

    edges = await ResearchEdgeRepository(db).list_edges(question_id, kind="addresses")
    assert len(edges) == 1
    assert edges[0]["from_type"] == "evidence"


@pytest.mark.asyncio
async def test_second_round_is_a_no_op(db, tmp_path: Path) -> None:
    closes, stamps = _volatile()
    path = _klines_db(tmp_path / "k.db", {("BTC/USDT", "1h"): (closes, stamps)})
    await _question(
        db, "BTC 1h 这波能否延续", {"symbols": ["BTC/USDT"], "intervals": ["1h"]}
    )

    first = await market_move_round(db, db_path=path)
    second = await market_move_round(db, db_path=path)

    assert first["written"] == 1
    assert second["written"] == 0
    assert second["duplicate"] == 1
    assert await _count(db, "research_evidence") == 1


@pytest.mark.asyncio
async def test_one_observation_serves_every_question_on_that_series(
    db, tmp_path: Path
) -> None:
    """同一标的上的两个问题看到的是同一件事实 —— 一行证据,两条边。"""
    closes, stamps = _volatile()
    path = _klines_db(tmp_path / "k.db", {("BTC/USDT", "1h"): (closes, stamps)})
    q1 = await _question(db, "问法一", {"symbols": ["BTC/USDT"], "intervals": ["1h"]})
    q2 = await _question(db, "问法二", {"symbols": ["BTC/USDT"], "intervals": ["1h"]})

    counts = await market_move_round(db, db_path=path)

    assert counts["written"] == 1
    assert await _count(db, "research_evidence") == 1
    assert await _count(db, "research_episodes") == 1
    edge_repo = ResearchEdgeRepository(db)
    assert len(await edge_repo.list_edges(q1, kind="addresses")) == 1
    assert len(await edge_repo.list_edges(q2, kind="addresses")) == 1


@pytest.mark.asyncio
async def test_parked_questions_are_not_fed(db, tmp_path: Path) -> None:
    """停放是"先放下" —— 继续往里堆证据等于不尊重那个决定。"""
    closes, stamps = _volatile()
    path = _klines_db(tmp_path / "k.db", {("BTC/USDT", "1h"): (closes, stamps)})
    await _question(
        db,
        "已停放",
        {"symbols": ["BTC/USDT"], "intervals": ["1h"]},
        status="parked",
    )

    counts = await market_move_round(db, db_path=path)

    assert counts["questions"] == 0
    assert counts["written"] == 0
    assert await _count(db, "research_evidence") == 0


@pytest.mark.asyncio
async def test_anchor_without_interval_is_skipped_not_defaulted(
    db, tmp_path: Path
) -> None:
    """``counterfactual_settlement`` 默认 1h 是因为读记录自带的字段;anchor 的
    周期是人的意图,猜一个就把"看 1d 的研究"悄悄换成"看 1h 的研究"。"""
    closes, stamps = _volatile()
    path = _klines_db(tmp_path / "k.db", {("BTC/USDT", "1h"): (closes, stamps)})
    await _question(db, "只有标的", {"symbols": ["BTC/USDT"]})

    counts = await market_move_round(db, db_path=path)

    assert counts["no_interval"] == 1
    assert counts["series"] == 0
    assert await _count(db, "research_evidence") == 0


@pytest.mark.asyncio
async def test_anchor_without_symbol_is_counted_separately(db, tmp_path: Path) -> None:
    path = _klines_db(tmp_path / "k.db", {("BTC/USDT", "1h"): _volatile()})
    await _question(db, "无 anchor", {})

    counts = await market_move_round(db, db_path=path)

    assert counts["no_symbol"] == 1
    assert counts["series"] == 0


@pytest.mark.asyncio
async def test_same_bar_time_on_two_intervals_writes_two_rows(
    db, tmp_path: Path
) -> None:
    """1d 与 1h 的最后一根都落在 UTC 00:00 —— 幂等键必须能把它们分开。"""
    h_closes, h_stamps = _volatile(step_ms=HOUR_MS)
    # 1d 序列:与 1h 同一终点时间
    d_closes = [100.0 + 4.0 * math.sin(i / 2.0) for i in range(400)]
    end_ms = h_stamps[-1]
    d_stamps = [end_ms - (len(d_closes) - 1 - i) * DAY_MS for i in range(len(d_closes))]
    d_closes[-1] = d_closes[-2] + 20.0
    path = _klines_db(
        tmp_path / "k.db",
        {
            ("BTC/USDT", "1h"): (h_closes, h_stamps),
            ("BTC/USDT", "1d"): (d_closes, d_stamps),
        },
    )
    assert d_stamps[-1] == h_stamps[-1], "测试前提:两条序列终点必须相同"
    await _question(db, "双周期", {"symbols": ["BTC/USDT"], "intervals": ["1h", "1d"]})

    counts = await market_move_round(db, db_path=path)

    assert counts["series"] == 2
    assert counts["written"] == 2, counts
    rows = await db.execute(
        "SELECT source_ref FROM research_evidence ORDER BY source_ref"
    )
    refs = [str(r["source_ref"]) for r in rows]
    assert any(":1h:" in r for r in refs)
    assert any(":1d:" in r for r in refs)


@pytest.mark.asyncio
async def test_observation_boundary_matches_the_scored_window(
    db, tmp_path: Path
) -> None:
    """episode 的边界必须是真的被比较的那两根 bar,不是"今天"。"""
    closes, stamps = _volatile()
    path = _klines_db(tmp_path / "k.db", {("BTC/USDT", "1h"): (closes, stamps)})
    await _question(db, "边界", {"symbols": ["BTC/USDT"], "intervals": ["1h"]})

    await market_move_round(db, db_path=path)

    rows = await db.execute("SELECT * FROM research_episodes")
    episode = dict(rows[0])
    expected_start = (
        datetime.fromtimestamp(stamps[-1 - 24] / 1000, UTC).date().isoformat()
    )
    expected_end = datetime.fromtimestamp(stamps[-1] / 1000, UTC).date().isoformat()
    assert episode["boundary_start"] == expected_start
    assert episode["boundary_end"] == expected_end
    assert episode["subject_type"] == "market"
    assert episode["subject_id"] == "BTC/USDT"

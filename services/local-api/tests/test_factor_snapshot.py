"""factor_snapshot 服务层 —— 快照契约测试。

用 AsyncSQLiteManager(":memory:", migration_runner=ALL_MIGRATIONS) 造库
(run_probe.py --self-check 与 test_api_research_graph.py 同法),不碰真实
selenespace.db。

覆盖契约五条:
1. NaN↔null 往返无损(merge 前原始面板,含末根 NaN 行)
2. snapshot_key 参数规范化(symbols 乱序同 key)
3. 同秒双 save + load 取新(rowid 二级键竞态单测)
4. 可选 question_id 分支(addresses 边)+ anchor fail-fast(KeyError/ValueError)
5. 5MB 闸 fail-fast
"""

from __future__ import annotations

import pandas as pd
import pytest
import pytest_asyncio

from app.services import factor_snapshot as fs
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph import (
    EpisodeRepository,
    EvidenceRepository,
    ResearchQuestionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

pytestmark = pytest.mark.asyncio

PARAMS = dict(
    exchange="binance",
    market_type="futures",
    symbols=["BTC/USDT", "ETH/USDT"],
    interval="1h",
    factor="momentum",
    period=5,
    limit=500,
)


def _panels(n_dates: int = 4):
    """两个 date×symbol 网格严格一致的面板;末根 fut_ret_1=NaN(原始态)。"""
    dates = [
        (pd.Timestamp("2026-01-01") + pd.Timedelta(days=i)).isoformat()
        for i in range(n_dates)
    ]
    symbols = ["BTC/USDT", "ETH/USDT"]
    f_rows, r_rows = [], []
    for d in dates:
        for s in symbols:
            f_rows.append({"trade_date": d, "symbol": s, "factor": 0.01})
            r_rows.append({"trade_date": d, "symbol": s, "fut_ret_1": 0.02})
    # 末根 NaN(模拟 close.shift(-1) 的尾部空洞)
    r_rows[-1]["fut_ret_1"] = float("nan")
    return pd.DataFrame(f_rows), pd.DataFrame(r_rows)


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


async def test_serialize_deserialize_roundtrip_preserves_nan(db):
    """NaN→null→NaN 往返;rows 是 merge 前原始面板(末根 NaN 保留)。"""
    factor_df, ret_df = _panels()
    rows = fs.serialize_panel(factor_df, ret_df)
    assert len(rows) == 8
    # 末根(btc 之后 eth 最后写入的一行)fut_ret_1 是 None 而非 0.0
    assert rows[-1][3] is None
    assert rows[0][3] == pytest.approx(0.02)
    f2, r2 = fs.deserialize_panel(rows)
    assert len(f2) == len(factor_df)
    # null→NaN 往返
    assert r2["fut_ret_1"].isna().sum() == 1
    assert f2["factor"].notna().all()
    # 列名与 ICPanel 默认列零适配
    assert list(f2.columns) == ["trade_date", "symbol", "factor"]
    assert list(r2.columns) == ["trade_date", "symbol", "fut_ret_1"]


async def test_serialize_rejects_divergent_grids():
    """factor/ret 网格不一致 → 拒(静默丢行比重算错位更糟)。"""
    f = pd.DataFrame([{"trade_date": "2026-01-01", "symbol": "A", "factor": 1.0}])
    r = pd.DataFrame(
        [
            {"trade_date": "2026-01-01", "symbol": "A", "fut_ret_1": 0.1},
            {"trade_date": "2026-01-02", "symbol": "A", "fut_ret_1": 0.2},
        ]
    )
    with pytest.raises(ValueError, match="grids diverge"):
        fs.serialize_panel(f, r)


async def test_snapshot_key_normalizes_symbols():
    """symbols 乱序/重复/带空格 → 同 key;horizon 不进 key(根本没有该参数)。"""
    a = fs.snapshot_key(**PARAMS)
    b = fs.snapshot_key(**{**PARAMS, "symbols": [" ETH/USDT ", "BTC/USDT", "ETH/USDT"]})
    assert a == b
    assert a.startswith("ic-panel:binance:futures:")
    assert a.endswith(":1h:momentum:5:500")


async def test_save_and_load_latest_roundtrip(db):
    factor_df, ret_df = _panels()
    saved = await fs.save_snapshot(
        manager=db, **PARAMS, factor_df=factor_df, ret_df=ret_df
    )
    assert saved["n_rows"] == 8
    loaded = await fs.load_latest_snapshot(db, key=saved["key"])
    assert loaded is not None
    assert loaded["episode_id"] == saved["episode_id"]
    assert loaded["payload"]["snapshot_schema"] == 2
    assert loaded["payload"]["params"]["symbols"] == ["BTC/USDT", "ETH/USDT"]
    assert len(loaded["payload"]["rows"]) == 8
    # schema v2:target 契约随数据走(评估定义血缘)
    assert loaded["payload"]["target"] == {
        "entry_price": "current_close",
        "exit_price": "close",
        "horizon_bars": 1,
        "return_type": "simple",
        "definition": "close[t+1]/close[t] - 1",
    }
    assert fs.resolve_target(loaded["payload"]) == fs.DEFAULT_TARGET
    # boundary 来自数据,不来自钟
    assert loaded["payload"]["boundary"]["start"] == "2026-01-01T00:00:00"
    # occurred_at 是挖掘时刻(save 显式传 now),不是数据末根时间
    assert loaded["occurred_at"] == saved["occurred_at"]


async def test_same_second_double_save_load_returns_newest(db, monkeypatch):
    """同秒双 mine:occurred_at 全等时 rowid(插入序)决定新旧 —— 取后存的。"""
    fixed_now = "2026-06-01T00:00:00+00:00"
    monkeypatch.setattr(fs, "_now", lambda: fixed_now)
    factor_df, ret_df = _panels()
    first = await fs.save_snapshot(
        manager=db, **PARAMS, factor_df=factor_df, ret_df=ret_df
    )
    # 第二次改一个值,便于区分新旧快照
    ret_df2 = ret_df.copy()
    ret_df2.loc[ret_df2.index[-1], "fut_ret_1"] = 0.03
    second = await fs.save_snapshot(
        manager=db, **PARAMS, factor_df=factor_df, ret_df=ret_df2
    )
    assert first["episode_id"] != second["episode_id"]
    assert first["occurred_at"] == second["occurred_at"]  # 同秒成立
    loaded = await fs.load_latest_snapshot(db, key=first["key"])
    assert loaded is not None
    assert loaded["episode_id"] == second["episode_id"]
    # 新快照的末根是 0.03(而非 NaN)—— 证明取到的是第二次的 rows
    assert loaded["payload"]["rows"][-1][3] == pytest.approx(0.03)


async def test_save_with_question_id_addresses_edge(db):
    factor_df, ret_df = _panels()
    qid = await ResearchQuestionRepository(db).create(
        "SOL 4h 亏损根因",
        body="",
        anchor={"strategy_ids": ["snap-test"]},
        created_by="user",
    )
    saved = await fs.save_snapshot(
        manager=db, **PARAMS, factor_df=factor_df, ret_df=ret_df, question_id=qid
    )
    edges = await db.execute(
        "SELECT from_type, from_id, to_type, to_id, kind FROM research_edges "
        "WHERE kind='addresses' AND from_id=?",
        (saved["evidence_id"],),
    )
    assert edges and edges[0]["to_id"] == qid


async def test_save_with_missing_question_raises_key_error(db):
    factor_df, ret_df = _panels()
    with pytest.raises(KeyError):
        await fs.save_snapshot(
            manager=db,
            **PARAMS,
            factor_df=factor_df,
            ret_df=ret_df,
            question_id="q-not-exist",
        )


async def test_save_with_anchorless_question_raises_value_error(db):
    factor_df, ret_df = _panels()
    qid = await ResearchQuestionRepository(db).create(
        "无锚点问题", body="", anchor={}, created_by="user"
    )
    with pytest.raises(ValueError, match="anchor has no usable keys"):
        await fs.save_snapshot(
            manager=db,
            **PARAMS,
            factor_df=factor_df,
            ret_df=ret_df,
            question_id=qid,
        )


async def test_payload_gate_rejects_oversized(db, monkeypatch):
    """5MB 闸:超闸 fail-fast(参数失真该减 limit,不许静默压缩)。"""
    factor_df, ret_df = _panels(n_dates=50)
    monkeypatch.setattr(fs, "MAX_PAYLOAD_BYTES", 10)  # 闸收紧到触发
    with pytest.raises(ValueError, match="payload exceeds"):
        await fs.save_snapshot(manager=db, **PARAMS, factor_df=factor_df, ret_df=ret_df)


async def test_v1_snapshot_reader_resolves_default_target():
    """向后兼容:schema v1 payload(无 target 键)→ reader 显式 default(simple)。"""
    v1_payload = {
        "snapshot_schema": 1,
        "params": {"factor": "momentum"},
        "rows": [["2026-01-01T00:00:00", "BTC/USDT", 0.01, 0.02]],
        "boundary": {"start": "2026-01-01T00:00:00", "end": "2026-01-01T00:00:00"},
    }
    assert fs.resolve_target(v1_payload) == fs.DEFAULT_TARGET
    assert fs.resolve_target(v1_payload).definition() == "close[t+1]/close[t] - 1"
    # schema 键缺失的更老 payload 同样视为 default
    assert fs.resolve_target({"rows": []}) == fs.DEFAULT_TARGET


async def test_save_rejects_non_default_target(db):
    """非 default target → fail-fast(消费方只会计 simple fut_ret_1,v3 再放行)。"""
    factor_df, ret_df = _panels()
    rogue = fs.ForwardReturnTarget(
        entry_price="next_open",
        exit_price="close",
        horizon_bars=1,
        return_type="simple",
    )
    with pytest.raises(NotImplementedError, match="non-default"):
        await fs.save_snapshot(
            manager=db, **PARAMS, factor_df=factor_df, ret_df=ret_df, target=rogue
        )


async def test_snapshot_episode_stays_single_evidence(db):
    """契约:快照 episode 永不加第二条 evidence —— load 只 JOIN 出 RECORDED
    那条,且 compare_modes 场景下消费方可凭 snapshot_schema 标记跳过。"""
    factor_df, ret_df = _panels()
    saved = await fs.save_snapshot(
        manager=db, **PARAMS, factor_df=factor_df, ret_df=ret_df
    )
    loaded = await fs.load_latest_snapshot(db, key=saved["key"])
    assert loaded is not None
    # payload 首键类型标记存在
    assert loaded["payload"]["snapshot_schema"] == 2
    # 该 episode 的 evidence 恰好一条(RECORDED)
    evs = await EvidenceRepository(db).list_for_episode(saved["episode_id"])
    assert len(evs) == 1 and evs[0]["mode"] == "RECORDED"
    # episode 本体 recorded 带类型标记(列表消费方可识别跳过)
    ep = await EpisodeRepository(db).get(saved["episode_id"])
    assert ep is not None and ep["recorded"]["kind"] == "factor_snapshot"


async def test_snapshot_episode_starts_lifecycle_at_discovered(db):
    """落账即初始 DISCOVERED(不做认识论判断);transitions 空;流转经窄门。"""
    factor_df, ret_df = _panels()
    saved = await fs.save_snapshot(
        manager=db, **PARAMS, factor_df=factor_df, ret_df=ret_df
    )
    ep = await EpisodeRepository(db).get(saved["episode_id"])
    assert ep is not None
    lifecycle = ep["recorded"]["lifecycle"]
    assert lifecycle["status"] == "DISCOVERED"
    assert lifecycle["transitions"] == []
    # 流转一次可读出(窄门已开;全路径行为由 storage 单测覆盖)
    await EpisodeRepository(db).record_lifecycle_transition(
        saved["episode_id"], "INSPECTED", actor="user"
    )
    ep2 = await EpisodeRepository(db).get(saved["episode_id"])
    assert ep2 is not None
    assert ep2["recorded"]["lifecycle"]["status"] == "INSPECTED"
    assert ep2["recorded"]["kind"] == "factor_snapshot"  # 内容键未动


async def test_same_key_resave_creates_derived_from_chain(db):
    """同参数集重跑链:第二次 save 自动连 derived_from 边(子→父);首次无边。"""
    factor_df, ret_df = _panels()
    first = await fs.save_snapshot(
        manager=db, **PARAMS, factor_df=factor_df, ret_df=ret_df
    )
    edges1 = await db.execute("SELECT * FROM research_edges WHERE kind='derived_from'")
    assert not edges1  # 首跑无边
    second = await fs.save_snapshot(
        manager=db, **PARAMS, factor_df=factor_df, ret_df=ret_df
    )
    edges2 = await db.execute(
        "SELECT from_type, from_id, to_type, to_id FROM research_edges "
        "WHERE kind='derived_from'"
    )
    assert len(edges2) == 1
    e = edges2[0]
    assert (e["from_id"], e["to_id"]) == (second["episode_id"], first["episode_id"])
    assert e["from_type"] == "episode" and e["to_type"] == "episode"


@pytest.mark.asyncio
async def test_load_lineage_walks_derived_from_chain(db):
    """血缘读口:首跑空链;重跑后能沿 derived_from 走回首跑。"""
    factor_df, ret_df = _panels()
    first = await fs.save_snapshot(
        manager=db, **PARAMS, factor_df=factor_df, ret_df=ret_df
    )
    assert await fs.load_lineage(db, first["episode_id"]) == []

    second = await fs.save_snapshot(
        manager=db, **PARAMS, factor_df=factor_df, ret_df=ret_df
    )
    lineage = await fs.load_lineage(db, second["episode_id"])
    assert len(lineage) == 1
    assert lineage[0]["episode_id"] == first["episode_id"]

    third = await fs.save_snapshot(
        manager=db, **PARAMS, factor_df=factor_df, ret_df=ret_df
    )
    lineage = await fs.load_lineage(db, third["episode_id"])
    assert [a["episode_id"] for a in lineage] == [
        second["episode_id"],
        first["episode_id"],
    ]

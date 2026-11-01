"""召回 + 证据链闸门(§6.10 ⑤ / §7.4 / §12.1 第 14 行)。

两条要同时成立,少一条这个模块就退化:
- **取得到**:相似情境的历史真的被捞出来(否则等于没有召回)
- **闸得住**:证据链走不到 Episode / Evidence 的必须被标成 Research Record,
  不得混进 Learned Experience 被当作既有知识消费

用原生 SQL 造种子,不依赖 Task 6 的对象仓储 —— 这个 task 排在 Task 6 之前,
它的测试必须能独立跑绿(同 Task 4 的 ``_seed_episode_with_evidence`` 手法)。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


T0 = "2026-09-01T00:00:00+00:00"


async def _seed_question(conn, qid: str, *, strategy_ids: list[str]) -> str:
    await conn.execute(
        "INSERT INTO research_questions (id, title, body, anchor_json, status, "
        "opened_at, updated_at) VALUES (?, ?, '', ?, 'open', ?, ?)",
        (
            qid,
            f"问题 {qid}",
            '{"strategy_ids": %s}'
            % ("[" + ", ".join(f'"{s}"' for s in strategy_ids) + "]"),
            T0,
            T0,
        ),
    )
    return qid


async def _seed_broken_chain(db, *, qid: str) -> str:
    """问题 + 一条只靠 addresses 挂在它下面的假设 —— 证据链走不到任何东西。

    **``addresses`` 不算证据链**:它是"进了这个问题的研究世界"的凭证,
    不是"被现实锚住了"。这条区分不写死,任何一个问题下的假设都会因为
    同问题下有别的证据而被误判成 Learned Experience。
    """
    async with db.connection() as conn:
        await _seed_question(conn, qid, strategy_ids=["strat-a"])
        await conn.execute(
            "INSERT INTO research_hypotheses (id, statement, status, created_at, "
            "updated_at) VALUES ('hyp-1', '亏损主要由配置漂移导致', 'proposed', ?, ?)",
            (T0, T0),
        )
        await conn.execute(
            "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
            "kind, edge_class, actor, created_at) "
            "VALUES ('e-h1', 'hypothesis', 'hyp-1', 'question', ?, 'addresses', "
            "'fact', 'system', ?)",
            (qid, T0),
        )
    return qid


async def _seed_anchored_chain(db, *, qid: str) -> str:
    """问题 + Episode + Evidence + 一条引用了该 Evidence 的 revision。

    这条链走得到 Evidence,所以 revision 应该是 Learned Experience。
    """
    async with db.connection() as conn:
        await _seed_question(conn, qid, strategy_ids=["strat-a"])
        await conn.execute(
            "INSERT INTO research_episodes (id, subject_type, subject_id, title, "
            "boundary_start, boundary_end, occurred_at, created_at) "
            "VALUES ('ep-1', 'strategy', 'strat-a', '实跑段', ?, ?, ?, ?)",
            (T0, T0, T0, T0),
        )
        await conn.execute(
            "INSERT INTO research_evidence (id, episode_id, mode, observed_at, "
            "created_at) VALUES ('ev-1', 'ep-1', 'RECORDED', ?, ?)",
            (T0, T0),
        )
        await conn.execute(
            "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
            "kind, edge_class, actor, created_at) "
            "VALUES ('e-ev1', 'evidence', 'ev-1', 'question', ?, 'addresses', "
            "'fact', 'system', ?)",
            (qid, T0),
        )
        await conn.execute(
            "INSERT INTO research_revisions (id, question_id, seq, content, "
            "rationale_json, cited_json, actor, created_at) "
            "VALUES ('rev-1', ?, 1, '初步判断:配置漂移', '[]', ?, 'selene', ?)",
            (qid, '["ev-1"]', T0),
        )
    return qid


@pytest.mark.asyncio
async def test_recall_returns_anchored_revision_as_learned_experience(db):
    from selene_space_storage.repositories.research_graph.recall import (
        LEARNED_EXPERIENCE,
        recall,
    )

    qid = await _seed_anchored_chain(db, qid="q-a")
    out = await recall(db, question_id=qid, kinds=("revision",))

    assert [c["id"] for c in out] == ["rev-1"]
    assert out[0]["recall_class"] == LEARNED_EXPERIENCE
    assert out[0]["evidence_chain"], "走到 Evidence 的路径必须被记下来"


@pytest.mark.asyncio
async def test_recall_marks_broken_chain_as_research_record(db):
    """走不到 Episode / Evidence 的历史**仍然召回**,但身份不同(§6.10 ⑤)。

    这里刻意不排除它 —— 被证伪的结论和未验证的假设都是合法历史,
    而且"为什么失败"往往最有复利价值。**是分层,不是资格排除。**
    """
    from selene_space_storage.repositories.research_graph.recall import (
        RESEARCH_RECORD,
        recall,
    )

    qid = await _seed_broken_chain(db, qid="q-b")
    out = await recall(db, question_id=qid, kinds=("hypothesis",))

    assert [c["id"] for c in out] == ["hyp-1"]
    assert out[0]["recall_class"] == RESEARCH_RECORD
    assert out[0]["evidence_chain"] == []


@pytest.mark.asyncio
async def test_addresses_alone_never_counts_even_with_sibling_evidence(db):
    """**同一个问题下已经有真证据,也不能把靠 addresses 挂着的假设洗白。**

    这是 ``addresses`` 必须被排除的**真正理由**:它只是"进了这个问题的研究
    世界"的凭证。如果把"问题下有没有证据"当作证据链,那么任何问题只要有一条
    证据,它底下所有假设都会自动升级成 Learned Experience —— 分层当场失效。
    """
    from selene_space_storage.repositories.research_graph.recall import (
        LEARNED_EXPERIENCE,
        RESEARCH_RECORD,
        recall,
    )

    # 这个问题下有完整的锚定链(ep-1 / ev-1 / 引用 ev-1 的 rev-1)
    qid = await _seed_anchored_chain(db, qid="q-g")
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO research_hypotheses (id, statement, status, created_at, "
            "updated_at) VALUES ('hyp-9', '亏损主要由配置漂移导致', 'proposed', ?, ?)",
            (T0, T0),
        )
        await conn.execute(
            "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
            "kind, edge_class, actor, created_at) "
            "VALUES ('e-h9', 'hypothesis', 'hyp-9', 'question', ?, 'addresses', "
            "'fact', 'system', ?)",
            (qid, T0),
        )

    # 前置事实:同一个问题下,**有**东西真的走到了证据 —— 否则本测试会因为
    # "种子里压根没有证据"而假绿,证明不了任何事
    anchored = await recall(db, question_id=qid, kinds=("revision",))
    assert [c["id"] for c in anchored] == ["rev-1"]
    assert anchored[0]["recall_class"] == LEARNED_EXPERIENCE

    out = await recall(db, question_id=qid, kinds=("hypothesis",))
    assert [c["id"] for c in out] == ["hyp-9"], "假设仍然召回 —— 是分层不是排除"
    assert out[0]["recall_class"] == RESEARCH_RECORD
    assert out[0]["evidence_chain"] == [], "同问题下的 ev-1 不是它的证据"


@pytest.mark.asyncio
async def test_recall_require_class_filters_records_out(db):
    """要"既有知识"时,走不到的必须被闸掉 —— §7.4 的机器形态。"""
    from selene_space_storage.repositories.research_graph.recall import (
        LEARNED_EXPERIENCE,
        recall,
    )

    qid = await _seed_broken_chain(db, qid="q-c")
    out = await recall(
        db,
        question_id=qid,
        kinds=("hypothesis",),
        require_class=LEARNED_EXPERIENCE,
    )
    assert out == []


@pytest.mark.asyncio
async def test_recall_explains_why_each_candidate_came_back(db):
    """不解释的召回是黑箱 —— v0 明确禁 embedding / 假精度(§12.1 第 14 行)。"""
    from selene_space_storage.repositories.research_graph.recall import recall

    qid = await _seed_anchored_chain(db, qid="q-d")
    out = await recall(db, question_id=qid, kinds=("revision",))

    assert out[0]["matched_on"], "必须说得出为什么被取回"
    assert out[0]["score"] == len(out[0]["matched_on"])
    assert any("question" in m or "anchor" in m for m in out[0]["matched_on"])


@pytest.mark.asyncio
async def test_recall_filters_out_unrelated_anchor(db):
    """Anchor 不重叠、问题也不同 —— 不许被捞出来。"""
    from selene_space_storage.repositories.research_graph.recall import recall

    await _seed_anchored_chain(db, qid="q-e1")
    async with db.connection() as conn:
        await _seed_question(conn, "q-e2", strategy_ids=["strat-z"])
        await conn.execute(
            "INSERT INTO research_revisions (id, question_id, seq, content, "
            "rationale_json, cited_json, actor, created_at) "
            "VALUES ('rev-z', 'q-e2', 1, '无关结论', '[]', '[]', 'selene', ?)",
            (T0,),
        )

    out = await recall(db, anchor={"strategy_ids": ["strat-a"]}, kinds=("revision",))
    assert [c["id"] for c in out] == ["rev-1"]


@pytest.mark.asyncio
async def test_recall_reaches_across_questions(db):
    """**跨 Question 召回是本函数存在的理由之一**(Hypothesis 本就跨 Question,§6.2)。

    触发键是"当前研究状态与历史情境相似",不是"同一个 Question"。
    """
    from selene_space_storage.repositories.research_graph.recall import recall

    await _seed_anchored_chain(db, qid="q-f1")
    # 另一个问题,锚相同 —— 历史应该被带过来
    async with db.connection() as conn:
        await _seed_question(conn, "q-f2", strategy_ids=["strat-a"])

    out = await recall(db, question_id="q-f2", kinds=("revision",))
    assert [c["id"] for c in out] == ["rev-1"], "同锚的历史问题必须能被带进新问题"
    assert "anchor:strategy_ids" in out[0]["matched_on"]


# ---------------------------------------------------------------------------
# B3:refuted / retired 假设的过时降权 —— 历史有效 ≠ 当前有效
# ---------------------------------------------------------------------------


async def _seed_hyp_with_status(conn, hyp_id: str, status: str, *, qid: str) -> None:
    await conn.execute(
        "INSERT INTO research_hypotheses (id, statement, status, created_at, "
        f"updated_at) VALUES ('{hyp_id}', '假设 {hyp_id}', '{status}', ?, ?)",
        (T0, T0),
    )
    await conn.execute(
        "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
        "kind, edge_class, actor, created_at) "
        f"VALUES ('e-{hyp_id}', 'hypothesis', '{hyp_id}', 'question', '{qid}', "
        "'addresses', 'fact', 'system', ?)",
        (T0,),
    )


@pytest.mark.asyncio
async def test_refuted_hypothesis_marked_superseded_and_downweighted(db):
    """refuted 假设仍召回(失败史有复利价值),但 superseded=True 且被扣分。"""
    from selene_space_storage.repositories.research_graph.recall import recall

    qid = await _seed_broken_chain(db, qid="q-h1")
    async with db.connection() as conn:
        await _seed_hyp_with_status(conn, "hyp-ref", "refuted", qid=qid)

    out = await recall(db, question_id=qid, kinds=("hypothesis",))
    by_id = {c["id"]: c for c in out}
    assert set(by_id) == {"hyp-1", "hyp-ref"}, "是分层不是排除,refuted 仍召回"
    assert by_id["hyp-ref"]["superseded"] is True
    assert by_id["hyp-ref"]["score"] == 0, "matched=1 扣降权 1 分"
    assert by_id["hyp-1"]["superseded"] is False
    assert by_id["hyp-1"]["score"] == 1
    assert out[0]["id"] == "hyp-1", "活假设排在被证伪假设之前"


@pytest.mark.asyncio
async def test_retired_hypothesis_also_superseded(db):
    from selene_space_storage.repositories.research_graph.recall import recall

    qid = await _seed_broken_chain(db, qid="q-h2")
    async with db.connection() as conn:
        await _seed_hyp_with_status(conn, "hyp-ret", "retired", qid=qid)

    out = await recall(db, question_id=qid, kinds=("hypothesis",))
    by_id = {c["id"]: c for c in out}
    assert by_id["hyp-ret"]["superseded"] is True


@pytest.mark.asyncio
async def test_active_statuses_not_superseded(db):
    """proposed/testing/supported 是活假设,superseded 必须为 False。"""
    from selene_space_storage.repositories.research_graph.recall import recall

    qid = await _seed_broken_chain(db, qid="q-h3")
    async with db.connection() as conn:
        await _seed_hyp_with_status(conn, "hyp-t", "testing", qid=qid)
        await _seed_hyp_with_status(conn, "hyp-s", "supported", qid=qid)

    out = await recall(db, question_id=qid, kinds=("hypothesis",))
    by_id = {c["id"]: c for c in out}
    assert by_id["hyp-t"]["superseded"] is False
    assert by_id["hyp-s"]["superseded"] is False
    assert by_id["hyp-1"]["superseded"] is False
    assert by_id["hyp-t"]["score"] == by_id["hyp-s"]["score"] == 1


# ---------------------------------------------------------------------------
# B1:purpose 参数 —— 召回意图的分光焊点。解析顺序 = 显式 > preset > 旧默认
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_purpose_investigate_equivalent_to_default(db):
    """purpose='investigate' ≡ 显式 kinds=(revision, hypothesis, evidence)(等价性)。

    preset 扩容(B4 第一刀)后 investigate 不再等于旧默认 (revision, hypothesis),
    等价对象改为扩容后的 kinds 元组;解析顺序语义(显式 > preset)由
    ``test_recall_explicit_args_win_over_purpose`` 单独守着。
    """
    from selene_space_storage.repositories.research_graph.recall import (
        EVIDENCE,
        HYPOTHESIS,
        REVISION,
        recall,
    )

    qid = await _seed_anchored_chain(db, qid="q-p1")

    explicit = await recall(db, question_id=qid, kinds=(REVISION, HYPOTHESIS, EVIDENCE))
    with_purpose = await recall(db, question_id=qid, purpose="investigate")
    assert with_purpose == explicit


@pytest.mark.asyncio
async def test_recall_explicit_args_win_over_purpose(db):
    """显式传 kinds/require_class 时显式参数赢过 purpose preset。"""
    from selene_space_storage.repositories.research_graph.recall import (
        LEARNED_EXPERIENCE,
        recall,
    )

    # q-p2 下只有一条走不到证据链的 hypothesis(Research Record)
    qid = await _seed_broken_chain(db, qid="q-p2")

    # purpose 默认 kinds=(revision, hypothesis) 且 require_class=None → hyp-1 召回
    plain = await recall(db, question_id=qid, purpose="investigate")
    assert [c["id"] for c in plain] == ["hyp-1"]

    # 显式 require_class=learned_experience 覆盖 preset 的 None → 被闸掉
    gated = await recall(
        db,
        question_id=qid,
        purpose="investigate",
        require_class=LEARNED_EXPERIENCE,
    )
    assert gated == []

    # 显式 kinds 覆盖 preset
    only_hyp = await recall(
        db, question_id=qid, purpose="investigate", kinds=("hypothesis",)
    )
    assert [c["id"] for c in only_hyp] == ["hyp-1"]


@pytest.mark.asyncio
async def test_recall_unknown_purpose_raises(db):
    from selene_space_storage.repositories.research_graph.recall import recall

    with pytest.raises(ValueError, match="unknown purpose"):
        await recall(db, question_id="q-any", purpose="no_such_purpose")


@pytest.mark.asyncio
async def test_recall_purpose_profile_generation_narrows_to_revisions(db):
    """purpose='profile_generation' 只召回 revision(预设分光的实际效果)。"""
    from selene_space_storage.repositories.research_graph.recall import recall

    qid = await _seed_anchored_chain(db, qid="q-p4")  # 有 rev-1
    async with db.connection() as conn:
        await _seed_hyp_with_status(conn, "hyp-p4", "proposed", qid=qid)

    out = await recall(db, question_id=qid, purpose="profile_generation")
    kinds_seen = {c["kind"] for c in out}
    assert kinds_seen == {"revision"}, "profile_generation 预设不含 hypothesis"


# ---------------------------------------------------------------------------
# B4 第一刀:evidence 成为 recall 候选 kind —— probe 观察进账本
# ---------------------------------------------------------------------------


async def _seed_probe_evidence(db, *, qid: str, ev_id: str = "ev-x") -> str:
    """问题 + market episode + 带 summary 的 RECORDED 证据 + addresses 边。"""
    async with db.connection() as conn:
        await _seed_question(conn, qid, strategy_ids=["strat-a"])
        await conn.execute(
            "INSERT INTO research_episodes (id, subject_type, subject_id, title, "
            "boundary_start, boundary_end, occurred_at, created_at) "
            "VALUES ('ep-1', 'market', 'BTC/USDT', 'IC probe', ?, ?, ?, ?)",
            (T0, T0, T0, T0),
        )
        await conn.execute(
            "INSERT INTO research_evidence (id, episode_id, mode, payload_json, "
            "observed_at, created_at) VALUES (?, 'ep-1', 'RECORDED', ?, ?, ?)",
            (
                ev_id,
                '{"summary": "funding_z last90d=+0.0121", "probe": "funding_ic"}',
                T0,
                T0,
            ),
        )
        await conn.execute(
            "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
            "kind, edge_class, actor, created_at) "
            f"VALUES ('e-{ev_id}', 'evidence', '{ev_id}', 'question', '{qid}', "
            "'addresses', 'fact', 'system', ?)",
            (T0,),
        )
    return qid


@pytest.mark.asyncio
async def test_recall_evidence_kind_is_candidate(db):
    from selene_space_storage.repositories.research_graph.recall import (
        LEARNED_EXPERIENCE,
        recall,
    )

    qid = await _seed_probe_evidence(db, qid="q-b4a")
    out = await recall(db, question_id=qid, kinds=("evidence",))

    assert len(out) == 1
    cand = out[0]
    assert cand["id"] == "ev-x"
    assert cand["kind"] == "evidence"
    assert cand["recall_class"] == LEARNED_EXPERIENCE
    assert cand["evidence_chain"] == ["evidence:ev-x", "episode:ep-1"]
    assert cand["text"] == "funding_z last90d=+0.0121"
    assert cand["superseded"] is False
    assert cand["matched_on"], "matched 必须可解释"


@pytest.mark.asyncio
async def test_evidence_without_overlap_not_recalled(db):
    """anchor 无重叠的 evidence 不召回(v0 纪律:匹配不上的宁可不召回)。"""
    from selene_space_storage.repositories.research_graph.recall import recall

    await _seed_probe_evidence(db, qid="q-b4b")

    out = await recall(db, anchor={"strategy_ids": ["strat-z"]}, kinds=("evidence",))
    assert out == []


@pytest.mark.asyncio
async def test_evidence_matches_by_subject(db):
    """1d 的 episode 跳:subject=('market', symbol) 经 evidence→episode 匹配。"""
    from selene_space_storage.repositories.research_graph.recall import recall

    await _seed_probe_evidence(db, qid="q-b4c")

    out = await recall(
        db,
        subject=("market", "BTC/USDT"),
        anchor={"strategy_ids": ["strat-a"]},
        kinds=("evidence",),
    )
    assert len(out) == 1
    assert "subject:market" in out[0]["matched_on"]


@pytest.mark.asyncio
async def test_investigate_preset_includes_evidence(db):
    """investigate preset 扩容后三类 kind 齐全(探针证据进调查会)。"""
    from selene_space_storage.repositories.research_graph.recall import recall

    qid = await _seed_probe_evidence(db, qid="q-b4d")
    async with db.connection() as conn:
        await _seed_hyp_with_status(conn, "hyp-b4", "proposed", qid=qid)
        await conn.execute(
            "INSERT INTO research_revisions (id, question_id, seq, content, "
            "rationale_json, cited_json, actor, created_at) "
            "VALUES ('rev-b4', ?, 1, '探针前置判断', '[]', '[]', 'selene', ?)",
            (qid, T0),
        )

    out = await recall(db, question_id=qid, purpose="investigate")
    kinds_seen = {c["kind"] for c in out}
    assert kinds_seen == {"revision", "hypothesis", "evidence"}


# ---------------------------------------------------------------------------
# evidence payload → 可读文本(真库实测 11 条候选里 4 条曾整坨 JSON 进 prompt)
# ---------------------------------------------------------------------------


def test_evidence_text_prefers_readable_keys_in_order():
    from selene_space_storage.repositories.research_graph.recall import (
        _evidence_text,
    )

    assert _evidence_text({"summary": "探针摘要"}, "{}") == "探针摘要"
    assert _evidence_text({"conclusion": "结论文本"}, "{}") == "结论文本"
    # summary 优先于 conclusion
    assert _evidence_text({"summary": "S", "conclusion": "C"}, "{}") == "S"
    # 空白串不算可读值
    assert _evidence_text({"summary": "   "}, '{"a": 1}') == '{"a": 1}'


def test_evidence_text_summarizes_snapshot_instead_of_dumping_json():
    """行情快照无 summary/conclusion → 结构化一行摘要,不是 124KB JSON。"""
    from selene_space_storage.repositories.research_graph.recall import (
        _evidence_text,
    )

    payload = {
        "boundary": {"start": "2026-09-05T13:00:00", "end": "2026-09-26T08:00:00"},
        "params": {
            "exchange": "binance",
            "factor": "momentum",
            "interval": "1h",
            "market_type": "futures",
            "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        },
        "rows": [["2026-09-05T13:00:00", 1, 2, 3]] * 5000,
    }
    raw = str(payload)
    out = _evidence_text(payload, raw)
    assert out.startswith("行情快照 momentum")
    assert "1h" in out
    assert "3标的" in out
    assert "2026-09-05T13:00:00~2026-09-26T08:00:00" in out
    assert len(out) < 200, "结构化摘要必须远短于原始 JSON"
    assert "rows" not in out


def test_evidence_text_truncates_unknown_payload():
    """既无可读键也无结构字段 → 截断,不原样透传。"""
    from selene_space_storage.repositories.research_graph.recall import (
        _EVIDENCE_TEXT_MAX,
        _evidence_text,
    )

    raw = "x" * (_EVIDENCE_TEXT_MAX + 500)
    out = _evidence_text({}, raw)
    assert len(out) <= _EVIDENCE_TEXT_MAX + 10
    assert out.endswith("…(截断)")


@pytest.mark.asyncio
async def test_recall_candidate_text_never_dumps_raw_json(db):
    """端到端:conclusion 型 evidence 的候选文本必须是 conclusion,
    不是 `{"conclusion": ...}` 原始串。"""
    from selene_space_storage.repositories.research_graph.recall import recall

    qid = "q-concl"
    async with db.connection() as conn:
        await _seed_question(conn, qid, strategy_ids=["strat-a"])
        await conn.execute(
            "INSERT INTO research_episodes (id, subject_type, subject_id, title, "
            "boundary_start, boundary_end, occurred_at, created_at) "
            "VALUES ('ep-c', 'market', 'BTC/USDT', 'p', ?, ?, ?, ?)",
            (T0, T0, T0, T0),
        )
        await conn.execute(
            "INSERT INTO research_evidence (id, episode_id, mode, payload_json, "
            "observed_at, created_at) VALUES ('ev-c', 'ep-c', 'RECORDED', ?, ?, ?)",
            (
                '{"conclusion": "全族 |IC|<=0.02,不进词表", '
                '"key_numbers": {"a": 1}, "verdict": "NO-GO"}',
                T0,
                T0,
            ),
        )
        await conn.execute(
            "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
            "kind, edge_class, actor, created_at) "
            "VALUES ('e-ev-c', 'evidence', 'ev-c', 'question', ?, "
            "'addresses', 'fact', 'system', ?)",
            (qid, T0),
        )

    out = await recall(db, question_id=qid, purpose="investigate")
    ev = [c for c in out if c["id"] == "ev-c"]
    assert ev, "evidence 候选应被取回"
    text = ev[0]["text"]
    assert text == "全族 |IC|<=0.02,不进词表"
    assert not text.startswith("{")
    assert "key_numbers" not in text


# ---- v025 horizon 透传 -------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_revision_carries_horizon(db):
    """revision 召回项必须带 horizon 键;tactical 落库后原样透传。"""
    from selene_space_storage.repositories.research_graph.recall import recall

    async with db.connection() as conn:
        await _seed_question(conn, "q-h1", strategy_ids=["strat-h"])
        await conn.execute(
            "INSERT INTO research_revisions (id, question_id, seq, content, "
            "actor, horizon, created_at) "
            "VALUES ('rev-h1', 'q-h1', 1, '结构性结论', 'selene', 'structural', ?)",
            (T0,),
        )
        await conn.execute(
            "INSERT INTO research_revisions (id, question_id, seq, content, "
            "actor, horizon, created_at) "
            "VALUES ('rev-h2', 'q-h1', 2, '战术判断', 'selene', 'tactical', ?)",
            (T0,),
        )

    out = await recall(db, question_id="q-h1", kinds=("revision",))
    by_id = {c["id"]: c for c in out}
    assert by_id["rev-h1"]["horizon"] == "structural"
    assert by_id["rev-h2"]["horizon"] == "tactical"


@pytest.mark.asyncio
async def test_recall_hypothesis_has_no_horizon_key(db):
    """hypothesis 无 horizon 列 —— 召回项不该有这个键(消费侧按键判型)。"""
    from selene_space_storage.repositories.research_graph.recall import recall

    async with db.connection() as conn:
        await _seed_question(conn, "q-h2", strategy_ids=["strat-h"])
        await conn.execute(
            "INSERT INTO research_hypotheses (id, statement, status, created_at, updated_at) "
            "VALUES ('hyp-h2', '假设一', 'proposed', ?, ?)",
            (T0, T0),
        )
        await conn.execute(
            "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
            "kind, edge_class, actor, created_at) "
            "VALUES ('e-h2', 'hypothesis', 'hyp-h2', 'question', 'q-h2', "
            "'addresses', 'fact', 'system', ?)",
            (T0,),
        )

    out = await recall(db, question_id="q-h2", kinds=("hypothesis",))
    assert out, "hypothesis 候选应被取回"
    assert all("horizon" not in c for c in out)


# ---------------------------------------------------------------------------
# 账本回放 recall(as_of) —— 认识论过滤 + data_window(N1 消费侧刀)
#
# 语义:as_of 只保留 created_at <= as_of 的候选(账本"当时知道"什么)。
# 硬约束:as_of=None 时逐键与改动前一致(文件上半的既有测试即回归锚)。
# ---------------------------------------------------------------------------

T_MID = "2026-09-15T00:00:00+00:00"  # 居中
T_LATE = "2026-12-31T23:59:59+00:00"  # 晚于全部
T_EARLY = "2026-01-01T00:00:00+00:00"  # 早于全部
T_NEW = "2026-10-01T00:00:00+00:00"


async def _seed_two_eras(db, *, qid: str) -> str:
    """同问题下放早/晚两个 era 的 episode+evidence+revision。"""
    async with db.connection() as conn:
        await _seed_question(conn, qid, strategy_ids=["strat-a"])
        for eid, ts in (("ep-old", T0), ("ep-new", T_NEW)):
            await conn.execute(
                "INSERT INTO research_episodes (id, subject_type, subject_id, title, "
                "boundary_start, boundary_end, occurred_at, created_at) "
                "VALUES (?, 'strategy', 'strat-a', ?, ?, ?, ?, ?)",
                (eid, "段", ts, ts, ts, ts),
            )
        for evid, epid, ts in (("ev-old", "ep-old", T0), ("ev-new", "ep-new", T_NEW)):
            await conn.execute(
                "INSERT INTO research_evidence (id, episode_id, mode, observed_at, "
                "created_at) VALUES (?, ?, 'RECORDED', ?, ?)",
                (evid, epid, ts, ts),
            )
        for eid, evid in (("e-old", "ev-old"), ("e-new", "ev-new")):
            await conn.execute(
                "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
                "kind, edge_class, actor, created_at) "
                "VALUES (?, 'evidence', ?, 'question', ?, 'addresses', 'fact', "
                "'system', ?)",
                (eid, evid, qid, T0),
            )
        for rid, seq, ts in (("rev-old", 1, T0), ("rev-new", 2, T_NEW)):
            await conn.execute(
                "INSERT INTO research_revisions (id, question_id, seq, content, "
                "rationale_json, cited_json, actor, created_at) "
                "VALUES (?, ?, ?, ?, '[]', '[]', 'selene', ?)",
                (rid, qid, seq, "判断", ts),
            )
    return qid


@pytest.mark.asyncio
async def test_recall_as_of_none_keeps_key_set_identical(db):
    """硬约束:无 as_of 时键集合与今天完全一致(零行为变更)。"""
    from selene_space_storage.repositories.research_graph.recall import recall

    qid = await _seed_anchored_chain(db, qid="q-k")
    out = await recall(
        db, question_id=qid, kinds=("revision", "evidence", "hypothesis")
    )
    assert out, "种子应产出候选"
    allowed = {
        "id",
        "kind",
        "text",
        "question_id",
        "score",
        "matched_on",
        "recall_class",
        "superseded",
        "evidence_chain",
        "created_at",
        "horizon",
    }
    for c in out:
        assert "data_window" not in c, "默认路径不得带回放专属键"
        assert set(c.keys()) <= allowed


@pytest.mark.asyncio
async def test_recall_as_of_earlier_than_everything_is_empty(db):
    from selene_space_storage.repositories.research_graph.recall import recall

    qid = await _seed_two_eras(db, qid="q-e")
    out = await recall(
        db, question_id=qid, kinds=("revision", "evidence"), as_of=T_EARLY
    )
    assert out == [], "回放日早于一切记录 -> 空账本"


@pytest.mark.asyncio
async def test_recall_as_of_mid_keeps_only_earlier(db):
    from selene_space_storage.repositories.research_graph.recall import recall

    qid = await _seed_two_eras(db, qid="q-m")
    out = await recall(db, question_id=qid, kinds=("revision", "evidence"), as_of=T_MID)
    ids = {c["id"] for c in out}
    assert ids == {"rev-old", "ev-old"}, f"只应保留 as_of 前记录的,实际 {ids}"


@pytest.mark.asyncio
async def test_recall_as_of_late_matches_unanchored_id_set(db):
    """as_of 晚于全部 -> id 集合与无 as_of 相同(但 evidence 多 data_window)。"""
    from selene_space_storage.repositories.research_graph.recall import recall

    qid = await _seed_two_eras(db, qid="q-l")
    base = await recall(db, question_id=qid, kinds=("revision", "evidence"))
    late = await recall(
        db, question_id=qid, kinds=("revision", "evidence"), as_of=T_LATE
    )
    assert {c["id"] for c in base} == {c["id"] for c in late}
    evs = [c for c in late if c["kind"] == "evidence"]
    assert evs and all("data_window" in c for c in evs)


@pytest.mark.asyncio
async def test_recall_as_of_evidence_data_window_matches_episode(db):
    from selene_space_storage.repositories.research_graph.recall import recall

    qid = await _seed_two_eras(db, qid="q-w")
    out = await recall(db, question_id=qid, kinds=("evidence",), as_of=T_LATE)
    by_id = {c["id"]: c for c in out}
    assert by_id["ev-old"]["data_window"] == {"start": T0, "end": T0}
    assert by_id["ev-new"]["data_window"] == {"start": T_NEW, "end": T_NEW}


@pytest.mark.asyncio
async def test_recall_as_of_invalid_raises(db):
    from selene_space_storage.repositories.research_graph.recall import recall

    qid = await _seed_anchored_chain(db, qid="q-x")
    with pytest.raises(ValueError, match="invalid as_of"):
        await recall(db, question_id=qid, kinds=("revision",), as_of="not-a-date")


@pytest.mark.asyncio
async def test_recall_as_of_excludes_unparseable_created_at(db):
    """created_at 不可解析 = 无法断言当时已知 -> 排除,不静默收入。"""
    from selene_space_storage.repositories.research_graph.recall import recall

    async with db.connection() as conn:
        await _seed_question(conn, "q-bad", strategy_ids=["strat-a"])
        await conn.execute(
            "INSERT INTO research_revisions (id, question_id, seq, content, "
            "rationale_json, cited_json, actor, created_at) "
            "VALUES ('rev-bad', 'q-bad', 1, ?, '[]', '[]', 'selene', ?)",
            ("坏时间", "not-a-timestamp"),
        )
    out = await recall(db, question_id="q-bad", kinds=("revision",), as_of=T_LATE)
    assert out == [], "不可解析的 created_at 在回放模式下必须排除"


@pytest.mark.asyncio
async def test_recall_as_of_naive_created_at_compared_as_utc(db):
    """真库存在 naive 串 —— naive 按 UTC 兜底比较。"""
    from selene_space_storage.repositories.research_graph.recall import recall

    async with db.connection() as conn:
        await _seed_question(conn, "q-nv", strategy_ids=["strat-a"])
        await conn.execute(
            "INSERT INTO research_episodes (id, subject_type, subject_id, title, "
            "boundary_start, boundary_end, occurred_at, created_at) "
            "VALUES ('ep-nv', 'strategy', 'strat-a', ?, ?, ?, ?, ?)",
            ("段", T0, T0, T0, T0),
        )
        await conn.execute(
            "INSERT INTO research_evidence (id, episode_id, mode, observed_at, "
            "created_at) VALUES ('ev-nv', 'ep-nv', 'RECORDED', ?, ?)",
            (T0, "2026-09-24T05:00:00"),
        )
        await conn.execute(
            "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
            "kind, edge_class, actor, created_at) "
            "VALUES ('e-nv', 'evidence', 'ev-nv', 'question', 'q-nv', 'addresses', "
            "'fact', 'system', ?)",
            (T0,),
        )
    keep = await recall(
        db, question_id="q-nv", kinds=("evidence",), as_of="2026-09-25T00:00:00+00:00"
    )
    drop = await recall(
        db, question_id="q-nv", kinds=("evidence",), as_of="2026-09-24T00:00:00+00:00"
    )
    assert [c["id"] for c in keep] == ["ev-nv"], "naive 串按 UTC 后应早于 09-25"
    assert drop == [], "应晚于 09-24T00:00"


@pytest.mark.asyncio
async def test_recall_as_of_respects_matched_gate(db):
    """matched 门 x as_of:零边 evidence 任何 as_of 下都不可见(钉死契约)。

    这是 v1 事故的护栏 —— 验收计数必须走功能路径,不能数裸表。
    """
    from selene_space_storage.repositories.research_graph.recall import recall

    async with db.connection() as conn:
        await _seed_question(conn, "q-gate", strategy_ids=["strat-a"])
        await conn.execute(
            "INSERT INTO research_episodes (id, subject_type, subject_id, title, "
            "boundary_start, boundary_end, occurred_at, created_at) "
            "VALUES ('ep-ok', 'strategy', 'strat-a', ?, ?, ?, ?, ?)",
            ("段", T0, T0, T0, T0),
        )
        await conn.execute(
            "INSERT INTO research_evidence (id, episode_id, mode, observed_at, "
            "created_at) VALUES ('ev-ok', 'ep-ok', 'RECORDED', ?, ?)",
            (T0, T0),
        )
        await conn.execute(
            "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
            "kind, edge_class, actor, created_at) "
            "VALUES ('e-ok', 'evidence', 'ev-ok', 'question', 'q-gate', 'addresses', "
            "'fact', 'system', ?)",
            (T0,),
        )
        # 零边 -> matched 门拦下
        await conn.execute(
            "INSERT INTO research_evidence (id, episode_id, mode, observed_at, "
            "created_at) VALUES ('ev-lone', 'ep-ok', 'RECORDED', ?, ?)",
            (T0, T0),
        )
    out = await recall(db, question_id="q-gate", kinds=("evidence",), as_of=T_LATE)
    ids = {c["id"] for c in out}
    assert "ev-ok" in ids, "有边 + anchor 重叠 + 时间合规 -> 保留"
    assert "ev-lone" not in ids, "零边 -> matched 门拦下(与 as_of 无关)"


# -- 公开函数抽取(US-1): parse_as_of / filter_known_by -----------------------


def test_parse_as_of_valid_returns_aware_datetime():
    from selene_space_storage.repositories.research_graph.recall import parse_as_of

    ts = parse_as_of("2026-09-27T23:59:59+00:00")
    assert ts is not None
    assert ts.tzinfo is not None
    assert (ts.year, ts.month, ts.day) == (2026, 9, 27)


def test_parse_as_of_invalid_raises():
    from selene_space_storage.repositories.research_graph.recall import parse_as_of

    with pytest.raises(ValueError, match="invalid as_of"):
        parse_as_of("not-a-date")


def test_filter_known_by_threshold_and_unparseable():
    from datetime import UTC, datetime

    from selene_space_storage.repositories.research_graph.recall import (
        filter_known_by,
    )

    threshold = datetime(2026, 9, 27, 23, 59, 59, tzinfo=UTC)
    items = [
        {"id": "old", "created_at": "2026-09-01T00:00:00+00:00"},
        {"id": "same", "created_at": "2026-09-27T23:59:59+00:00"},
        {"id": "new", "created_at": "2026-09-28T00:00:00+00:00"},
        {"id": "bad", "created_at": "not-a-date"},
        {"id": "none", "created_at": None},
    ]
    out = filter_known_by(items, threshold)
    ids = [i["id"] for i in out]
    assert ids == ["old", "same"], "边界含等号;解析不出/晚于阈值都排除"


def test_filter_known_by_custom_key():
    from datetime import UTC, datetime

    from selene_space_storage.repositories.research_graph.recall import (
        filter_known_by,
    )

    threshold = datetime(2026, 9, 15, tzinfo=UTC)
    items = [{"id": "a", "ts": "2026-09-01"}, {"id": "b", "ts": "2026-10-01"}]
    out = filter_known_by(items, threshold, key="ts")
    assert [i["id"] for i in out] == ["a"]

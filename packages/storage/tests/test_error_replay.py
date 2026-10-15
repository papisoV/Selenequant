"""US-202:失败经验回放(v017)行为锁。

三条主线:
① fail→fail→success 闭环:error_types upsert 去重 + resolved_by 边 + 召回返回正确对
② 退化剪枝:值检查错→执行错相邻对被剪
③ 20 连败熔断返回 Abandoned
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph import (
    Abandoned,
    ErrorTypeRepository,
    ExperimentRepository,
    HypothesisRepository,
    ResearchEdgeRepository,
    analyze_error,
    classify_error,
    recall_error_replay,
    render_error_replay,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

VALUE_ERR = "信号索引与行情索引不一致"


def _tb(message: str = "length mismatch") -> str:
    """能命中 _TRACEBACK_RE 的真实格式反馈。"""
    return (
        'File "factor.py", line 10, in compute\n'
        '    df["close"].shift(-1)\n'
        f"ValueError: {message}"
    )


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


async def _make_setup(db):
    hyp = HypothesisRepository(db)
    exp = ExperimentRepository(db)
    edges = ResearchEdgeRepository(db)
    errors = ErrorTypeRepository(db)
    hypothesis_id = await hyp.create("动量因子在 4h 上有效")
    return exp, edges, errors, hypothesis_id


async def _fail(exp, edges, errors, hypothesis_id, feedback, *, code="bad code"):
    eid = await exp.create(
        "backtest", hypothesis_id=hypothesis_id, config={"code": code}
    )
    await exp.mark_ran(eid)
    content = analyze_error(feedback)[0]
    error_id = await errors.upsert(content)
    await exp.set_outcome(eid, "failed", [error_id])
    return eid, error_id


async def _success(exp, edges, from_id, hypothesis_id, *, code="good code"):
    sid = await exp.create(
        "backtest", hypothesis_id=hypothesis_id, config={"code": code}
    )
    await exp.mark_ran(sid)
    await exp.set_outcome(sid, "success")
    await edges.add_fact_edge(
        ("experiment", from_id), ("experiment", sid), "resolved_by"
    )
    return sid


# --- 闭环:fail→fail→success ----------------------------------------------


@pytest.mark.asyncio
async def test_upsert_dedup_and_resolved_by(db):
    exp, edges, errors, hyp_id = await _make_setup(db)
    e1, err1 = await _fail(exp, edges, errors, hyp_id, _tb())
    e2, err2 = await _fail(exp, edges, errors, hyp_id, _tb())
    # 同错误文本 upsert 去重:同一 error id,计数累加
    assert err1 == err2
    row = await errors.get(err1)
    assert row["occurrence_count"] == 2

    await _success(exp, edges, e2, hyp_id)

    result = await recall_error_replay(
        db, hyp_id, experiments=exp, edges=edges, error_types=errors
    )
    assert not isinstance(result, Abandoned)
    assert len(result.error_pairs) == 1
    pair = result.error_pairs[0]
    assert pair.error_content == analyze_error(_tb())[0]
    assert pair.success_config["code"] == "good code"
    assert pair.failed_config["code"] == "bad code"


@pytest.mark.asyncio
async def test_recall_requires_shared_error_id(db):
    """最后失败的 error 与 resolved_by 边对端失败行的 error 不同 → 不配对。"""
    exp, edges, errors, hyp_id = await _make_setup(db)
    e1, _ = await _fail(exp, edges, errors, hyp_id, _tb())
    await _success(exp, edges, e1, hyp_id)
    # 之后又来一个不同错误的失败(成为 last_failed)
    await _fail(exp, edges, errors, hyp_id, "完全未知的错误", code="other")
    result = await recall_error_replay(
        db, hyp_id, experiments=exp, edges=edges, error_types=errors
    )
    assert result.error_pairs == []


# --- 退化剪枝 --------------------------------------------------------------


@pytest.mark.asyncio
async def test_deterioration_pruning(db):
    exp, edges, errors, hyp_id = await _make_setup(db)
    # 时间正序:执行错 → 值检查错 → 执行错。[值检查→执行错]相邻对剪掉后者
    await _fail(exp, edges, errors, hyp_id, _tb(), code="c1")
    await _fail(exp, edges, errors, hyp_id, VALUE_ERR, code="c2")
    await _fail(exp, edges, errors, hyp_id, _tb(), code="c3")
    result = await recall_error_replay(
        db, hyp_id, experiments=exp, edges=edges, error_types=errors
    )
    pruned_codes = [f["config"]["code"] for f in result.former_fails]
    assert "c3" not in pruned_codes
    assert pruned_codes == ["c1", "c2"]


# --- 20 连败熔断 ------------------------------------------------------------


@pytest.mark.asyncio
async def test_twenty_fails_circuit_breaker(db):
    exp, edges, errors, hyp_id = await _make_setup(db)
    for _ in range(20):
        await _fail(exp, edges, errors, hyp_id, _tb(), code="x")
    result = await recall_error_replay(
        db, hyp_id, experiments=exp, edges=edges, error_types=errors
    )
    assert isinstance(result, Abandoned)
    assert result.fail_count == 20


@pytest.mark.asyncio
async def test_nineteen_fails_not_abandoned(db):
    exp, edges, errors, hyp_id = await _make_setup(db)
    for _ in range(19):
        await _fail(exp, edges, errors, hyp_id, _tb(), code="x")
    result = await recall_error_replay(
        db, hyp_id, experiments=exp, edges=edges, error_types=errors
    )
    assert not isinstance(result, Abandoned)


# --- set_outcome 校验 -------------------------------------------------------


@pytest.mark.asyncio
async def test_set_outcome_rejects_success_with_errors(db):
    exp, edges, errors, hyp_id = await _make_setup(db)
    err = await errors.upsert("e")
    eid = await exp.create("backtest", hypothesis_id=hyp_id)
    with pytest.raises(ValueError, match="success rows carry no error ids"):
        await exp.set_outcome(eid, "success", [err])


@pytest.mark.asyncio
async def test_set_outcome_rejects_unknown(db):
    exp, edges, errors, hyp_id = await _make_setup(db)
    eid = await exp.create("backtest", hypothesis_id=hyp_id)
    with pytest.raises(ValueError, match="outcome must be"):
        await exp.set_outcome(eid, "maybe")


# --- 渲染 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_injection_template(db):
    """执行错→值错→执行错的序列:对回放 + 剪后失败史 + 被剪的最新失败提醒。"""
    exp, edges, errors, hyp_id = await _make_setup(db)
    e1, _ = await _fail(exp, edges, errors, hyp_id, _tb(), code="bad")
    await _fail(exp, edges, errors, hyp_id, VALUE_ERR, code="mid")
    await _fail(exp, edges, errors, hyp_id, _tb(), code="latest")
    await _success(exp, edges, e1, hyp_id)

    result = await recall_error_replay(
        db, hyp_id, experiments=exp, edges=edges, error_types=errors
    )
    text = render_error_replay(result, hypothesis_statement="动量因子在 4h 上有效")
    assert "--------------Target hypothesis:---------------" in text
    assert "动量因子在 4h 上有效" in text
    # 同错误回放对(e1 失败代码 → 成功代码)
    assert "=====Failed code with this error:=====" in text
    assert "bad" in text
    assert "=====Success code that resolved it:=====" in text
    assert "good" in text
    # 剪后失败史含 c1(mid 在 latest 之前),最新失败进提醒块
    assert "mid" in text
    assert "=====Latest attempt=====" in text
    assert "latest" in text
    assert "=====Feedback=====" in text


# --- CoSTEER 三层分类(US-301) ----------------------------------------------


def test_classify_error_three_layers():
    """白名单→return_checking / traceback→execution / 自检不达标→code / 兜底。"""
    assert classify_error(VALUE_ERR) == [(VALUE_ERR, "return_checking")]
    assert classify_error(_tb()) == [
        ('ErrorType: ValueError\nError line: df["close"].shift(-1)', "execution")
    ]
    code_feedback = "回测完成,夏普 0.3 指标不达标,低于随机基线"
    assert classify_error(code_feedback) == [(code_feedback, "code")]
    assert classify_error("完全未知的现象") == [("Undefined Error", "undefined")]


def test_classify_error_traceback_beats_code_patterns():
    """既有 traceback 又有不达标文本 → execution 优先(跑崩是硬事实)。"""
    mixed = _tb() + "\n回测失败:指标不达标"
    layers = {layer for _, layer in classify_error(mixed)}
    assert layers == {"execution"}


def test_classify_error_t4_verdicts_are_code_layer():
    """T4 验证协议 verdict(实战桥接发现的真实反馈文本)→ code 层。"""
    text = "INVALID: degenerate output (constant/empty factor panel)"
    assert classify_error(text) == [(text, "code")]
    text2 = "INSUFFICIENT_DATA: fewer than 2 finite OOS folds — no usable OOS evidence"
    assert classify_error(text2) == [(text2, "code")]


@pytest.mark.asyncio
async def test_upsert_layer_upgrade(db):
    """同文本先 undefined 后具体层 → 升级;再以 undefined 重复 → 不回退。"""
    _, _, errors, _ = await _make_setup(db)
    eid = await errors.upsert("同一种错")
    assert (await errors.get(eid))["layer"] == "undefined"
    eid2 = await errors.upsert("同一种错", layer="execution")
    assert eid2 == eid
    row = await errors.get(eid)
    assert row["layer"] == "execution"
    assert row["occurrence_count"] == 2
    await errors.upsert("同一种错")
    assert (await errors.get(eid))["layer"] == "execution"

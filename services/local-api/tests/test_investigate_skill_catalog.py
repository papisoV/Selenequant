"""批次4:启用的技能目录注入调查会 prompt(名称+一句描述,带字符预算)。

覆盖:
- render_skill_catalog 纯函数:预算截断 / 不注入正文 / model_invocable 过滤 /
  注册表缺失与异常降级
- 端到端:注册表里有的技能在四张 prompt(三视角+主持人)里出现,
  rationale 记 skills_injected,但不进 CITED(候选集合不变)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio
from app.api.v1 import research_graph as rg
from app.services import investigate_council as council
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph import (
    ResearchQuestionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

pytestmark = pytest.mark.asyncio


def _skill(name: str, desc: str, *, model_invocable: bool = True):
    return SimpleNamespace(
        name=name,
        description=desc,
        frontmatter=SimpleNamespace(model_invocable=model_invocable),
    )


class _FakeRegistry:
    def __init__(self, skills):
        self._skills = list(skills)
        self.queries: list[str] = []

    def top_n_for_prompt(self, query, n=6):
        self.queries.append(query)
        return self._skills[:n]


def test_render_skill_catalog_lists_name_and_description():
    reg = _FakeRegistry(
        [
            _skill("backtest-runner", "跑历史回测并生成指标"),
            _skill("kline-view", "看 K 线结构"),
        ]
    )
    text, names = council.render_skill_catalog(reg, "SOL 4h 亏损根因")
    assert names == ["backtest-runner", "kline-view"]
    assert "- backtest-runner: 跑历史回测并生成指标" in text
    assert reg.queries == ["SOL 4h 亏损根因"]


def test_render_skill_catalog_respects_budget(monkeypatch):
    monkeypatch.setattr(council, "SKILLS_PROMPT_BUDGET_CHARS", 60)
    reg = _FakeRegistry([_skill(f"skill-{i}", "x" * 40) for i in range(5)])
    text, names = council.render_skill_catalog(reg, "research")
    # 每行 ≈ 49+ 字符,预算 60 → 只放得下一行
    assert len(names) == 1
    assert text.count("\n") == 0
    assert len(text) <= 60


def test_render_skill_catalog_skips_model_invocable_false():
    reg = _FakeRegistry(
        [
            _skill("hidden", "用户专用,模型不该看到", model_invocable=False),
            _skill("visible", "可被模型引用"),
        ]
    )
    text, names = council.render_skill_catalog(reg, "research")
    assert names == ["visible"]
    assert "hidden" not in text


def test_render_skill_catalog_truncates_long_description():
    reg = _FakeRegistry([_skill("verbose", "字" * 500)])
    text, _ = council.render_skill_catalog(reg, "research")
    assert "…" in text
    assert len(text) < 200


def test_render_skill_catalog_degrades_on_missing_or_broken_registry():
    assert council.render_skill_catalog(None, "research") == ("", [])

    class _Broken:
        def top_n_for_prompt(self, query, n=6):
            raise RuntimeError("registry exploded")

    # 技能系统故障不能让调查失败 —— 降级为空目录
    assert council.render_skill_catalog(_Broken(), "research") == ("", [])


@pytest_asyncio.fixture
async def ctx():
    from app.core import dependencies as deps

    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        deps.set_db_manager(db)
        app = create_app()
        app.dependency_overrides[rg.get_graph_db] = lambda: db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, db
        deps.set_skill_registry(None)
        deps.set_db_manager(None)
    rg._INVESTIGATE_TASKS.clear()
    rg._INVESTIGATE_STARTED_AT.clear()


async def _await_investigate() -> None:
    tasks = list(rg._INVESTIGATE_TASKS.values())
    rg._INVESTIGATE_TASKS.clear()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_skill_catalog_reaches_all_prompts_and_is_recorded(ctx):
    from app.core import dependencies as deps

    client, db = ctx
    deps.set_skill_registry(_FakeRegistry([_skill("backtest-runner", "跑回测")]))

    resp = await client.post(
        "/api/v1/research/graph/questions",
        json={"title": "backtest 结果是否可靠"},
    )
    qid = resp.json()["data"]["question_id"]

    prompts: list[str] = []

    async def _fake_llm(settings_service, prompt, **kwargs):
        prompts.append(prompt)
        return "结论。"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(
            f"/api/v1/research/graph/questions/{qid}/investigate", json={}
        )
        assert resp.status_code == 202, resp.text
        await _await_investigate()
    finally:
        monkeypatch.undo()

    assert len(prompts) == 4
    for p in prompts:
        assert "backtest-runner" in p
        # 目录被明确标成"能力提示",不是证据
        assert "不得进入 CITED" in p

    repo = ResearchQuestionRepository(db)
    revisions = await repo.list_revisions(qid)
    assert any(
        "skills_injected:backtest-runner" in (r.get("rationale") or [])
        for r in revisions
    )


async def test_no_registry_means_no_skill_section(ctx):
    client, _db = ctx  # 默认 registry=None

    resp = await client.post(
        "/api/v1/research/graph/questions", json={"title": "无关问题"}
    )
    qid = resp.json()["data"]["question_id"]

    prompts: list[str] = []

    async def _fake_llm(settings_service, prompt, **kwargs):
        prompts.append(prompt)
        return "结论。"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        await client.post(
            f"/api/v1/research/graph/questions/{qid}/investigate", json={}
        )
        await _await_investigate()
    finally:
        monkeypatch.undo()

    assert all("可用的研究方法技能" not in p for p in prompts)

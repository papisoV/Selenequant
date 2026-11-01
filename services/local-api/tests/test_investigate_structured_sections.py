"""④ 结构化字段:【反向检验】/【适用条件】/置信度 的解析与落库。

背景(实测):prompt 早就强制五段,但 20 条 revision 里只有 3 条真写了
【反向检验】。所以这里测的不是"解析器好不好用",而是**缺口是否可见** ——
解析不到必须不写键 + 落 structured_missing 事件,不能静默留空。
"""

from __future__ import annotations

import asyncio

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

_FULL = """【时间线】
2026-10-01 出现第一次背离。

【观点整合与矛盾】
经验派与记录派在成交量口径上互斥,记录派有数据支撑,经验派无。

【深层分析】
资金费率转向领先价格两日。
置信度：0.65

【反向检验】
1h 收盘站上 78000 且随后三根同向实体,则本判断失效。

【适用条件】
BTC/USDT 1h,趋势行情;震荡区间内该结论不成立。

【新问题】
1. 资金费率领先性是否在 4h 同样成立
"""

_NO_FALSIFY = """【时间线】
t0。

【观点整合与矛盾】
无分歧。

【深层分析】
看不出什么。
置信度：无法给

【适用条件】
仅 BTC。

【新问题】
无
"""

_NO_APPLIES = """【时间线】
t0。

【深层分析】
结论是 A。
置信度：70

【反向检验】
收盘跌破 60000。
"""


def test_full_sections_are_all_parsed():
    structured, missing = council._parse_structured_sections(_FULL)
    assert structured["falsified_when"].startswith("1h 收盘站上 78000")
    assert structured["applies_when"].startswith("BTC/USDT 1h")
    assert structured["confidence"] == pytest.approx(0.65)
    assert missing == []


def test_missing_falsification_is_reported_not_silently_empty():
    structured, missing = council._parse_structured_sections(_NO_FALSIFY)
    assert "falsified_when" not in structured, "缺段必须不写键,而不是写空串"
    assert "反向检验" in missing
    # "无法给" 是模型的原话,要原样留下,不能被吞成 None
    assert structured["confidence"] == "无法给"


def test_missing_applies_when_is_reported():
    structured, missing = council._parse_structured_sections(_NO_APPLIES)
    assert "applies_when" not in structured
    assert "适用条件" in missing
    assert structured["falsified_when"] == "收盘跌破 60000。"


def test_confidence_percent_is_normalized_and_section_bound():
    """70 这种百分数要归一化;置信度只在【深层分析】段里找,
    别把【反向检验】里偶然出现的"置信"也算进来。"""
    structured, _ = council._parse_structured_sections(_NO_APPLIES)
    assert structured["confidence"] == pytest.approx(0.70)

    body = """【深层分析】
没问题。

【反向检验】
若置信度跌破 0.3 说明模型自己不信,这只是段外噪音。
"""
    structured2, missing2 = council._parse_structured_sections(body)
    assert "confidence" not in structured2
    assert "置信度" in missing2


def test_confidence_out_of_range_kept_as_raw_text():
    body = "【深层分析】\n置信度：120\n【反向检验】\nx\n【适用条件】\ny\n"
    structured, _ = council._parse_structured_sections(body)
    assert structured["confidence"] == "120"


# ---------------------------------------------------------------------------
# 端到端:落库 + 缺口事件
# ---------------------------------------------------------------------------


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
        deps.set_db_manager(None)
    rg._INVESTIGATE_TASKS.clear()
    rg._INVESTIGATE_STARTED_AT.clear()


async def _await_investigate() -> None:
    tasks = list(rg._INVESTIGATE_TASKS.values())
    rg._INVESTIGATE_TASKS.clear()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _run_with_host_reply(client, reply: str) -> str:
    resp = await client.post(
        "/api/v1/research/graph/questions", json={"title": "结构化字段回归"}
    )
    assert resp.status_code == 200, resp.text
    qid = str(resp.json()["data"]["question_id"])

    async def _fake_llm(settings_service, prompt, **kwargs):
        # 三视角 + 主持人共用一个假 LLM:主持人靠 prompt 特征区分
        if "你是主持人" in prompt:
            return reply
        return "视角结论。"

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
    return qid


async def test_compliant_reply_lands_structured_json(ctx):
    client, db = ctx
    qid = await _run_with_host_reply(client, _FULL)

    repo = ResearchQuestionRepository(db)
    revisions = await repo.list_revisions(qid)
    assert len(revisions) == 1
    structured = revisions[0]["structured"]
    assert structured["falsified_when"].startswith("1h 收盘站上 78000")
    assert structured["confidence"] == pytest.approx(0.65)
    # 全合规 → 不该有缺口事件
    assert await repo.list_events(qid, kind="structured_missing") == []

    # 详情端点也要带出来,否则前端只能读正文
    detail = await client.get(f"/api/v1/research/graph/questions/{qid}")
    assert detail.status_code == 200, detail.text
    revs = detail.json()["data"]["revisions"]
    assert revs[0]["structured"]["applies_when"].startswith("BTC/USDT")


async def test_noncompliant_reply_lands_missing_event(ctx):
    client, db = ctx
    qid = await _run_with_host_reply(client, _NO_APPLIES)

    repo = ResearchQuestionRepository(db)
    revisions = await repo.list_revisions(qid)
    assert revisions[0]["structured"].get("applies_when") is None
    # 缺口靠「键不存在」+ 事件表达,不写进 rationale —— rationale 记的是
    # 「这一版为什么长这样」(来源/回放/指示),不是产物质量。

    events = await repo.list_events(qid, kind="structured_missing")
    assert len(events) == 1
    assert events[0]["detail"]["missing"] == ["适用条件"]
    assert events[0]["detail"]["revision_id"] == revisions[0]["id"]

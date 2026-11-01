"""Tests for DecisionOutcomeMemory (B1) + rating parser (B2).

B2 语义吸收自 open-source-reference v0.6.0(见 decision_outcomes.py 模块注释):读不出
评级的决策一律 REVIEW,绝不伪装成 Hold——一个没人能读懂的判断不是 Hold,把它
记成 Hold 会被下一次运行当成从未做出的判断引回去(#1170/#1435/#1466)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from selene_space_agent.memory.decision_outcomes import (
    RATING_REVIEW,
    DecisionOutcomeMemory,
    extract_rating,
    human_view_entry,
    is_review,
    parse_rating,
)


@pytest.fixture()
def memory(tmp_path: Path) -> DecisionOutcomeMemory:
    return DecisionOutcomeMemory(memory_dir=tmp_path)


class TestParseRating:
    def test_label_line_priority(self) -> None:
        text = "Some rambling prose.\nRating: **Overweight**\nmore prose"
        assert parse_rating(text) == "Overweight"

    def test_label_with_hyphen(self) -> None:
        assert parse_rating("rating - Sell") == "Sell"

    def test_absent_rating_is_review_not_hold(self) -> None:
        # #1170: 读不出评级就是 REVIEW,绝不静默降级成可交易的 Hold。
        assert parse_rating("no rating words at all") == RATING_REVIEW

    def test_fullwidth_colon_and_dash_separators(self) -> None:
        # NFKC 规范化:全角冒号/破折号与 ASCII 一样匹配。
        assert parse_rating("Rating：Overweight") == "Overweight"
        assert parse_rating("Rating—Underweight") == "Underweight"


class TestRatingIntegrity:
    """回归:#1170/#1435/#1466 家族 —— 旧解析器在这些输入上读错方向。"""

    def test_quoted_rating_does_not_replace_the_decision(self) -> None:
        # 引别家的评级是论据,不是本次判断;决策自身的 "Rating: X" 才是。
        text = "The desk's consensus rating: Buy. Our view differs.\nRating: Hold"
        assert parse_rating(text) == "Hold"

    def test_prose_negation_is_not_read_as_the_call(self) -> None:
        # "not a Sell" 不是在建议卖出;正文评级词无标签时不可读成方向。
        assert extract_rating("We are not a Sell here; momentum is intact.") is None
        assert (
            parse_rating("We are not a Sell here; momentum is intact.") == RATING_REVIEW
        )

    def test_sell_side_margin_is_not_a_rating(self) -> None:
        # "Sell-side" 只是词尾撞上 "rating" 语境;"rating" 必须是词首。
        assert (
            extract_rating("Operating margin: Sell-side sits below guidance.") is None
        )

    def test_scale_legend_echo_is_not_the_rating(self) -> None:
        # 模型回显图例("Rating Scale: Buy, ...")不是决策;跳过图例行取真判断。
        text = (
            "Rating Scale: Buy, Overweight, Hold, Underweight, Sell\n"
            "Our call:\nRating: Overweight"
        )
        assert parse_rating(text) == "Overweight"

    def test_opening_rating_line_wins_even_if_a_later_label_differs(self) -> None:
        # 决策被要求以评级抬头;首行即为判断,后续标签不改写它。此为首行短路
        # 路径,与上游 TA v0.6.0 行为一致(已逐字比对)。
        assert extract_rating("Rating: Buy\nRating: Sell") == "Buy"

    def test_ratings_that_disagree_need_review(self) -> None:
        # 没有"本次判断"形状的行、而多处标签矛盾时,无法判定哪个是判断。
        text = "Trader's proposed rating: Buy\n\nPortfolio Manager's rating: Hold"
        assert extract_rating(text) is None

    def test_own_rating_line_wins_over_quoted_list_items(self) -> None:
        text = (
            "Broker views:\n- Rating: Buy (Morgan Stanley)\n- Rating: Hold (Goldman)"
            "\n\n**Rating**: Sell"
        )
        assert extract_rating(text) == "Sell"

    def test_a_refusal_has_no_rating(self) -> None:
        assert (
            parse_rating("I'm sorry, I can't provide a rating for this security.")
            == RATING_REVIEW
        )

    def test_is_review_sentinel(self) -> None:
        assert is_review(RATING_REVIEW)
        assert not is_review("Hold")
        assert not is_review("")

    def test_ranges_covered_by_labelled_rating(self) -> None:
        # 标签行优先:正文先说 Buy 再以 "Final rating — Underweight" 收尾,取后者。
        text = (
            "The aggressive analyst pushed hard for a Buy, but the conservative "
            "case carried.\nFinal rating — Underweight. Trim to half weight."
        )
        assert extract_rating(text) == "Underweight"


class TestRecordPending:
    def test_record_then_pending_list(self, memory: DecisionOutcomeMemory) -> None:
        memory.record_pending("dep-1", "strat-1", ["BTC/USDT"], "2026-09-28")
        assert memory.pending_deployments() == ["dep-1"]

    def test_rating_field_is_never_blank(self, memory: DecisionOutcomeMemory) -> None:
        # B2 接线契约:落盘 rating 恒为五档之一或 REVIEW,绝不留空。
        memory.record_pending(
            "dep-1",
            "strat-1",
            ["BTC/USDT"],
            "2026-09-28",
            decision_text="Deployed 'Momentum' on BTC/USDT 1h.",
        )
        assert memory._entries[0]["rating"] == RATING_REVIEW

    def test_explicit_rating_is_respected(self, memory: DecisionOutcomeMemory) -> None:
        memory.record_pending(
            "dep-1", "strat-1", ["BTC/USDT"], "2026-09-28", rating="Overweight"
        )
        assert memory._entries[0]["rating"] == "Overweight"

    def test_rating_derived_from_decision_text(
        self, memory: DecisionOutcomeMemory
    ) -> None:
        # 调用方不显式给 rating 时,从 decision_text 经完整性解析器导出。
        memory.record_pending(
            "dep-1",
            "strat-1",
            ["BTC/USDT"],
            "2026-09-28",
            decision_text="Consensus held. **Rating**: Underweight",
        )
        assert memory._entries[0]["rating"] == "Underweight"

    def test_deploy_log_line_yields_review_not_a_fabricated_hold(
        self, memory: DecisionOutcomeMemory
    ) -> None:
        # 真实部署路径的 decision_text 是代码拼的日志行,无方向判断;诚实结果是
        # REVIEW,而不是伪装成可交易的 Hold(#1170)。
        memory.record_pending(
            "dep-1",
            "strat-1",
            ["BTC/USDT"],
            "2026-09-28",
            decision_text=(
                "Deployed 'Buy the Dip' (strat-1) on BTC/USDT 1h, "
                "dedicated account acc-1."
            ),
        )
        assert memory._entries[0]["rating"] == RATING_REVIEW

    def test_same_deployment_idempotent_overwrite(
        self, memory: DecisionOutcomeMemory
    ) -> None:
        memory.record_pending("dep-1", "strat-1", ["BTC/USDT"], "2026-09-28")
        memory.record_pending("dep-1", "strat-1", ["ETH/USDT"], "2026-09-28")
        entries = memory.pending_deployments()
        assert entries == ["dep-1"]
        # 覆盖后 symbols 更新
        assert "[pending]" not in str(memory.recall("ETH/USDT"))

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        DecisionOutcomeMemory(memory_dir=tmp_path).record_pending(
            "dep-1", "strat-1", ["BTC/USDT"], "2026-09-28"
        )
        fresh = DecisionOutcomeMemory(memory_dir=tmp_path)
        assert fresh.pending_deployments() == ["dep-1"]


class TestPendingNeverRotatedAway:
    """N2:轮转必须永不挤掉 pending 条目(未处理的工作)。

    旧实现 ``self._entries = self._entries[-_MAX_ENTRIES:]`` 无条件保留最新 50
    条:一条排在第 50 名之外的 pending 决策,会被后续新记录挤掉,于是
    ``pending_deployments()`` 再也列不出它 —— 静默永久丢失,回填永远轮不到。
    pending 是"还没处理",不能因为攒够了数就丢;只有已回填的条目才可变旧。
    """

    def test_pending_survives_overflow(self, tmp_path: Path) -> None:
        memory = DecisionOutcomeMemory(memory_dir=tmp_path)
        # 一条早先的 pending(希望它能在 60 条之后存活)
        memory.record_pending("dep-old", "strat-old", ["BTC/USDT"], "2026-09-01")
        for i in range(60):
            memory.record_pending(f"dep-{i}", f"strat-{i}", ["BTC/USDT"], "2026-09-28")
        pending = memory.pending_deployments()
        assert "dep-old" in pending, "旧的 pending 不能被新记录挤掉(静默丢失)"

    def test_filled_entries_are_rotated(self, tmp_path: Path) -> None:
        # 已回填的条目才是可牺牲的:超过上限时丢最旧的 filled。
        memory = DecisionOutcomeMemory(memory_dir=tmp_path)
        for i in range(60):
            memory.record_pending(f"dep-{i}", f"strat-{i}", ["BTC/USDT"], "2026-09-28")
            memory.backfill(f"dep-{i}", {"summary": f"+{i}%"}, f"教训{i}")
        ids = [e["deployment_id"] for e in memory._entries]
        assert len(memory._entries) <= 50, "filled 条目仍受上限约束"
        assert "dep-59" in ids  # 最新的 filled 保留

    def test_pending_kept_even_when_filled_hits_cap(self, tmp_path: Path) -> None:
        # 混合场景:pending 夹在大量 filled 中间(位置早于轮转窗口),仍须保留;
        # 而最旧的 filled 该被丢掉 —— 证明轮转没被整个关掉。
        memory = DecisionOutcomeMemory(memory_dir=tmp_path)
        memory.record_pending("pending-mid", "s", ["BTC/USDT"], "2026-09-01")
        for i in range(60):
            memory.record_pending(f"filled-{i}", "s", ["BTC/USDT"], "2026-09-02")
            memory.backfill(f"filled-{i}", {"summary": "+1%"}, "r")
        ids = [e["deployment_id"] for e in memory._entries]
        assert (
            "pending-mid" in memory.pending_deployments()
        ), "夹在中间的 pending 不能被轮转挤掉"
        assert "filled-0" not in ids, "最旧的 filled 仍应被轮转丢掉"


class TestBackfill:
    def test_backfill_fills_and_flips_status(
        self, memory: DecisionOutcomeMemory
    ) -> None:
        memory.record_pending("dep-1", "strat-1", ["BTC/USDT"], "2026-09-28")
        ok = memory.backfill(
            "dep-1",
            {"summary": "+3.2% vs CSI300 -1.1%", "alpha": "4.3%"},
            "方向对了,动量论点成立;教训是止损位设太近。",
        )
        assert ok
        assert memory.pending_deployments() == []
        out = memory.recall("BTC/USDT")
        assert "+3.2%" in out and "动量论点成立" in out

    def test_backfill_unknown_id_returns_false(
        self, memory: DecisionOutcomeMemory
    ) -> None:
        assert memory.backfill("dep-x", {"summary": "y"}, "z") is False

    def test_backfill_twice_rejected(self, memory: DecisionOutcomeMemory) -> None:
        memory.record_pending("dep-1", "strat-1", ["BTC/USDT"], "2026-09-28")
        assert memory.backfill("dep-1", {"summary": "a"}, "r1") is True
        assert memory.backfill("dep-1", {"summary": "b"}, "r2") is False


class TestResolvedAtCapture:
    """[A] 捕获半:回填时记下**写反思的墙钟时刻**(resolved_at)。

    条目的 ``entry_date`` 是**做决定那天**,推不出"什么时候知道结局"——回填是
    手动、滞后触发的。不记这个时刻,一旦以后要做按历史时点重放的分析,今天之前
    的条目就无法按"当时已知"过滤了(时间戳被丢,只能猜)。故回填即落一个 UTC
    ISO8601 时间戳:这是防不可逆丢失,不是建功能(过滤侧仍无调用方)。

    ⚠️ 注意语义:这是**墙钟回填时刻**,不是 open-source-reference 的 ``resolution_date``
    (那记的是出场价格 bar 日期)。当前无出场日期来源,故该戳只作"结局已知日"的
    保守上界——过滤只会多排除、不会注入未来信息。"""

    def test_pending_entry_has_no_resolved_at(
        self, memory: DecisionOutcomeMemory
    ) -> None:
        memory.record_pending("dep-1", "strat-1", ["BTC/USDT"], "2026-09-28")
        # 未回填 = 结局未知,字段存在但为 None(诚实,不编造日期)
        assert memory._entries[0]["resolved_at"] is None

    def test_backfill_stamps_resolved_at(self, memory: DecisionOutcomeMemory) -> None:
        memory.record_pending("dep-1", "strat-1", ["BTC/USDT"], "2026-09-28")
        memory.backfill("dep-1", {"summary": "+2%"}, "教训")
        stamped = memory._entries[0]["resolved_at"]
        assert isinstance(stamped, str) and stamped  # 默认自动落时间戳

    def test_resolved_at_default_is_utc_aware(
        self, memory: DecisionOutcomeMemory
    ) -> None:
        from datetime import datetime

        memory.record_pending("dep-1", "strat-1", ["BTC/USDT"], "2026-09-28")
        memory.backfill("dep-1", {"summary": "+2%"}, "教训")
        parsed = datetime.fromisoformat(memory._entries[0]["resolved_at"])
        assert parsed.tzinfo is not None  # 带时区,跨时区可比,不歧义

    def test_resolved_at_explicit_value_respected(
        self, memory: DecisionOutcomeMemory
    ) -> None:
        memory.record_pending("dep-1", "strat-1", ["BTC/USDT"], "2026-09-28")
        memory.backfill(
            "dep-1",
            {"summary": "+2%"},
            "教训",
            resolved_at="2026-10-01T00:00:00+00:00",
        )
        assert memory._entries[0]["resolved_at"] == "2026-10-01T00:00:00+00:00"

    def test_resolved_at_survives_reload(self, tmp_path: Path) -> None:
        memory = DecisionOutcomeMemory(memory_dir=tmp_path)
        memory.record_pending("dep-1", "strat-1", ["BTC/USDT"], "2026-09-28")
        memory.backfill(
            "dep-1", {"summary": "+2%"}, "教训", resolved_at="2026-10-02T00:00:00+00:00"
        )
        fresh = DecisionOutcomeMemory(memory_dir=tmp_path)
        assert fresh._entries[0]["resolved_at"] == "2026-10-02T00:00:00+00:00"

    def test_legacy_entry_without_resolved_at_reads_as_none(
        self, memory: DecisionOutcomeMemory
    ) -> None:
        # 老条目没有该字段:读取返回 None(未知),recall 照常工作、不崩。
        memory._entries = [
            {
                "deployment_id": "dep-old",
                "strategy_id": "strat-1",
                "symbols": ["BTC/USDT"],
                "entry_date": "2026-09-01",
                "rating": "Hold",
                "status": "filled",
                "outcome_metrics": {"summary": "+1%"},
                "reflection_text": "旧教训",
            }
        ]
        assert memory._entries[0].get("resolved_at") is None
        assert "旧教训" in memory.recall("BTC/USDT")  # 不受影响


class TestConcurrentWriters:
    """N3:两个写者并发时跨进程锁 + 锁内重读,互不覆盖对方条目。

    三个调用点各自 ``new`` 一个 ``DecisionOutcomeMemory``,构造即快照。若写操作
    只锁 ``_persist`` 而不在锁内重读磁盘,后写者仍会用陈旧快照整file覆写,静默吞掉
    先写者的条目(丢更新)。此测试在两个句柄上交替写,断言两边条目都在。
    """

    def test_two_handles_both_survive(self, tmp_path: Path) -> None:
        a = DecisionOutcomeMemory(memory_dir=tmp_path)
        b = DecisionOutcomeMemory(memory_dir=tmp_path)  # 各自快照(此时为空)
        a.record_pending("dep-a", "s", ["BTC/USDT"], "2026-09-28")
        b.record_pending("dep-b", "s", ["ETH/USDT"], "2026-09-28")
        fresh = DecisionOutcomeMemory(memory_dir=tmp_path)
        assert set(fresh.pending_deployments()) == {"dep-a", "dep-b"}

    def test_late_handle_does_not_drop_earlier_backfill(self, tmp_path: Path) -> None:
        a = DecisionOutcomeMemory(memory_dir=tmp_path)
        a.record_pending("dep-1", "s", ["BTC/USDT"], "2026-09-28")
        stale = DecisionOutcomeMemory(memory_dir=tmp_path)  # 快照含 dep-1(pending)
        a.backfill("dep-1", {"summary": "+3%"}, "教训")  # 先回填
        # stale 句柄再写另一条:必须重读到 dep-1 已 filled,不能把回填抹回 pending。
        stale.record_pending("dep-2", "s", ["ETH/USDT"], "2026-09-29")
        fresh = DecisionOutcomeMemory(memory_dir=tmp_path)
        by_id = {e["deployment_id"]: e for e in fresh._entries}
        assert by_id["dep-1"]["status"] == "filled", "回填被陈旧快照覆写抹掉了"
        assert by_id["dep-1"]["reflection_text"] == "教训"
        assert "dep-2" in by_id

    def test_atomic_write_leaves_no_partial_file(self, tmp_path: Path) -> None:
        memory = DecisionOutcomeMemory(memory_dir=tmp_path)
        memory.record_pending("dep-1", "s", ["BTC/USDT"], "2026-09-28")
        # 落盘是完整合法 JSON;无残留 .tmp 文件。
        import json

        data = json.loads(
            (tmp_path / "decision_outcomes.json").read_text(encoding="utf-8")
        )
        assert data["entries"][0]["deployment_id"] == "dep-1"
        assert not list(tmp_path.glob("*.tmp-*"))


class TestRecall:
    def test_empty_query_returns_empty(self, memory: DecisionOutcomeMemory) -> None:
        memory.record_pending("dep-1", "strat-1", ["BTC/USDT"], "2026-09-28")
        assert memory.recall("") == ""

    def test_no_match_returns_empty(self, memory: DecisionOutcomeMemory) -> None:
        memory.record_pending("dep-1", "strat-1", ["BTC/USDT"], "2026-09-28")
        assert memory.recall("SOL/USDT momentum") == ""

    def test_same_symbol_full_entry(self, memory: DecisionOutcomeMemory) -> None:
        memory.record_pending("dep-1", "strat-1", ["BTC/USDT"], "2026-09-28")
        memory.backfill("dep-1", {"summary": "+2%"}, "教训文本A")
        out = memory.recall("BTC/USDT 涨了")
        assert "+2%" in out and "教训文本A" in out

    def test_cross_symbol_reflection_only(self, memory: DecisionOutcomeMemory) -> None:
        memory.record_pending("dep-1", "strat-1", ["ETH/USDT"], "2026-09-28")
        memory.backfill("dep-1", {"summary": "-1%"}, "跨票教训B")
        out = memory.recall("BTC/USDT momentum")
        # ETH 未命中 query,但已回填反思以跨票教训注入
        assert "跨票教训B" in out
        assert "-1%" not in out  # 不泄 outcome 全文

    def test_pending_entry_shows_pending_not_metrics(
        self, memory: DecisionOutcomeMemory
    ) -> None:
        memory.record_pending("dep-1", "strat-1", ["BTC/USDT"], "2026-09-28")
        out = memory.recall("BTC/USDT")
        assert "pending" in out

    def test_legacy_blank_rating_reads_as_review(
        self, memory: DecisionOutcomeMemory
    ) -> None:
        # 接线前落盘的旧记录 rating 为 "";读取时归一到 REVIEW,不渲染成 "rating=?"。
        memory._entries = [
            {
                "deployment_id": "dep-old",
                "strategy_id": "strat-1",
                "symbols": ["BTC/USDT"],
                "entry_date": "2026-09-01",
                "rating": "",
                "decision_text": "",
                "status": "pending",
                "outcome_metrics": None,
                "reflection_text": "",
            }
        ]
        out = memory.recall("BTC/USDT")
        assert f"rating={RATING_REVIEW}" in out
        assert "rating=?" not in out


class TestHumanViewEntry:
    """B2 端到端:REVIEW 哨兵必须在人类可读面显式标出,而非当普通评级渲染。

    解析侧已产出 REVIEW,但此前只在账本里存着,用户看不到"这条评级读不出、
    需要人看/重跑"。``human_view_entry`` 给每条记录补 ``is_review`` 布尔,
    并把空白 rating 归一到 REVIEW —— 让前端能把哨兵渲染成醒目警告。
    """

    def test_blank_rating_normalized_and_flagged_review(self) -> None:
        out = human_view_entry({"deployment_id": "d1", "rating": ""})
        assert out["rating"] == RATING_REVIEW
        assert out["is_review"] is True

    def test_review_sentinel_flagged(self) -> None:
        out = human_view_entry({"deployment_id": "d1", "rating": RATING_REVIEW})
        assert out["is_review"] is True

    def test_tradeable_rating_not_flagged(self) -> None:
        for rating in ("Buy", "Overweight", "Hold", "Underweight", "Sell"):
            out = human_view_entry({"deployment_id": "d1", "rating": rating})
            assert out["is_review"] is False
            assert out["rating"] == rating

    def test_missing_rating_key_flagged_review(self) -> None:
        out = human_view_entry({"deployment_id": "d1"})
        assert out["is_review"] is True
        assert out["rating"] == RATING_REVIEW

    def test_original_entry_not_mutated(self) -> None:
        entry = {"deployment_id": "d1", "rating": ""}
        human_view_entry(entry)
        assert entry["rating"] == ""  # 不可变:返回新 dict,不改原记录
        assert "is_review" not in entry

"""窗口重看检测(F3)—— 纯函数,不需要 DB。

吸收 external-research-project 的"看过即降级"纪律(SKILL.md:131):一旦读取某区间的
结果,该区间就不再是 untouched holdout。Selene 侧实测缺口:同
(probe, symbol, interval) 的两次运行窗口 99.8% 重叠而系统无提示。

本文件只锁 ``window_replay_overlap`` 的纯函数行为 —— 它**只算重叠度,
不做判断**(不阻断、不改阈值,与 F2 subperiod_sign_consistency 同纪律)。
"""

from __future__ import annotations

from selene_space_storage.repositories.research_graph._shared import (
    _parse_iso,
    window_replay_overlap,
)


def test_full_overlap_is_one() -> None:
    prior = [("2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00")]
    r = window_replay_overlap(
        prior, "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"
    )
    assert r == 1.0


def test_partial_overlap_ratio() -> None:
    """前窗 [01-01, 01-20] 与本次 [01-10, 01-30]:重叠 10 天 / 本次 20 天 = 0.5。"""
    prior = [("2026-01-01T00:00:00+00:00", "2026-01-20T00:00:00+00:00")]
    r = window_replay_overlap(
        prior, "2026-01-10T00:00:00+00:00", "2026-01-30T00:00:00+00:00"
    )
    assert r is not None
    assert abs(r - 0.5) < 1e-9


def test_picks_max_over_prior_windows() -> None:
    """多个历史窗口取最大重叠。"""
    prior = [
        ("2026-01-01T00:00:00+00:00", "2026-01-05T00:00:00+00:00"),  # 小
        ("2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"),  # 全覆盖
    ]
    r = window_replay_overlap(
        prior, "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"
    )
    assert r == 1.0


def test_no_overlap_returns_none() -> None:
    """无重叠 → None(不冒充 0.0 —— 0.0 会被读成"看过但没重叠")。"""
    prior = [("2026-01-01T00:00:00+00:00", "2026-01-10T00:00:00+00:00")]
    r = window_replay_overlap(
        prior, "2026-02-01T00:00:00+00:00", "2026-03-01T00:00:00+00:00"
    )
    assert r is None


def test_first_run_returns_none() -> None:
    """首次运行(无历史窗口)→ None。"""
    assert window_replay_overlap([], "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00") is None
    assert window_replay_overlap(None, "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00") is None


def test_degenerate_window_returns_none() -> None:
    """零长度窗口无"看过多少"可言 → None。"""
    prior = [("2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00")]
    assert window_replay_overlap(prior, "2026-01-15T00:00:00+00:00", "2026-01-15T00:00:00+00:00") is None
    # 反向区间同样退化
    assert window_replay_overlap(prior, "2026-02-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00") is None


def test_dirty_strings_fail_safe_not_raise() -> None:
    """脏数据 fail-safe 返回 None,绝不抛(防探针被账本里的坏数据炸掉)。"""
    assert window_replay_overlap(
        [("不是日期", "2026-02-01T00:00:00+00:00")],
        "2026-01-01T00:00:00+00:00",
        "2026-02-01T00:00:00+00:00",
    ) is None
    assert window_replay_overlap(
        [("2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00")],
        "not-a-date",
        "2026-02-01T00:00:00+00:00",
    ) is None
    assert window_replay_overlap(
        [None, ("x",), ("a", "b", "c")],  # 各种畸形 prior 项
        "2026-01-01T00:00:00+00:00",
        "2026-02-01T00:00:00+00:00",
    ) is None


def test_naive_iso_treated_as_utc() -> None:
    """无时区串按 UTC 处理(与仓内 _now() 的 UTC 约定一致)。"""
    r = window_replay_overlap(
        [("2026-01-01T00:00:00", "2026-02-01T00:00:00")],
        "2026-01-01T00:00:00",
        "2026-02-01T00:00:00",
    )
    assert r == 1.0


def test_parse_iso_rejects_non_strings() -> None:
    """_parse_iso 对 None/数字/空串返回 None。"""
    assert _parse_iso(None) is None
    assert _parse_iso("") is None
    assert _parse_iso(12345) is None
    assert _parse_iso({}) is None

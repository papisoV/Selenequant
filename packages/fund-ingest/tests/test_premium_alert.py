"""premium_alert 离线测试 — 阈值筛选 + 告警文案 + loop 单轮。"""

from __future__ import annotations

import asyncio

from selene_space_fund_ingest import premium_alert


def _rows() -> list[dict]:
    return [
        {
            "code": "160323",
            "name": "A基金",
            "premium_pct": 5.2,
            "nav_date": "2026-09-04",
        },
        {
            "code": "501025",
            "name": "B基金",
            "premium_pct": -6.8,
            "nav_date": "2026-09-04",
        },
        {
            "code": "161005",
            "name": "C基金",
            "premium_pct": 3.9,
            "nav_date": "2026-09-04",
        },  # 阈内
        {
            "code": "162411",
            "name": "D基金",
            "premium_pct": None,
            "nav_date": None,
        },  # 未配对排除
    ]


def test_breach_detection_positive_and_negative():
    breaches = premium_alert.find_premium_breaches(_rows(), threshold=4.0)
    assert {r["code"] for r in breaches} == {"160323", "501025"}  # abs() 判定


def test_no_breach_returns_empty():
    assert premium_alert.find_premium_breaches(_rows(), threshold=10.0) == []


def test_breach_excludes_unpaired_nav():
    breaches = premium_alert.find_premium_breaches(_rows(), threshold=4.0)
    assert all(r["premium_pct"] is not None for r in breaches)


def test_format_alert_includes_code_and_pct():
    text = premium_alert.format_alert(premium_alert.find_premium_breaches(_rows(), 4.0))
    assert "160323" in text
    assert "A基金" in text
    assert "5.20" in text  # {:+.2f}% 格式
    assert "nav 2026-09-04" in text


def test_alert_loop_single_round_dispatches(monkeypatch):
    """单轮 loop:超阈值行触发 _send_alert 后 sleep 被取消。"""
    sent: list[str] = []

    def _capture(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(premium_alert, "_send_alert", _capture)

    from selene_space_fund_ingest import store

    def _fake_rows(*a, **k):
        return _rows()

    monkeypatch.setattr(store, "fetch_all", _fake_rows)

    async def _run():
        task = asyncio.create_task(premium_alert.alert_loop(interval=999))
        await asyncio.sleep(0.1)  # 让第一轮跑完
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    assert len(sent) == 1
    assert "160323" in sent[0]


def test_alert_loop_round_failure_keeps_loop_alive(monkeypatch):
    """connect 抛异常:loop 不 crash,吞掉后继续 sleep。"""
    from selene_space_fund_ingest import store

    def _boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(store, "connect", _boom)
    monkeypatch.setattr(
        premium_alert,
        "_send_alert",
        lambda t: (_ for _ in ()).throw(AssertionError("should not dispatch")),
    )

    async def _run():
        task = asyncio.create_task(premium_alert.alert_loop(interval=999))
        await asyncio.sleep(0.1)
        assert not task.done()  # 异常被吞,loop 仍活
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())

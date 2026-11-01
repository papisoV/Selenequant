"""P4 tests: PA brief rendering (pure) + env-gated notification wiring.

render_pa_brief is pure — pins the format for full/trade/reject/next_period
records and robustness on malformed input. maybe_notify_pa env gate: off by
default, on sends through pulse (stubbed at the notify boundary).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    str(_ROOT / "scripts"),
    str(_ROOT / "packages" / "pulse"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pa_research.pa_notify import maybe_notify_pa, render_pa_brief  # noqa: E402


def _full_record() -> dict:
    return {
        "meta": {"input_id": "pa-1h-2026", "tree_id": "trading_decision"},
        "input_data": {
            "summary": {
                "symbol": "BTC/USDT",
                "interval": "1h",
                "last_close": 43210.5,
            }
        },
        "stage1": {
            "diagnosis_or_decision": {
                "state": "trend_up",
                "gate_result": "proceed",
            }
        },
        "stage2": {
            "diagnosis_or_decision": {
                "decision": "trade",
                "terminal_outcome": "trade",
                "order": {"direction": "long", "entry": 43200},
                "decision_summary": {
                    "diagnosis_summary": {"state": "trend_up"},
                    "next_period": {
                        "direction": "long",
                        "watch_levels": [43500, 42800],
                        "invalidate_if": "跌破 42800 收盘",
                    },
                },
            }
        },
    }


def test_render_full_record() -> None:
    brief = render_pa_brief(_full_record())
    assert "PA 诊断 BTC/USDT 1h" in brief
    assert "state=trend_up gate=proceed → trade (terminal trade)" in brief
    assert "last_close=43210.5" in brief
    assert "next_period direction=long" in brief
    assert "watch=[43500, 42800]" in brief
    assert "order=" in brief


def test_render_reject_record() -> None:
    rec = _full_record()
    rec["stage2"]["diagnosis_or_decision"] = {
        "decision": "reject",
        "terminal_outcome": "reject",
        "decision_summary": {"reject_reason": "样本不足"},
    }
    brief = render_pa_brief(rec)
    assert "→ reject (terminal reject)" in brief
    assert "reject_reason=样本不足" in brief
    assert "order=" not in brief
    assert "next_period" not in brief


def test_render_wait_record_no_next_period() -> None:
    rec = _full_record()
    rec["stage2"] = None
    brief = render_pa_brief(rec)
    assert "→ wait (terminal wait)" in brief
    assert "next_period" not in brief


def test_render_malformed_record_never_raises() -> None:
    assert render_pa_brief({}) != ""
    assert render_pa_brief({"meta": None, "stage1": None}) != ""


def test_maybe_notify_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SELENE_PA_NOTIFY_ENABLED", raising=False)
    assert maybe_notify_pa(_full_record()) is None


def test_maybe_notify_enabled_sends(monkeypatch) -> None:
    monkeypatch.setenv("SELENE_PA_NOTIFY_ENABLED", "1")
    sent: list[str] = []

    import selene_space_pulse.notify as notify_mod

    monkeypatch.setattr(
        notify_mod, "send_trade_alert", lambda text: sent.append(text) or ["ok"]
    )
    result = maybe_notify_pa(_full_record())
    assert result == ["ok"]
    assert len(sent) == 1
    assert "PA 诊断 BTC/USDT 1h" in sent[0]


def test_maybe_notify_swallows_errors(monkeypatch) -> None:
    monkeypatch.setenv("SELENE_PA_NOTIFY_ENABLED", "1")

    import selene_space_pulse.notify as notify_mod

    def _boom(_text: str) -> list[str]:
        raise RuntimeError("network down")

    monkeypatch.setattr(notify_mod, "send_trade_alert", _boom)
    assert maybe_notify_pa(_full_record()) is None

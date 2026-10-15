"""Honesty lock for the backfill reflection prompt (N2).

The reflection prompt asks the LLM to write a lesson from a past decision's
outcome. The failure this pins: the prompt demanded "cite the alpha figure",
but ``_collect_outcome`` supplies no return/alpha/pnl at all — so the model
either fabricates a number or the prompt lies about what it can see. That
fabrication then gets stored in the decision log and re-read by future
analyses (the "negative-asset injection" the ledger exists to prevent).

The fix is honest degradation: when no realized-return figure is available,
say so and forbid estimating — never invite a number the system cannot supply.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MOD = Path(__file__).resolve().parent.parent / "scripts" / "backfill_outcomes.py"
_spec = importlib.util.spec_from_file_location("backfill_outcomes", _MOD)
bo = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(bo)


def test_prompt_does_not_demand_a_figure_the_system_cannot_supply() -> None:
    """The old prompt said 'cite the alpha figure'; that must be gone."""
    lowered = bo._REFLECTION_PROMPT.lower()
    assert "cite the alpha figure" not in lowered


def test_prompt_forbids_estimating_outcome_numbers() -> None:
    """The prompt must tell the model not to invent numbers it wasn't given."""
    lowered = bo._REFLECTION_PROMPT.lower()
    assert (
        "do not estimate" in lowered
        or "do not fabricate" in lowered
        or "不得" in bo._REFLECTION_PROMPT
    )


def test_prompt_still_asks_for_the_direction_check() -> None:
    """Core value kept: still ask whether the directional call was right."""
    lowered = bo._REFLECTION_PROMPT.lower()
    assert "directional call correct" in lowered or "方向" in bo._REFLECTION_PROMPT


# --------------------------------------------------------------------------
# N6: horizon honesty — a short window cannot disprove a long-horizon thesis.
# --------------------------------------------------------------------------


def test_prompt_names_the_evaluation_window_days() -> None:
    """The prompt must name the window length so a 5-day outcome is not read as
    a verdict on a thesis written for months. The window comes from
    SELENE_BACKFILL_DAYS; the prompt itself must speak of the horizon."""
    lowered = bo._REFLECTION_PROMPT.lower()
    assert (
        "window" in lowered or "horizon" in lowered or "窗口" in bo._REFLECTION_PROMPT
    )


def test_prompt_warns_short_window_cannot_judge_long_thesis() -> None:
    """Honest degradation: say plainly when the window is too short to judge the
    thesis, rather than treating a short move as its disproof."""
    lowered = bo._REFLECTION_PROMPT.lower()
    assert "too short" in lowered or "太短" in bo._REFLECTION_PROMPT


def test_user_content_names_the_window_length() -> None:
    """The number of days must reach the model, not just a generic 'window'."""
    captured: dict[str, str] = {}

    class _Capture:
        async def chat(self, messages):  # noqa: ANN001
            captured["user"] = messages[-1].content
            return "ok"

    import asyncio

    asyncio.run(bo._reflect(_Capture(), "thesis", {"summary": "s"}))
    # 默认窗口 5 天须出现在喂给模型的文本里(而不是只在 prompt 里说"窗口")。
    assert "5" in captured["user"]


# --------------------------------------------------------------------------
# _collect_outcome: must not claim a return it does not have.
# --------------------------------------------------------------------------


def test_collect_outcome_marks_return_unavailable_when_absent() -> None:
    """No deploy record -> the outcome dict must say the figure is unavailable,
    not silently omit the key (an omitted key reads as 'zero' downstream)."""
    out = bo._collect_outcome({"deployment_id": "d1"}, None)
    assert out.get("return_available") is False
    assert out.get("alpha_available") is False


def test_collect_outcome_never_invents_a_return() -> None:
    """A slot_status snapshot carries no PnL today; the collector must not
    manufacture a 0.0 that would read as 'flat'."""
    out = bo._collect_outcome(
        {"deployment_id": "d1"},
        {"slot_status": {"signals_emitted": 3, "orders_placed": 2, "errors": 0}},
    )
    for key in ("alpha_return", "raw_return", "pnl"):
        assert out.get(key) is None or key not in out
    assert out.get("return_available") is False

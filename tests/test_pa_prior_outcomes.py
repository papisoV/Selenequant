"""W-A — settled prior outcomes feed back as a precedent block.

The plan (`internal-plan`)
closes the continuous-analysis loop: ``pa_outcome`` writes a settled outcome
onto each record, and the next diagnosis on the same symbol+interval reads
those outcomes as ``prior_outcomes`` — the "what happened last time" the model
never previously saw.

Guarantees pinned here:

- only settled outcomes (win/loss/expired/timeout_open) qualify; open/no
  outcome contributes nothing (T1)
- interval is matched (the repo filters by symbol only) and symbol mismatch is
  dropped (T2)
- the block is *absent* from the enriched input when empty, present when
  non-empty — an empty block never dilutes the prompt (T3)
- the block is a list[dict] of {decided_at, decision, direction, outcome, pnl_r}
  and the input dict is not mutated (T4)
- a global rule describing prior_outcomes is present in build_global_rules (T5)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for _p in [
    REPO / "packages" / "decision-workflow",
    REPO / "services" / "local-api",
]:
    sys.path.insert(0, str(_p))

from app.core.trading_input import build_prior_outcome_block  # noqa: E402
from selene_space_decision_workflow.trading_pipeline import (  # noqa: E402
    build_global_rules,
    enrich_input_data,
)

_SYMBOL = "BTC/USDT"
_INTERVAL = "1h"


def _bars(n: int = 120) -> list[dict]:
    step = 3_600_000
    return [
        {
            "ts": 1_700_000_000_000 + i * step,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1.0,
        }
        for i in range(n)
    ]


def _record(
    *,
    symbol: str = _SYMBOL,
    interval: str = _INTERVAL,
    status: str | None = "win",
    decision: str = "limit",
    direction: str = "long",
    pnl_r: float | None = 2.0,
) -> dict:
    rec: dict = {
        "meta": {"input_id": "x", "timestamp": "2026-01-01T00:00:00+00:00"},
        "input_data": {"summary": {"symbol": symbol, "interval": interval}},
        "stage2": {
            "diagnosis_or_decision": {
                "decision": decision,
                "order": {"direction": direction},
            }
        },
    }
    if status is not None:
        rec["outcome"] = {"status": status, "pnl_r": pnl_r}
    return rec


@pytest.fixture(autouse=True)
def _no_experiences(monkeypatch):
    import selene_space_decision_workflow.trading_pipeline as tp

    monkeypatch.setattr(tp, "load_experiences", lambda *a, **k: [])


# ------------------------------------------------------------ T1 settled only


@pytest.mark.parametrize(
    "status,expected",
    [
        ("win", 1),
        ("loss", 1),
        ("expired", 1),
        ("timeout_open", 1),
        ("still_open", 0),
        (None, 0),
    ],
)
def test_T1_only_settled_outcomes_qualify(status, expected) -> None:
    block = build_prior_outcome_block([_record(status=status)], _SYMBOL, _INTERVAL)
    assert len(block) == expected


# ------------------------------------------------------------- T2 matching


def test_T2_symbol_and_interval_are_matched() -> None:
    records = [
        _record(symbol="ETH/USDT"),  # wrong symbol
        _record(interval="4h"),  # wrong interval
        _record(),  # matches
    ]
    block = build_prior_outcome_block(records, _SYMBOL, _INTERVAL)
    assert len(block) == 1
    assert block[0]["outcome"] == "win"


# ---------------------------------------------------- T3 presence / absence


def test_T3_absent_when_empty_present_when_not() -> None:
    base = {"symbol": _SYMBOL, "interval": _INTERVAL}
    empty, _ = enrich_input_data(dict(base), _bars(), _SYMBOL, _INTERVAL)
    assert "prior_outcomes" not in empty

    block = build_prior_outcome_block([_record()], _SYMBOL, _INTERVAL)
    filled, _ = enrich_input_data(
        dict(base), _bars(), _SYMBOL, _INTERVAL, prior_outcomes=block
    )
    assert filled["prior_outcomes"] == block


# -------------------------------------------------------------- T4 shape


def test_T4_block_shape_and_no_mutation() -> None:
    rec = _record(status="win", direction="long", pnl_r=2.0)
    block = build_prior_outcome_block([rec], _SYMBOL, _INTERVAL)
    assert block == [
        {
            "decided_at": "2026-01-01T00:00:00+00:00",
            "decision": "limit",
            "direction": "long",
            "outcome": "win",
            "pnl_r": 2.0,
        }
    ]
    # enrich must not mutate the caller's input dict
    base = {"symbol": _SYMBOL, "interval": _INTERVAL}
    snapshot = dict(base)
    enrich_input_data(base, _bars(), _SYMBOL, _INTERVAL, prior_outcomes=block)
    assert base == snapshot


# -------------------------------------------------------------- T5 rule


def test_T5_global_rule_describes_prior_outcomes() -> None:
    rules, _ = build_global_rules(_SYMBOL, _INTERVAL)
    assert any("prior_outcomes" in r for r in rules)

"""Item 1 + item 6 — structure features into Path B, numeric R:R gate.

The plan (`internal-plan`)
injects market-analysis's deterministic ``compute_structure_features`` into the
decision-workflow (Path B) ``input_data`` at the single ``enrich_input_data``
seam shared by all three writer arms (endpoint / heartbeat / CLI), and adds a
numeric R:R hard gate to the DW validator mirroring market-analysis.

Guarantees pinned here:

- the list[dict] -> DataFrame helper keeps only OHLC (T1)
- a thick window injects all 38 features and does not mutate the input (T2)
- an empty window leaves the block *absent* (T3)
- the R:R gate is opt-in, recomputes from prices, and is off by default (T4)
- same-window Path A (direct call) and Path B (enrich output) are value-equal,
  AND the cross-window count features genuinely differ — the window fact is
  pinned so nobody "fixes" it as a bug (T5)
- the Stage-2 system prompt is byte-unchanged (T6)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

import pytest

REPO = Path(__file__).resolve().parents[1]
for _p in [
    REPO / "packages" / "decision-workflow",
    REPO / "packages" / "shared-types",
    REPO / "packages" / "core",
    REPO / "packages" / "market-analysis",
    REPO / "services" / "local-api",
]:
    sys.path.insert(0, str(_p))

from selene_space_decision_workflow.models import (  # noqa: E402
    Stage2Result,
)
from selene_space_decision_workflow.trading_pipeline import (  # noqa: E402
    _bars_to_dataframe,
    build_full_input_data,
    enrich_input_data,
)
from selene_space_decision_workflow.validator import (  # noqa: E402
    ValidatorConfig,
    _check_risk_reward,
)
from selene_space_market_analysis.structure_features import (  # noqa: E402
    STRUCTURE_FEATURE_KEYS,
    compute_structure_features,
)

_SYMBOL = "BTC/USDT"
_INTERVAL = "1h"


def _bars(closes: list[float], *, base_ts: int = 1_700_000_000_000) -> list[dict]:
    step = 3_600_000
    return [
        {
            "ts": base_ts + i * step,
            "open": c,
            "high": c + 1.0,
            "low": c - 1.0,
            "close": c,
            "volume": 1.0,
        }
        for i, c in enumerate(closes)
    ]


@pytest.fixture(autouse=True)
def _no_experiences(monkeypatch):
    """Isolate enrich_input_data's filesystem read (project_experiences)."""
    import selene_space_decision_workflow.trading_pipeline as tp

    monkeypatch.setattr(tp, "load_experiences", lambda *a, **k: [])


# ------------------------------------------------------------------ T1 helper


def test_T1_bars_to_dataframe_ohlc_only() -> None:
    df = _bars_to_dataframe(_bars([1.0, 2.0, 3.0]))
    assert list(df.columns) == ["open", "high", "low", "close"]
    assert len(df) == 3
    # ts/volume are dropped — the structure layer has no volume key.
    assert "ts" not in df.columns and "volume" not in df.columns


def test_T1_empty_list_yields_empty_features() -> None:
    df = _bars_to_dataframe([])
    assert compute_structure_features(df) == {}


def test_T1_missing_column_yields_empty_features() -> None:
    import pandas as pd

    df = pd.DataFrame([(1.0, 2.0)], columns=["open", "high"])
    assert compute_structure_features(df) == {}


# --------------------------------------------------------------- T2 injection


def test_T2_thick_window_injects_all_features() -> None:
    all_bars = _bars([100.0 + (i % 7) * 0.5 for i in range(120)])
    input_data = {"symbol": _SYMBOL, "interval": _INTERVAL}
    enriched, overlays_hit = enrich_input_data(input_data, all_bars, _SYMBOL, _INTERVAL)
    assert "structure_features" in enriched
    assert set(STRUCTURE_FEATURE_KEYS).issubset(enriched["structure_features"])
    # Return shape unchanged; input dict not mutated.
    assert isinstance(overlays_hit, dict)
    assert "structure_features" not in input_data


def test_T2_feature_values_match_direct_call() -> None:
    """Same window -> enrich output equals the pure function's own output."""
    all_bars = _bars([100.0 + (i % 5) * 0.3 for i in range(60)])
    enriched, _ = enrich_input_data(
        {"symbol": _SYMBOL, "interval": _INTERVAL}, all_bars, _SYMBOL, _INTERVAL
    )
    direct = compute_structure_features(_bars_to_dataframe(all_bars))
    assert enriched["structure_features"] == direct


# ------------------------------------------------------------- T3 empty window


def test_T3_empty_features_leave_block_absent(monkeypatch) -> None:
    """When the structure function yields {}, the key is left absent (no empty
    block diluting the prompt). Forced via the function itself: a real >=50-bar
    window always yields features, and enrich_input_data requires >=50 bars
    (four_windows guard), so the empty branch is reachable here only by forcing
    the pure function's output."""
    import selene_space_decision_workflow.trading_pipeline as tp
    import selene_space_market_analysis.structure_features as sf

    monkeypatch.setattr(sf, "compute_structure_features", lambda df: {})
    all_bars = _bars([100.0 + (i % 7) * 0.5 for i in range(60)])
    enriched, _ = tp.enrich_input_data(
        {"symbol": _SYMBOL, "interval": _INTERVAL}, all_bars, _SYMBOL, _INTERVAL
    )
    assert "structure_features" not in enriched


# --------------------------------------------------------------- T4 R:R gate


def _s2(
    order: dict | None,
    outcome: Literal["trade", "wait", "reject", "proceed"] = "trade",
) -> Stage2Result:
    return Stage2Result(
        decision="wait",
        terminal_outcome=outcome,
        order=order,
        trace=[],
        decision_summary={},
    )


def test_T4_gate_off_by_default() -> None:
    """Default config (min_risk_reward=None) never fires — behaviour unchanged."""
    errors: list = []
    _check_risk_reward(
        _s2({"entry": 100.0, "stop_loss": 99.0, "take_profit": 100.1}),
        ValidatorConfig(),
        errors,
    )
    assert errors == []


def test_T4_gate_blocks_low_ratio() -> None:
    errors: list = []
    _check_risk_reward(
        _s2({"entry": 100.0, "stop_loss": 99.0, "take_profit": 100.5}),
        ValidatorConfig(min_risk_reward=1.0),
        errors,
    )
    assert len(errors) == 1
    assert errors[0].layer == "consistency"
    assert errors[0].field == "order.risk_reward_ratio"
    assert errors[0].cheat_detected is False


def test_T4_gate_passes_at_or_above_threshold() -> None:
    errors: list = []
    _check_risk_reward(
        _s2({"entry": 100.0, "stop_loss": 99.0, "take_profit": 101.0}),
        ValidatorConfig(min_risk_reward=1.0),
        errors,
    )
    assert errors == []


def test_T4_zero_risk_flagged() -> None:
    errors: list = []
    _check_risk_reward(
        _s2({"entry": 100.0, "stop_loss": 100.0, "take_profit": 105.0}),
        ValidatorConfig(min_risk_reward=1.0),
        errors,
    )
    assert len(errors) == 1
    assert errors[0].field == "order.stop_loss"


def test_T4_price_order_checked_per_side() -> None:
    errors: list = []
    # long with stop above entry is inverted.
    _check_risk_reward(
        _s2(
            {
                "direction": "long",
                "entry": 100.0,
                "stop_loss": 101.0,
                "take_profit": 105.0,
            }
        ),
        ValidatorConfig(min_risk_reward=1.0),
        errors,
    )
    assert any(e.field == "order.price_order" for e in errors)


def test_T4_incomplete_order_skips() -> None:
    errors: list = []
    _check_risk_reward(
        _s2({"entry": 100.0}), ValidatorConfig(min_risk_reward=1.0), errors
    )
    assert errors == []


# --------------------------------------------------------- T5 cross-pipeline


def test_T5_same_window_same_values() -> None:
    """Same fixture window: Path A call == Path B enrich output, all 38 keys."""
    all_bars = _bars([100.0 + ((i * 13) % 11) * 0.4 for i in range(120)])
    path_b, _ = build_full_input_data(all_bars, _SYMBOL, _INTERVAL)
    path_a = compute_structure_features(_bars_to_dataframe(all_bars))
    for key in STRUCTURE_FEATURE_KEYS:
        assert path_b["structure_features"][key] == path_a[key], key
        assert path_b["structure_features"][key] is not None


def test_T5_cross_window_counts_differ() -> None:
    """Pin the window fact: count features scale with the window, so 500-bar
    (Path A production) and 120-bar (Path B production) counts differ. This is
    by design, not a bug — the comparable subset is the window-invariant
    (ratio/position/last-value) features."""
    import pandas as pd

    # Directional bars (open != close) so bullish_count is meaningful.
    def _dir_df(n: int) -> pd.DataFrame:
        rows = []
        for i in range(n):
            o = 100.0 + i * 0.1
            c = o + (0.5 if i % 3 else -0.5)
            rows.append((o, max(o, c) + 0.2, min(o, c) - 0.2, c))
        return pd.DataFrame(rows, columns=["open", "high", "low", "close"])

    f500 = compute_structure_features(_dir_df(500))
    f120 = compute_structure_features(_dir_df(120))
    # The window-invariant last-value feature agrees.
    assert f500["body_ratio_last"] == f120["body_ratio_last"]
    # A count feature does not (window scale) — pinned so it is never "fixed".
    assert f500["bar_count"] == 500 and f120["bar_count"] == 120
    assert f500["bullish_count"] != f120["bullish_count"]


# ------------------------------------------- T5b genuine cross-pipeline parity


def _write_klines_db(
    path: Path, bars: list[dict], *, symbol: str, interval: str
) -> None:
    """Materialise fixture bars into a production-schema klines.db so both
    paths' *real* loaders (``KlineSource.load`` / ``load_bars``) can read the
    same rows. Inlined DDL mirrors ``market-data/.../kline_db.py`` (importing it
    drags an unrelated exchange dependency); kept identical to the columns both
    loaders SELECT."""
    import sqlite3

    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE klines ("
            " exchange TEXT NOT NULL, market_type TEXT NOT NULL,"
            " symbol TEXT NOT NULL, interval TEXT NOT NULL,"
            " timestamp INTEGER NOT NULL, open REAL, high REAL, low REAL,"
            " close REAL, volume REAL, closed INTEGER DEFAULT 0)"
        )
        conn.executemany(
            "INSERT INTO klines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "binance",
                    "futures",
                    symbol,
                    interval,
                    b["ts"],
                    b["open"],
                    b["high"],
                    b["low"],
                    b["close"],
                    b["volume"],
                    1,
                )
                for b in bars
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_T5b_real_loaders_cross_pipeline_parity(tmp_path: Path) -> None:
    """Genuine cross-pipeline parity through each path's *actual* loader.

    T5 above only proves Path B matches the pure function on Path B's own frame;
    it re-calls ``compute_structure_features`` for the "Path A" side, so the
    loader seam — the one place the two pipelines can diverge (selected column
    set, numeric coercion, row ordering) — is never exercised. Here one
    klines.db is read by both production loaders at the *same* 120-bar window:

    - Path A: ``KlineSource.load`` (SQL + ``pd.to_numeric``) -> structure dict
    - Path B: ``load_bars`` (row tuples -> ``float()``) -> ``enrich_input_data``

    and every one of the 38 features must be value-equal. A coercion or ordering
    drift between the two loaders would fail here and nowhere else.
    """
    from selene_space_decision_workflow.trading_pipeline import load_bars
    from selene_space_market_analysis.data_source import KlineSource

    db = tmp_path / "klines.db"
    _write_klines_db(
        db,
        _bars([100.0 + ((i * 13) % 11) * 0.4 for i in range(120)]),
        symbol=_SYMBOL,
        interval=_INTERVAL,
    )

    # Path A arm — market-analysis' loader (SQL read + pd.to_numeric coercion).
    df_a = KlineSource(db).load("binance", "futures", _SYMBOL, _INTERVAL, limit=120)
    features_a = compute_structure_features(df_a)

    # Path B arm — decision-workflow's loader (row tuples -> float()).
    all_bars = load_bars(db, _SYMBOL, _INTERVAL, 120)
    enriched, _ = build_full_input_data(all_bars, _SYMBOL, _INTERVAL)
    features_b = enriched["structure_features"]

    assert set(STRUCTURE_FEATURE_KEYS).issubset(features_a)
    assert set(STRUCTURE_FEATURE_KEYS).issubset(features_b)
    for key in STRUCTURE_FEATURE_KEYS:
        assert features_a[key] == features_b[key], key


# ------------------------------------------------------------ T6 KV-cache


def test_T6_stage2_system_prompt_unchanged() -> None:
    """The injected block reaches only the Stage-1 user material; the Stage-2
    system prompt bytes must not move (KV-cache constraint)."""
    from selene_space_decision_workflow.llm_provider import (
        StubLLMProvider,
        build_system_prompt,
    )
    from selene_space_decision_workflow.persistence import InMemoryPersistence
    from selene_space_decision_workflow.trading_pipeline import (
        build_trading_workflow,
    )

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        wf, _meta = build_trading_workflow(
            llm=StubLLMProvider([]),
            persistence=InMemoryPersistence(),
            symbol=_SYMBOL,
            interval=_INTERVAL,
            results_dir=Path(td),
        )
        before = wf._system_prompt
        # Recomputing the documented bytes from the same inputs is stable and
        # contains no structure-feature material.
        assert "structure_features" not in before
        assert before == build_system_prompt(wf.tree_text, wf.global_rules)

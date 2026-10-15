"""Behaviour locks for intelligence layer: PnL attribution, trading memory,
and user preferences persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from selene_space_agent.attribution.engine import PnLAttributionEngine
from selene_space_agent.memory.preferences import (
    RiskAppetite,
    UserPreferences,
    load_preferences,
    save_preferences,
    update_preference,
)
from selene_space_agent.memory.trading_memory import TradingMemory
from selene_space_types.agent import ChatMessage

# ---------------------------------------------------------------------------
# Sample trade ledger (2 strategies x 2 trades each)
# ---------------------------------------------------------------------------


def _trade(strategy: str, pnl: float, *, entry_px: float, exit_px: float, qty: float,
           side: str, symbol: str, entry_ms: int, commission: float, slip: float, fee: float) -> dict:
    return {
        "strategy_id": strategy,
        "pnl": pnl,
        "entry_time": entry_ms,
        "exit_time": entry_ms + 86_400_000,
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "entry_price": entry_px,
        "exit_price": exit_px,
        "commission": commission,
        "slippage": slip,
        "fees": fee,
    }


_LEDGER = [
    _trade("alpha", 500.0, entry_px=42000.0, exit_px=43000.0, qty=0.5, side="buy",
           symbol="BTC/USDT", entry_ms=1_704_067_200_000, commission=10.0, slip=5.0, fee=2.0),
    _trade("alpha", -200.0, entry_px=2200.0, exit_px=2100.0, qty=2.0, side="buy",
           symbol="ETH/USDT", entry_ms=1_704_240_000_000, commission=8.0, slip=3.0, fee=1.5),
    _trade("beta", 800.0, entry_px=44000.0, exit_px=43200.0, qty=1.0, side="sell",
           symbol="BTC/USDT", entry_ms=1_704_412_800_000, commission=12.0, slip=4.0, fee=2.5),
    _trade("beta", -100.0, entry_px=2150.0, exit_px=2183.33, qty=3.0, side="sell",
           symbol="ETH/USDT", entry_ms=1_704_585_600_000, commission=6.0, slip=2.0, fee=1.0),
]


class TestPnLAttribution:
    def test_totals_and_per_strategy_split(self) -> None:
        got = PnLAttributionEngine().explain(_LEDGER)

        checks = (
            got.total_pnl == pytest.approx(1000.0),
            set(got.by_strategy) == {"alpha", "beta"},
            got.by_strategy["alpha"] == pytest.approx(300.0),
            got.by_strategy["beta"] == pytest.approx(700.0),
        )
        assert all(checks)

    def test_period_bucketing_present(self) -> None:
        # all trades fall in ISO week 2024-W01
        assert len(PnLAttributionEngine().explain(_LEDGER).by_period) >= 1

    def test_win_and_loss_buckets_both_populated(self) -> None:
        rows = PnLAttributionEngine().explain(_LEDGER).by_event
        winners = [r for r in rows if r["type"] == "win"]
        losers = [r for r in rows if r["type"] == "loss"]
        checks = (len(winners) >= 1, len(losers) >= 1)
        assert all(checks)

    def test_cost_breakdown_sums(self) -> None:
        costs = PnLAttributionEngine().explain(_LEDGER).by_cost
        triple = (costs["commissions"], costs["slippage"], costs["fees"])
        assert triple == (pytest.approx(36.0), pytest.approx(14.0), pytest.approx(7.0))

    def test_empty_ledger_degenerates(self) -> None:
        got = PnLAttributionEngine().explain([])
        pair = (got.total_pnl, got.by_strategy)
        assert pair == (0.0, {})

    def test_window_filter_narrows_trades(self) -> None:
        # window covers only the first two ledger entries
        got = PnLAttributionEngine().explain(_LEDGER, period=("2024-01-01", "2024-01-04"))
        assert got.total_pnl == pytest.approx(300.0)

    def test_narrative_names_strategies_and_costs(self) -> None:
        engine = PnLAttributionEngine()
        text = engine.generate_narrative(engine.explain(_LEDGER))
        quad = ("PnL Report" in text, "alpha" in text, "beta" in text, "Costs:" in text)
        assert all(quad)

    def test_period_bounds_recorded(self) -> None:
        got = PnLAttributionEngine().explain(_LEDGER)
        checks = (got.period_start != "", got.period_end != "")
        assert all(checks)


class TestTradingMemory:
    def test_save_then_list_roundtrip(self, tmp_path: Path) -> None:
        mem = TradingMemory(memory_dir=tmp_path)
        thread = [
            ChatMessage(role="user", content="Review SOL/USDT setup", timestamp=1_700_000_000_000),
            ChatMessage(role="assistant", content="SOL momentum is fading", timestamp=1_700_000_001_000),
        ]
        mem.save_context("thread-9", thread, summary="SOL review")

        rows = mem.list_sessions()
        pair = (len(rows), (rows[0]["session_id"], rows[0]["summary"]))
        assert pair == (1, ("thread-9", "SOL review"))

    def test_summary_auto_derived_from_first_message(self, tmp_path: Path) -> None:
        mem = TradingMemory(memory_dir=tmp_path)
        mem.save_context(
            "thread-10",
            [ChatMessage(role="user", content="Check DOT price", timestamp=1_700_000_000_000)],
        )
        assert "Check DOT price" in mem.list_sessions()[0]["summary"]

    def test_symbol_recall_matches_and_misses(self, tmp_path: Path) -> None:
        mem = TradingMemory(memory_dir=tmp_path)
        mem.save_context(
            "thread-11",
            [ChatMessage(role="user", content="Analyze BTC/USDT trend", timestamp=1_700_000_000_000)],
            summary="BTC/USDT trend analysis",
        )

        hit = mem.recall_for_prompt({"symbols": ["BTC/USDT"]})
        miss = mem.recall_for_prompt({"symbols": ["AVAX/USDT"]})
        checks = ("BTC/USDT" in hit, miss == "")
        assert all(checks)

    def test_recall_on_empty_memory_is_blank(self, tmp_path: Path) -> None:
        mem = TradingMemory(memory_dir=tmp_path)
        assert mem.recall_for_prompt({"symbols": ["BTC/USDT"]}) == ""

    def test_clear_wipes_sessions(self, tmp_path: Path) -> None:
        mem = TradingMemory(memory_dir=tmp_path)
        filler = [ChatMessage(role="user", content="x", timestamp=1_700_000_000_000)]
        mem.save_context("t-a", filler)
        mem.save_context("t-b", filler)
        assert len(mem.list_sessions()) == 2

        mem.clear()
        assert mem.list_sessions() == []


class TestUserPreferences:
    def test_factory_defaults(self) -> None:
        prefs = UserPreferences()
        quintet = (
            prefs.risk_appetite,
            prefs.preferred_symbols,
            prefs.preferred_timeframes,
            prefs.preferred_strategy_types,
        )
        assert quintet == (RiskAppetite.MODERATE, [], [], [])

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        store = tmp_path / "prefs.json"
        save_preferences(
            UserPreferences(
                risk_appetite=RiskAppetite.AGGRESSIVE,
                preferred_symbols=["BTC/USDT", "ETH/USDT"],
                preferred_timeframes=["1h", "4h"],
                preferred_strategy_types=["sma_cross"],
            ),
            store,
        )

        loaded = load_preferences(store)
        trio = (
            loaded.risk_appetite,
            loaded.preferred_symbols,
            loaded.preferred_timeframes,
        )
        assert trio == (RiskAppetite.AGGRESSIVE, ["BTC/USDT", "ETH/USDT"], ["1h", "4h"])

    def test_missing_file_yields_defaults(self, tmp_path: Path) -> None:
        prefs = load_preferences(tmp_path / "absent.json")
        assert prefs.risk_appetite == RiskAppetite.MODERATE

    def test_scalar_update_persists(self, tmp_path: Path) -> None:
        store = tmp_path / "prefs.json"
        save_preferences(UserPreferences(), store)

        flipped = update_preference("risk_appetite", RiskAppetite.CONSERVATIVE, store)
        reloaded = load_preferences(store)
        pair = (flipped.risk_appetite, reloaded.risk_appetite)
        assert pair == (RiskAppetite.CONSERVATIVE, RiskAppetite.CONSERVATIVE)

    def test_list_update(self, tmp_path: Path) -> None:
        store = tmp_path / "prefs.json"
        save_preferences(UserPreferences(), store)

        changed = update_preference("preferred_symbols", ["LINK/USDT"], store)
        assert changed.preferred_symbols == ["LINK/USDT"]

    def test_unknown_key_rejected(self, tmp_path: Path) -> None:
        store = tmp_path / "prefs.json"
        save_preferences(UserPreferences(), store)

        with pytest.raises(ValueError, match="Unknown preference"):
            update_preference("no_such_key", "v", store)

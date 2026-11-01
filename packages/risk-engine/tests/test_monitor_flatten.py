"""T2: ContinuousRiskMonitor — flatten fire sites, opt-out, daily loss fuse, loop survival."""

from __future__ import annotations

import asyncio

from selene_space_risk.flatten_hook import FlattenHook, FlattenReport
from selene_space_risk.monitor import ContinuousRiskMonitor, RiskSnapshot


class _RecordingHook:
    """Duck-typed hook recording fire reasons."""

    def __init__(self) -> None:
        self.fires: list[str] = []

    async def fire(self, strategy_id=None, *, reason="") -> FlattenReport:
        self.fires.append(reason)
        return FlattenReport(n_fired=1, n_failed=0)

    def fire_and_forget(self, strategy_id=None, *, reason="") -> None:
        self.fires.append(f"[forget] {reason}")


class _KillSwitch:
    def __init__(self) -> None:
        self.activations: list[tuple[str, str]] = []

    @property
    def is_active(self) -> bool:
        return False

    def auto_activate(self, reason, *, triggered_by_rule="", strategy_id=None) -> None:
        self.activations.append((reason, triggered_by_rule))


class _FillsLedger:
    def __init__(self, daily_pnl: float) -> None:
        self._pnl = daily_pnl

    def get_daily_realized_pnl(self, account_id, today_utc) -> float:
        return self._pnl


class _Monitor(ContinuousRiskMonitor):
    """Subclass that drives a fixed snapshot without engine machinery."""

    def __init__(self, snapshot: RiskSnapshot, **kwargs) -> None:
        super().__init__(**kwargs)
        self._snapshot = snapshot

    def _compute_snapshot(self) -> RiskSnapshot:
        return self._snapshot


def _drawdown_snapshot() -> RiskSnapshot:
    # peak pre-set to 20000 → total 10000 = 50% drawdown (>= 15% critical).
    return RiskSnapshot(total_equity=10000.0, closest_liquidation_pct=100.0)


def test_critical_drawdown_fires_flatten_and_kill() -> None:
    async def _run() -> None:
        hook = _RecordingHook()
        ks = _KillSwitch()
        mon = _Monitor(
            _drawdown_snapshot(),
            risk_engine=None,
            kill_switch=ks,
            flatten_hook=hook,
        )
        mon._peak_equity = 20000.0
        await mon._check_cycle()
        assert any("Drawdown" in r for r in hook.fires)
        assert ks.activations  # critical kill fired too

    asyncio.run(_run())


def test_liquidation_proximity_fires_flatten_only() -> None:
    """#8 exception: liquidation proximity flattens without a kill."""

    async def _run() -> None:
        hook = _RecordingHook()
        ks = _KillSwitch()
        mon = _Monitor(
            RiskSnapshot(total_equity=10000.0, closest_liquidation_pct=2.0),
            risk_engine=None,
            kill_switch=ks,
            flatten_hook=hook,
        )
        await mon._check_cycle()
        assert any("liquidation" in r for r in hook.fires)
        assert not ks.activations  # warning — no kill

    asyncio.run(_run())


def test_liquidation_flatten_opt_out() -> None:
    """flatten_on_liquidation=False suppresses the proximity flatten."""

    async def _run() -> None:
        hook = _RecordingHook()
        mon = _Monitor(
            RiskSnapshot(total_equity=10000.0, closest_liquidation_pct=2.0),
            risk_engine=None,
            kill_switch=None,
            flatten_hook=hook,
            flatten_on_liquidation=False,
        )
        await mon._check_cycle()
        assert hook.fires == []

    asyncio.run(_run())


def test_daily_loss_fuse_kills_and_flattens() -> None:
    """Realized loss >= equity * pct → kill (daily_loss_fuse) + flatten."""

    async def _run() -> None:
        hook = _RecordingHook()
        ks = _KillSwitch()
        mon = _Monitor(
            RiskSnapshot(total_equity=10000.0, closest_liquidation_pct=100.0),
            risk_engine=None,
            kill_switch=ks,
            flatten_hook=hook,
            fills_ledger=_FillsLedger(-500.0),  # 5% of equity >= 1% threshold
            daily_loss_flatten_pct=0.01,
        )
        await mon._check_cycle()
        assert any("daily loss fuse" in r for r in hook.fires)
        assert ks.activations and ks.activations[0][1] == "daily_loss_fuse"

    asyncio.run(_run())


def test_daily_loss_below_threshold_no_fire() -> None:
    """Small losses below the pct fuse do not flatten."""

    async def _run() -> None:
        hook = _RecordingHook()
        mon = _Monitor(
            RiskSnapshot(total_equity=10000.0, closest_liquidation_pct=100.0),
            risk_engine=None,
            kill_switch=None,
            flatten_hook=hook,
            fills_ledger=_FillsLedger(-50.0),  # 0.5% < 1% threshold
            daily_loss_flatten_pct=0.01,
        )
        await mon._check_cycle()
        assert not any("daily loss fuse" in r for r in hook.fires)

    asyncio.run(_run())


def test_flatten_exception_does_not_abort_cycle() -> None:
    """A raising flatten callback must not kill the monitor cycle."""

    async def _run() -> None:
        hook = FlattenHook()

        def _boom() -> None:
            raise RuntimeError("flatten boom")

        hook.register(_boom)
        ks = _KillSwitch()
        mon = _Monitor(
            _drawdown_snapshot(),
            risk_engine=None,
            kill_switch=ks,
            flatten_hook=hook,
        )
        mon._peak_equity = 20000.0
        await mon._check_cycle()  # must not raise
        assert ks.activations  # cycle continued past the failing flatten

    asyncio.run(_run())

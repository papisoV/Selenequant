"""T3: FeeRateMonitor — threshold / confirmation window / kill opt-in / idempotent / survival."""

from __future__ import annotations

import asyncio

from selene_space_risk.fee_ledger import FeeLedger
from selene_space_risk.fee_monitor import FeeRateMonitor


def _monitor(
    rates: list[float],
    *,
    expected: float = 0.0005,
    threshold_pct: float = 0.5,
    confirmations: int = 3,
    kill: bool = False,
    ks: object | None = None,
    ledger: object | None = None,
) -> FeeRateMonitor:
    it = iter(rates)

    def _fetch() -> float | None:
        return next(it, None)

    return FeeRateMonitor(
        _fetch,
        expected_taker_rate=expected,
        anomaly_threshold_pct=threshold_pct,
        enable_kill_on_anomaly=kill,
        confirmations=confirmations,
        kill_switch=ks,
        ledger=ledger,
    )


def _feed(m: FeeRateMonitor, n: int) -> None:
    for _ in range(n):
        m.check()


class _KS:
    def __init__(self) -> None:
        self.activated: list[tuple] = []

    def auto_activate(self, reason: str, *, triggered_by_rule: str) -> None:  # type: ignore[override]
        self.activated.append((reason, triggered_by_rule))


def test_no_alert_below_threshold() -> None:
    """expected=0.0005 +50% → threshold 0.00075; 0.0007 stays below."""
    m = _monitor(rates=[0.0005, 0.0006, 0.0007])
    _feed(m, 3)
    assert not m.triggered
    assert m.consecutive_worsening == 0


def test_alert_only_after_confirmations() -> None:
    m = _monitor(rates=[0.0009] * 5)
    m.check()
    m.check()
    assert not m.triggered
    assert m.consecutive_worsening == 2
    m.check()  # 3rd consecutive → fire
    assert m.triggered
    assert m.consecutive_worsening == 3


def test_ledger_recorded_once_on_fire(tmp_path) -> None:
    """Persistent anomaly fires once; ledger gets exactly one snapshot."""
    ledger = FeeLedger(tmp_path / "fee.db")
    m = _monitor(rates=[0.0009] * 6, ledger=ledger)
    _feed(m, 6)
    snaps = ledger.query_range("live", 0, 10**18)
    assert len(snaps) == 1
    assert snaps[0].effective_rate == 0.0009
    assert snaps[0].account_id == "live"


def test_kill_opt_in() -> None:
    ks = _KS()
    m = _monitor(rates=[0.0009] * 4, kill=True, ks=ks)
    _feed(m, 3)
    assert len(ks.activated) == 1
    reason, rule = ks.activated[0]
    assert rule == "fee_anomaly"
    assert "fee-anomaly" in reason


def test_kill_opt_out_by_default() -> None:
    ks = _KS()
    m = _monitor(rates=[0.0009] * 4, kill=False, ks=ks)
    _feed(m, 3)
    assert not ks.activated


def test_recovery_resets_window() -> None:
    m = _monitor(rates=[0.0009, 0.0009, 0.0005, 0.0009, 0.0009, 0.0009])
    m.check()
    m.check()
    assert m.consecutive_worsening == 2
    m.check()  # recovers below threshold → reset
    assert m.consecutive_worsening == 0
    assert not m.triggered
    m.check()
    m.check()
    m.check()  # 3 new consecutive high → fires
    assert m.triggered


def test_fetch_error_is_noop() -> None:
    def _boom() -> float:
        raise RuntimeError("upstream down")

    m = FeeRateMonitor(_boom, confirmations=3)
    assert m.check() is None
    assert not m.triggered


def test_fetch_none_is_noop() -> None:
    m = FeeRateMonitor(lambda: None, confirmations=3)
    assert m.check() is None
    assert not m.triggered


def test_run_loop_survives_fetch_error() -> None:
    async def _run() -> None:
        calls: dict[str, int] = {"n": 0}

        def _flaky() -> float:
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("boom")
            return 0.0005

        m = FeeRateMonitor(_flaky, poll_interval_s=0.01, confirmations=3)
        m.start()
        try:
            await asyncio.sleep(0.05)
        finally:
            await m.stop()
        assert calls["n"] >= 3  # loop kept polling despite upstream errors
        assert not m.triggered
        assert m._task is None

    asyncio.run(_run())


def test_stop_clears_task() -> None:
    async def _run() -> None:
        m = _monitor(rates=[0.0005] * 3, confirmations=3)
        m.start()
        await m.stop()
        assert m._task is None

    asyncio.run(_run())


def test_reset_rearms() -> None:
    m = _monitor(rates=[0.0009] * 3)
    _feed(m, 3)
    assert m.triggered
    m.reset()
    assert not m.triggered
    assert m.consecutive_worsening == 0

"""T3: build_fee_monitor — fetch closure aggregation (Σfee/Σnotional, filters, min_fills)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.risk_assembly import build_fee_monitor


def _fill(
    fee: str | float,
    qty: str | float = "1.0",
    price: str | float = "100.0",
) -> SimpleNamespace:
    def _dec(v: str | float) -> Decimal:
        return Decimal(str(v)) if isinstance(v, str) else Decimal(str(v))

    return SimpleNamespace(
        fee=_dec(fee),
        quantity=_dec(qty),
        price=_dec(price),
        timestamp=1000,
    )


class _Ledger:
    def __init__(self, fills: list, error: Exception | None = None) -> None:
        self._fills = fills
        self._error = error

    def get_all_fills(self, account_id: str, start_ts: int, end_ts: int) -> list:  # type: ignore[no-untyped-def]
        del account_id, start_ts, end_ts
        if self._error is not None:
            raise self._error
        return self._fills


def test_effective_rate_is_volume_weighted() -> None:
    """1 fee fill of 0.001 on qty 1 @ 100 → notional 100 → rate 1e-5."""
    monitor = build_fee_monitor(
        fills_ledger=_Ledger([_fill(fee="0.001", qty="1", price="100")]),
        min_fills=1,
    )
    assert monitor.check() == pytest.approx(1e-5)


def test_zero_fee_fills_filtered() -> None:
    """Maker fills (fee=0) are excluded — they carry no taker-rate signal."""
    fills = [_fill(fee="0")] * 3 + [_fill(fee="0.001", qty="1", price="100")] * 2
    monitor = build_fee_monitor(fills_ledger=_Ledger(fills), min_fills=2)
    # Σfee = 0.002, Σnotional = 200 → 1e-5 (zero-fee fills excluded from both)
    assert monitor.check() == pytest.approx(1e-5)


def test_insufficient_fee_fills_returns_none() -> None:
    """Fewer than min_fills (default 5) fee-bearing fills → None no-op poll."""
    fills = [_fill(fee="0.001")] + [_fill(fee="0")] * 4
    monitor = build_fee_monitor(fills_ledger=_Ledger(fills))
    assert monitor.check() is None


def test_all_zero_fee_returns_none() -> None:
    monitor = build_fee_monitor(
        fills_ledger=_Ledger([_fill(fee="0")] * 5),
        min_fills=1,
    )
    assert monitor.check() is None


def test_fetch_error_returns_none() -> None:
    monitor = build_fee_monitor(
        fills_ledger=_Ledger([], error=RuntimeError("db down")),
        min_fills=1,
    )
    assert monitor.check() is None


def test_no_ledger_returns_none() -> None:
    monitor = build_fee_monitor(fills_ledger=None, min_fills=1)
    assert monitor.check() is None


def test_account_id_literal_default() -> None:
    """X1: account_id defaults to the literal 'live' (import-cycle-free)."""
    monitor = build_fee_monitor(fills_ledger=_Ledger([]))
    assert monitor._account_id == "live"


def test_kill_opt_in_pass_through() -> None:
    monitor = build_fee_monitor(
        fills_ledger=_Ledger([]),
        enable_kill_on_anomaly=True,
    )
    assert monitor._enable_kill is True


def test_ledger_only_when_fills_ledger_present() -> None:
    monitor = build_fee_monitor(fills_ledger=_Ledger([]))
    assert monitor._ledger is not None
    monitor2 = build_fee_monitor(fills_ledger=None)
    assert monitor2._ledger is None

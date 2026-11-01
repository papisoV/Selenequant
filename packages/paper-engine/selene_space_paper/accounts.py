"""Simulated trading accounts.

Money never touches ``float`` inside this module. Balances arrive from three
untrusted directions — HTTP JSON bodies, SQLite rows, CLI flags — so every
coercion detours through ``str``: ``0.1`` becomes exactly ``Decimal("0.1")``
rather than its binary approximation. Serialization flips back to plain
numbers only at the JSON boundary, keeping API payloads numeric.
"""

from __future__ import annotations

import time
from collections.abc import Callable
import uuid
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

_ZERO = Decimal("0")

_BALANCE_FIELDS = ("initial_balance", "current_balance", "equity", "total_realized_pnl", "total_fee")

_MISSING = object()


def now_ms() -> int:
    """Epoch milliseconds for record stamping."""
    return round(time.time() * 1000)


class AccountStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"


class AccountType(str, Enum):
    STRATEGY = "strategy"
    AGENT = "agent"
    MANUAL = "manual"


def _coerce_amount(raw: Any) -> Decimal:
    """Funnel any incoming scalar into an exact Decimal."""
    match raw:
        case Decimal():
            return raw
        case None:
            return _ZERO
        case bool():
            # bool subclasses int; route explicitly so True -> Decimal(1).
            return Decimal(int(raw))
        case int():
            return Decimal(raw)
        case _:
            return Decimal(str(raw))


class PaperAccount(BaseModel):
    """A paper-trading ledger: balances plus realized-PnL accumulators."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(default_factory=lambda: "pa-" + uuid.uuid4().hex[:8])
    name: str = Field(..., min_length=1)
    account_type: AccountType = AccountType.MANUAL
    strategy_id: str | None = Field(default=None)
    deployment_id: str | None = Field(default=None)
    initial_balance: Decimal
    current_balance: Decimal
    equity: Decimal = Field(default=_ZERO)
    total_realized_pnl: Decimal = Field(default=_ZERO)
    total_fee: Decimal = Field(default=_ZERO)
    maker_fee_rate: float = Field(default=0.0002)
    taker_fee_rate: float = Field(default=0.0005)
    status: AccountStatus = AccountStatus.ACTIVE
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)

    @field_serializer("*", when_used="json")
    def _json_numbers(self, v: Any) -> Any:
        # FastAPI clients expect JSON numbers; Decimal would render as str.
        return float(v) if isinstance(v, Decimal) else v

    @field_validator(*_BALANCE_FIELDS, mode="before")
    @classmethod
    def _amount_in(cls, v: Any) -> Any:
        return _coerce_amount(v)

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> "PaperAccount":
        """Rows written before the ``equity`` column lack the key; derive it."""
        if isinstance(obj, dict) and "equity" not in obj:
            obj = obj | {"equity": obj.get("current_balance", 0)}
        return super().model_validate(obj, **kwargs)


def _as_account(value: Any) -> PaperAccount:
    """Accept either live models or persisted dicts."""
    if isinstance(value, PaperAccount):
        return value
    return PaperAccount.model_validate(value)


class AccountManager:
    """In-memory registry keyed by account id."""

    def __init__(self) -> None:
        # alias kept for callers poking the raw mapping in tests
        self._accounts: dict[str, PaperAccount] = {}

    def _register(self, account: PaperAccount) -> None:
        self._accounts[account.id] = account
    def create_account(
        self,
        name: str,
        initial_balance: float | Decimal | str,
        *,
        taker_fee_rate: float = 0.0005,
        maker_fee_rate: float = 0.0002,
        strategy_id: str | None = None,
        account_type: AccountType = AccountType.MANUAL,
        deployment_id: str | None = None,
        account_id: str | None = None,
    ) -> PaperAccount:
        opening = _coerce_amount(initial_balance)
        draft: dict[str, Any] = {
            "name": name,
            "initial_balance": opening,
            "current_balance": opening,
            "maker_fee_rate": maker_fee_rate,
            "taker_fee_rate": taker_fee_rate,
            "strategy_id": strategy_id,
            "account_type": account_type,
            "deployment_id": deployment_id,
        }
        account = PaperAccount(**draft)
        if account_id is not None:
            # The execution engine addresses accounts by its own identifier,
            # so the created record must carry that lookup key.
            account.id = account_id
        self._register(account)
        return account

    @staticmethod
    def _reset_ledger(account: PaperAccount) -> None:
        for field in ("total_fee", "total_realized_pnl", "equity"):
            setattr(account, field, _ZERO)
        account.current_balance = account.initial_balance
        account.status = AccountStatus.ACTIVE

    def _apply_edit(self, account_id: str, edit: Callable[[PaperAccount], None]) -> PaperAccount | None:
        """Run ``edit`` on the account when present; restamp time either way."""
        try:
            account = self._accounts[account_id]
        except KeyError:
            return None
        edit(account)
        account.updated_at = now_ms()
        return account

    def update_balance(
        self, account_id: str, delta: float | Decimal | str
    ) -> PaperAccount | None:
        """Credit (or debit, for negative amounts) an account's balance."""
        amount = _coerce_amount(delta)

        def credit(account: PaperAccount) -> None:
            account.current_balance += amount

        return self._apply_edit(account_id, credit)

    def set_status(self, account_id: str, new_status: AccountStatus) -> PaperAccount | None:
        """Flip an account between ACTIVE/PAUSED/STOPPED."""
        def flip(account: PaperAccount) -> None:
            account.status = new_status

        return self._apply_edit(account_id, flip)

    def reset_account(self, acct_id: str) -> PaperAccount | None:
        return self._apply_edit(acct_id, self._reset_ledger)

    def get_account(self, account_id: str) -> PaperAccount | None:
        found = self._accounts.get(account_id)
        return found

    def delete_account(self, account_id: str) -> bool:
        gone = self._accounts.pop(account_id, _MISSING)
        return gone is not _MISSING

    def list_accounts(self) -> list[PaperAccount]:
        return [*self._accounts.values()]

    def get_all_data(self) -> dict[str, PaperAccount]:
        return dict(self._accounts)

    def load_data(self, accounts: dict[str, PaperAccount]) -> None:
        self._accounts = {key: _as_account(raw) for key, raw in accounts.items()}

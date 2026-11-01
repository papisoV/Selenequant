"""User accounts and redemption-code entitlements (v016, monetize Phase 1.3).

Entitlement model: a user is entitled while they hold at least one redemption
code with ``status='used'`` and ``expires_at`` in the future. Codes are issued
offline by the operator (爱发电 fulfillment → ``scripts/gen_redemption_codes.py``).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

_PBKDF2_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    """PBKDF2-SHA256, format ``pbkdf2$<iter>$<salt_hex>$<hash_hex>``."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iter_s, salt_s, hash_s = stored.split("$")
        if algo != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_s), int(iter_s)
        )
        return hmac.compare_digest(digest.hex(), hash_s)
    except (ValueError, TypeError):
        return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class BillingRepository:
    """Access to ``users`` and ``redemption_codes`` (v016)."""

    def __init__(self, db: Any) -> None:
        self._db = db

    # -- users ---------------------------------------------------------

    async def create_user(self, email: str, password: str) -> dict[str, Any]:
        user_id = uuid.uuid4().hex
        try:
            await self._db.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
                (user_id, email.strip().lower(), hash_password(password)),
            )
        except Exception as exc:
            # aiosqlite surfaces UNIQUE violation as IntegrityError (sqlite3.IntegrityError)
            if "UNIQUE" in str(exc):
                raise ValueError("email already registered") from exc
            raise
        return {"id": user_id, "email": email.strip().lower(), "role": "user"}

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT id, email, password_hash, role FROM users WHERE email = ?",
            (email.strip().lower(),),
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["password_hash"] = row.get("password_hash", "")
        return row

    # -- redemption codes ----------------------------------------------

    async def redeem_code(self, code: str, user_id: str) -> dict[str, Any]:
        """Bind an unused code to a user. Idempotent re-bind for the same
        user is allowed; a different user gets ValueError('already used')."""
        code = code.strip()
        rows = await self._db.execute(
            "SELECT code, status, bound_user_id, expires_at FROM redemption_codes"
            " WHERE code = ?",
            (code,),
        )
        if not rows:
            raise ValueError("invalid code")
        row = dict(rows[0])
        if row["status"] == "used":
            if row["bound_user_id"] == user_id:
                return {"code": code, "status": "used", "expires_at": row["expires_at"]}
            raise ValueError("code already used")
        expires_at = self._plan_expiry(row.get("plan") or "basic")
        await self._db.execute(
            "UPDATE redemption_codes SET status='used', bound_user_id=?, bound_at=?,"
            " expires_at=? WHERE code=? AND status='unused'",
            (user_id, _now_iso(), expires_at, code),
        )
        return {"code": code, "status": "used", "expires_at": expires_at}

    async def has_active_entitlement(self, user_id: str) -> bool:
        rows = await self._db.execute(
            "SELECT 1 FROM redemption_codes"
            " WHERE bound_user_id = ? AND status='used'"
            " AND expires_at IS NOT NULL AND expires_at > ?"
            " ORDER BY expires_at DESC LIMIT 1",
            (user_id, _now_iso()),
        )
        return bool(rows)

    async def entitlement_expiry(self, user_id: str) -> str | None:
        rows = await self._db.execute(
            "SELECT expires_at FROM redemption_codes"
            " WHERE bound_user_id = ? AND status='used'"
            " AND expires_at IS NOT NULL AND expires_at > ?"
            " ORDER BY expires_at DESC LIMIT 1",
            (user_id, _now_iso()),
        )
        return rows[0]["expires_at"] if rows else None

    async def insert_codes(self, codes: list[dict[str, Any]]) -> int:
        """Bulk-insert codes (operator CLI). Each dict: code, plan, expires_at?"""
        if not codes:
            return 0
        await self._db.execute_many(
            "INSERT INTO redemption_codes (code, plan, expires_at) VALUES (?, ?, ?)",
            [(c["code"], c.get("plan", "basic"), c.get("expires_at")) for c in codes],
        )
        return len(codes)

    # -- plan expiry -----------------------------------------------------

    @staticmethod
    def _plan_expiry(plan: str) -> str:
        """Entitlement window starts at redeem time. Plans: monthly/yearly."""
        now = datetime.now(timezone.utc)
        if plan == "yearly":
            expiry = now.replace(year=now.year + 1)
        elif plan == "trial":
            # ponytail: trial = 7 fixed days, add configurable window when needed
            from datetime import timedelta

            expiry = now + timedelta(days=7)
        else:
            # monthly (and 'basic' legacy default)
            month = now.month + 1
            year = now.year
            if month > 12:
                month = 1
                year += 1
            expiry = now.replace(year=year, month=month)
        return expiry.strftime("%Y-%m-%dT%H:%M:%SZ")

"""API key security audit utilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PermissionLevel(Enum):
    """Categorization of API key permission scope."""

    READ_ONLY = "read_only"
    TRADE_ONLY = "trade_only"
    TRADE_AND_WITHDRAW = "trade_and_withdraw"
    FULL_ACCESS = "full_access"


@dataclass
class APIKeyAuditResult:
    """Result of auditing an API key configuration."""

    exchange: str
    has_withdraw_permission: bool
    has_transfer_permission: bool
    ip_whitelist_active: bool
    ip_whitelist_addresses: list[str] = field(default_factory=list)
    subaccount_only: bool = False
    risk_level: str = "low"
    recommendations: list[str] = field(default_factory=list)


def audit_api_key(
    exchange: str,
    permissions: list[str],
    ip_whitelist: list[str] | None = None,
    is_subaccount: bool = False,
) -> APIKeyAuditResult:
    """Audit an API key configuration and assess its risk level.

    Args:
        exchange: Name of the exchange (e.g. "binance", "okx").
        permissions: List of permission strings the key has.
        ip_whitelist: List of IP addresses on the whitelist, or None if inactive.
        is_subaccount: Whether the key belongs to a sub-account.

    Returns:
        An APIKeyAuditResult with risk assessment and recommendations.
    """
    perms_lower = [p.lower() for p in permissions]
    has_withdraw = "withdraw" in perms_lower
    has_transfer = "transfer" in perms_lower
    ip_active = ip_whitelist is not None and len(ip_whitelist) > 0
    ip_addresses = list(ip_whitelist) if ip_whitelist else []

    risk_level = "low"
    recommendations: list[str] = []

    # Withdraw permission is the most dangerous
    if has_withdraw:
        risk_level = "high"
        recommendations.append("Remove withdraw permission from API key")

    # Transfer permission is also risky
    if has_transfer:
        if risk_level == "low":
            risk_level = "medium"
        recommendations.append("Remove transfer permission from API key")

    # No IP whitelist
    if not ip_active:
        if risk_level == "low":
            risk_level = "medium"
        recommendations.append("Enable IP whitelist for API key")

    # Withdraw + no IP whitelist = critical
    if has_withdraw and not ip_active:
        risk_level = "critical"

    # Subaccount recommendation
    if not is_subaccount:
        recommendations.append("Use a sub-account for API key isolation")

    return APIKeyAuditResult(
        exchange=exchange,
        has_withdraw_permission=has_withdraw,
        has_transfer_permission=has_transfer,
        ip_whitelist_active=ip_active,
        ip_whitelist_addresses=ip_addresses,
        subaccount_only=is_subaccount,
        risk_level=risk_level,
        recommendations=recommendations,
    )


def check_min_permissions(required: list[str], actual: list[str]) -> tuple[bool, list[str]]:
    """Check if actual permissions satisfy the required permissions.

    Args:
        required: Permissions that are needed.
        actual: Permissions that the key actually has.

    Returns:
        A tuple of (satisfied, missing_permissions).
    """
    actual_lower = {p.lower() for p in actual}
    missing = [p for p in required if p.lower() not in actual_lower]
    return len(missing) == 0, missing

"""Tests for API key security audit."""

from __future__ import annotations

from selene_space_exchange.security.api_key_audit import (
    PermissionLevel,
    audit_api_key,
    check_min_permissions,
)


class TestAuditApiKeyRiskLevels:
    """Tests for risk level assessment."""

    def test_trade_only_with_ip_whitelist_is_low(self):
        result = audit_api_key(
            exchange="binance",
            permissions=["read", "trade"],
            ip_whitelist=["1.2.3.4"],
            is_subaccount=True,
        )
        assert result.risk_level == "low"
        assert result.has_withdraw_permission is False
        assert result.has_transfer_permission is False
        assert result.ip_whitelist_active is True
        assert result.subaccount_only is True

    def test_withdraw_permission_is_high(self):
        result = audit_api_key(
            exchange="binance",
            permissions=["read", "trade", "withdraw"],
            ip_whitelist=["1.2.3.4"],
            is_subaccount=True,
        )
        assert result.risk_level == "high"
        assert result.has_withdraw_permission is True

    def test_withdraw_no_ip_whitelist_is_critical(self):
        result = audit_api_key(
            exchange="binance",
            permissions=["read", "trade", "withdraw"],
            ip_whitelist=None,
            is_subaccount=False,
        )
        assert result.risk_level == "critical"
        assert result.has_withdraw_permission is True
        assert result.ip_whitelist_active is False

    def test_no_ip_whitelist_is_medium(self):
        result = audit_api_key(
            exchange="okx",
            permissions=["read", "trade"],
            ip_whitelist=None,
            is_subaccount=True,
        )
        assert result.risk_level == "medium"
        assert result.ip_whitelist_active is False

    def test_transfer_permission_is_medium(self):
        result = audit_api_key(
            exchange="okx",
            permissions=["read", "trade", "transfer"],
            ip_whitelist=["1.2.3.4"],
            is_subaccount=True,
        )
        assert result.risk_level == "medium"
        assert result.has_transfer_permission is True

    def test_transfer_no_ip_whitelist_is_medium(self):
        result = audit_api_key(
            exchange="okx",
            permissions=["read", "trade", "transfer"],
            ip_whitelist=None,
            is_subaccount=True,
        )
        assert result.risk_level == "medium"

    def test_withdraw_with_ip_whitelist_is_high(self):
        # Withdraw + IP whitelist = high (not critical since IP whitelist is active)
        result = audit_api_key(
            exchange="binance",
            permissions=["read", "trade", "withdraw"],
            ip_whitelist=["1.2.3.4"],
            is_subaccount=True,
        )
        assert result.risk_level == "high"


class TestAuditApiKeyRecommendations:
    """Tests for recommendation generation."""

    def test_withdraw_recommends_removing(self):
        result = audit_api_key(
            exchange="binance",
            permissions=["trade", "withdraw"],
            ip_whitelist=["1.2.3.4"],
            is_subaccount=True,
        )
        assert any("withdraw" in r.lower() for r in result.recommendations)

    def test_transfer_recommends_removing(self):
        result = audit_api_key(
            exchange="binance",
            permissions=["trade", "transfer"],
            ip_whitelist=["1.2.3.4"],
            is_subaccount=True,
        )
        assert any("transfer" in r.lower() for r in result.recommendations)

    def test_no_ip_whitelist_recommends_adding(self):
        result = audit_api_key(
            exchange="binance",
            permissions=["trade"],
            ip_whitelist=None,
            is_subaccount=True,
        )
        assert any("ip whitelist" in r.lower() for r in result.recommendations)

    def test_not_subaccount_recommends_using_one(self):
        result = audit_api_key(
            exchange="binance",
            permissions=["trade"],
            ip_whitelist=["1.2.3.4"],
            is_subaccount=False,
        )
        assert any("sub-account" in r.lower() for r in result.recommendations)

    def test_fully_secure_has_no_recommendations(self):
        result = audit_api_key(
            exchange="binance",
            permissions=["read", "trade"],
            ip_whitelist=["1.2.3.4"],
            is_subaccount=True,
        )
        assert len(result.recommendations) == 0

    def test_empty_ip_whitelist_treated_as_inactive(self):
        result = audit_api_key(
            exchange="binance",
            permissions=["trade"],
            ip_whitelist=[],
            is_subaccount=True,
        )
        assert result.ip_whitelist_active is False
        assert result.risk_level == "medium"


class TestAuditApiKeyResultFields:
    """Tests for APIKeyAuditResult field correctness."""

    def test_exchange_field(self):
        result = audit_api_key("okx", ["trade"], ip_whitelist=["1.2.3.4"])
        assert result.exchange == "okx"

    def test_ip_whitelist_addresses(self):
        result = audit_api_key(
            exchange="binance",
            permissions=["trade"],
            ip_whitelist=["1.2.3.4", "5.6.7.8"],
            is_subaccount=True,
        )
        assert result.ip_whitelist_addresses == ["1.2.3.4", "5.6.7.8"]

    def test_case_insensitive_permissions(self):
        result = audit_api_key(
            exchange="binance",
            permissions=["Trade", "Withdraw"],
            ip_whitelist=["1.2.3.4"],
            is_subaccount=True,
        )
        assert result.has_withdraw_permission is True
        assert result.risk_level == "high"


class TestCheckMinPermissions:
    """Tests for the check_min_permissions utility."""

    def test_satisfied(self):
        satisfied, missing = check_min_permissions(["read", "trade"], ["read", "trade", "withdraw"])
        assert satisfied is True
        assert missing == []

    def test_missing_permissions(self):
        satisfied, missing = check_min_permissions(["read", "trade", "transfer"], ["read"])
        assert satisfied is False
        assert "trade" in missing
        assert "transfer" in missing

    def test_exact_match(self):
        satisfied, missing = check_min_permissions(["read", "trade"], ["read", "trade"])
        assert satisfied is True
        assert missing == []

    def test_empty_required(self):
        satisfied, missing = check_min_permissions([], ["read"])
        assert satisfied is True
        assert missing == []

    def test_case_insensitive(self):
        satisfied, missing = check_min_permissions(["Trade"], ["trade"])
        assert satisfied is True
        assert missing == []


class TestPermissionLevel:
    """Tests for the PermissionLevel enum."""

    def test_enum_values(self):
        assert PermissionLevel.READ_ONLY.value == "read_only"
        assert PermissionLevel.TRADE_ONLY.value == "trade_only"
        assert PermissionLevel.TRADE_AND_WITHDRAW.value == "trade_and_withdraw"
        assert PermissionLevel.FULL_ACCESS.value == "full_access"

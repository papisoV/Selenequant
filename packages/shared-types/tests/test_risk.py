"""Wire-format locks for selene_space_types.risk — level enum + legacy
aliases, rule/decision/alert/config serialization and defaults."""

from selene_space_types.risk import (
    RiskAlert,
    RiskAlertConfig,
    RiskDecision,
    RiskLevel,
    RiskRule,
)


def _recycle(model_cls, instance):
    return model_cls.model_validate_json(instance.model_dump_json())


class TestRiskLevelEnum:
    def test_canonical_ladder_of_five(self):
        ladder = {"info", "warning", "restricted", "dangerous", "critical"}
        assert {lvl.value for lvl in RiskLevel} == ladder

    def test_legacy_names_alias_canonical_members(self):
        pairs = [
            (RiskLevel.SAFE, RiskLevel.INFO),
            (RiskLevel.ADJUSTED, RiskLevel.INFO),
            (RiskLevel.BLOCKED, RiskLevel.CRITICAL),
        ]
        for legacy, canonical in pairs:
            assert legacy is canonical


class TestRiskRuleWire:
    def test_survives_json_cycle(self):
        rule = RiskRule(
            id="rule-cap-notional",
            name="Notional Cap",
            description="Single order notional may not exceed 5k USDT",
            level=RiskLevel.RESTRICTED,
            parameters={"max_notional": 5000.0},
            enabled=True,
        )
        assert _recycle(RiskRule, rule) == rule

    def test_constructor_defaults_are_sane(self):
        rule = RiskRule(id="r-x", name="Bare")
        checks = {
            "enabled": rule.enabled is True,
            "parameters": rule.parameters == {},
            "description": rule.description == "",
            "level": rule.level is RiskLevel.WARNING,
        }
        assert all(checks.values())


class TestRiskDecisionWire:
    def test_survives_json_cycle_with_alerts(self):
        decision = RiskDecision(
            allowed=True,
            level=RiskLevel.RESTRICTED,
            reason="Position too large",
            alerts=[
                RiskAlert(
                    rule_id="notional_cap",
                    level=RiskLevel.RESTRICTED,
                    message="Order exceeds cap",
                    context={"symbol": "ETH/USDT", "pct": 0.31},
                    timestamp=1720005555666,
                )
            ],
            timestamp=1720005555666,
        )
        restored = _recycle(RiskDecision, decision)
        assert restored == decision
        assert restored.allowed is True
        assert len(restored.alerts) == 1

    def test_allow_by_default_decision(self):
        quiet = RiskDecision(timestamp=1720005555777)
        quartet = (quiet.allowed, quiet.level, quiet.alerts, quiet.reason)
        assert quartet == (True, RiskLevel.INFO, [], "")


class TestRiskAlertWire:
    def test_survives_json_cycle(self):
        alert = RiskAlert(
            rule_id="daily_loss",
            level=RiskLevel.DANGEROUS,
            message="Daily loss limit breached",
            context={"realized": -620.0},
            timestamp=1720005555888,
        )
        assert _recycle(RiskAlert, alert) == alert

    def test_context_and_timestamp_default(self):
        bare = RiskAlert(rule_id="probe", level=RiskLevel.INFO, message="ping")
        duo = (bare.context, bare.timestamp)
        assert duo == ({}, 0)


class TestRiskAlertConfigWire:
    def test_constructor_defaults(self):
        cfg = RiskAlertConfig()
        trio = (cfg.enabled, cfg.min_level, cfg.rules)
        assert trio == (True, RiskLevel.INFO, {})

    def test_survives_json_cycle(self):
        cfg = RiskAlertConfig(
            enabled=True,
            min_level=RiskLevel.WARNING,
            rules={"kelly_sizing": {"enabled": False}},
        )
        assert _recycle(RiskAlertConfig, cfg) == cfg

"""Unit tests for TieredTrailingConfig persistence in SettingsService.

Covers:
- Default values present (8 tiered_* float keys)
- Round-trip: PUT → GET reflects new value
- Invalid value ignored (PUT "abc" doesn't crash, default preserved)
- Config construction from risk dict
"""

from __future__ import annotations

import pytest

from app.core.settings_service import SettingsService


@pytest.fixture
def temp_settings(tmp_path, monkeypatch):
    """SettingsService backed by a temp config file."""
    cfg_path = tmp_path / "settings.json"
    monkeypatch.setattr(
        SettingsService, "_user_config_path", lambda self, user_id=None: cfg_path
    )
    return SettingsService()


TIERED_KEYS = {
    "tiered_hard_stop_pct",
    "tiered_low_tier_threshold",
    "tiered_first_tier_threshold",
    "tiered_second_tier_threshold",
    "tiered_low_tier_drop_pct",
    "tiered_first_tier_drop_pct",
    "tiered_second_tier_drop_pct",
    "tiered_min_emit_interval_seconds",
}


def test_defaults_contain_8_tiered_floats():
    """_defaults() risk section has 8 tiered_* keys, all float."""
    risk = SettingsService._defaults()["risk"]
    tiered_in_defaults = {k: v for k, v in risk.items() if k in TIERED_KEYS}
    assert len(tiered_in_defaults) == 8
    for k, v in tiered_in_defaults.items():
        assert isinstance(v, float), f"{k} should be float, got {type(v).__name__}"


def test_defaults_values_match_dataclass():
    """Default values match TieredTrailingConfig dataclass defaults."""
    from selene_space_paper.tiered_trailing_stop import TieredTrailingConfig

    cfg = TieredTrailingConfig()
    risk = SettingsService._defaults()["risk"]
    assert risk["tiered_hard_stop_pct"] == cfg.hard_stop_pct
    assert risk["tiered_low_tier_threshold"] == cfg.low_tier_threshold
    assert risk["tiered_first_tier_threshold"] == cfg.first_tier_threshold
    assert risk["tiered_second_tier_threshold"] == cfg.second_tier_threshold
    assert risk["tiered_low_tier_drop_pct"] == cfg.low_tier_drop_pct
    assert risk["tiered_first_tier_drop_pct"] == cfg.first_tier_drop_pct
    assert risk["tiered_second_tier_drop_pct"] == cfg.second_tier_drop_pct
    assert risk["tiered_min_emit_interval_seconds"] == cfg.min_emit_interval_seconds


@pytest.mark.asyncio
async def test_put_then_get_round_trip(temp_settings):
    """PUT tiered_hard_stop_pct=0.03 → GET reflects new value."""
    await temp_settings.update_settings(
        user_id=None, payload={"risk": {"tiered_hard_stop_pct": 0.03}}
    )
    result = await temp_settings.get_settings(user_id=None)
    assert result["risk"]["tiered_hard_stop_pct"] == 0.03


@pytest.mark.asyncio
async def test_invalid_value_ignored(temp_settings):
    """PUT 'abc' for tiered_hard_stop_pct doesn't crash, default preserved."""
    original = await temp_settings.get_settings(user_id=None)
    original_value = original["risk"]["tiered_hard_stop_pct"]
    await temp_settings.update_settings(
        user_id=None, payload={"risk": {"tiered_hard_stop_pct": "abc"}}
    )
    result = await temp_settings.get_settings(user_id=None)
    assert result["risk"]["tiered_hard_stop_pct"] == original_value


@pytest.mark.asyncio
async def test_all_8_keys_round_trip(temp_settings):
    """PUT all 8 keys → GET all reflect new values."""
    new_values = {
        "tiered_hard_stop_pct": 0.025,
        "tiered_low_tier_threshold": 0.018,
        "tiered_first_tier_threshold": 0.035,
        "tiered_second_tier_threshold": 0.065,
        "tiered_low_tier_drop_pct": 0.45,
        "tiered_first_tier_drop_pct": 0.28,
        "tiered_second_tier_drop_pct": 0.18,
        "tiered_min_emit_interval_seconds": 25.0,
    }
    await temp_settings.update_settings(
        user_id=None, payload={"risk": new_values}
    )
    result = await temp_settings.get_settings(user_id=None)
    for k, expected in new_values.items():
        assert result["risk"][k] == expected, f"{k}: expected {expected}, got {result['risk'][k]}"


def test_config_construction_from_risk_dict():
    """TieredTrailingConfig can be constructed from risk dict with float conversion."""
    from selene_space_paper.tiered_trailing_stop import TieredTrailingConfig

    risk = {
        "tiered_hard_stop_pct": "0.03",  # string from old settings format
        "tiered_low_tier_threshold": 0.015,
        "tiered_first_tier_threshold": 0.03,
        "tiered_second_tier_threshold": 0.06,
        "tiered_low_tier_drop_pct": 0.5,
        "tiered_first_tier_drop_pct": 0.3,
        "tiered_second_tier_drop_pct": 0.2,
        "tiered_min_emit_interval_seconds": 30.0,
    }
    cfg = TieredTrailingConfig(
        hard_stop_pct=float(risk.get("tiered_hard_stop_pct", 0.02)),
        low_tier_threshold=float(risk.get("tiered_low_tier_threshold", 0.015)),
        first_tier_threshold=float(risk.get("tiered_first_tier_threshold", 0.03)),
        second_tier_threshold=float(risk.get("tiered_second_tier_threshold", 0.06)),
        low_tier_drop_pct=float(risk.get("tiered_low_tier_drop_pct", 0.5)),
        first_tier_drop_pct=float(risk.get("tiered_first_tier_drop_pct", 0.3)),
        second_tier_drop_pct=float(risk.get("tiered_second_tier_drop_pct", 0.2)),
        min_emit_interval_seconds=float(risk.get("tiered_min_emit_interval_seconds", 30.0)),
    )
    assert cfg.hard_stop_pct == 0.03
    assert cfg.low_tier_threshold == 0.015

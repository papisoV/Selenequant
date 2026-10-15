"""P3-001 tests: experience sedimentation (digest → save → load roundtrip)."""

from __future__ import annotations

import json
from pathlib import Path

from selene_space_decision_workflow.experience import (
    digest_experience,
    load_experiences,
    save_experience,
)

OUTCOME = {
    "verdict": "REVALIDATED",
    "declared_intent": {"symbol": "BTC/USDT", "interval": "1h"},
    "in_sample": {"total_return": 0.12, "sharpe_ratio": 1.1},
    "oos": {"total_return": 0.05, "sharpe_ratio": 0.8},
    "baseline_sOL4h_drift": {"total_return": -0.356},
    "method": "IS 2/3 + OOS 1/3, declared config BTC/USDT 1h, no re-tuning",
}


def test_digest_maps_outcome_fields() -> None:
    case = digest_experience(OUTCOME, created_iso="2026-09-09T00:00:00+00:00")
    assert case.symbol == "BTC/USDT"
    assert case.interval == "1h"
    assert case.verdict == "REVALIDATED"
    assert case.oos["total_return"] == 0.05
    assert case.method.startswith("IS 2/3")
    assert case.id.startswith("exp-BTCUSDT-1h-revalidated")
    assert "REVALIDATE" in case.provenance


def test_digest_tolerates_missing_fields() -> None:
    case = digest_experience({}, created_iso="2026-09-09T00:00:00+00:00")
    assert case.symbol == "unknown"
    assert case.verdict == "unknown"
    assert case.in_sample == {}
    assert case.oos == {}


def test_save_load_roundtrip(tmp_path: Path) -> None:
    case = digest_experience(OUTCOME, created_iso="2026-09-09T12:00:00+00:00")
    path = save_experience(case, root=tmp_path)
    assert path.is_file()
    assert "revalidated" in path.name
    assert "BTC_USDT_1h" in str(path.parent)
    loaded = load_experiences("BTC/USDT", "1h", root=tmp_path)
    assert len(loaded) == 1
    assert loaded[0] == case


def test_load_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert load_experiences("ETH/USDT", "4h", root=tmp_path) == []


def test_load_orders_by_recency_and_limits(tmp_path: Path) -> None:
    for i in range(4):
        case = digest_experience(
            OUTCOME, created_iso=f"2026-09-0{i + 1}T00:00:00+00:00"
        )
        save_experience(case, root=tmp_path)
    loaded = load_experiences("BTC/USDT", "1h", limit=2, root=tmp_path)
    assert len(loaded) == 2
    assert loaded[0].created_iso > loaded[1].created_iso


def test_load_skips_corrupt_json(tmp_path: Path) -> None:
    case = digest_experience(OUTCOME, created_iso="2026-09-09T00:00:00+00:00")
    save_experience(case, root=tmp_path)
    directory = tmp_path / "BTC_USDT_1h"
    (directory / "garbage.json").write_text("{not json", encoding="utf-8")
    loaded = load_experiences("BTC/USDT", "1h", root=tmp_path)
    assert loaded == [case]


# ------------------------------------------------------- regime dimension (B1)


def test_legacy_json_without_regime_key_loads_with_default(tmp_path: Path) -> None:
    """Pre-regime on-disk JSON has no market_regime key — ExperienceCase(**json)
    must fall back to the default and never drop the case."""
    case = digest_experience(OUTCOME, created_iso="2026-09-09T00:00:00+00:00")
    save_experience(case, root=tmp_path)
    path = next((tmp_path / "BTC_USDT_1h").glob("*.json"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["market_regime"]
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    loaded = load_experiences("BTC/USDT", "1h", root=tmp_path)
    assert len(loaded) == 1
    assert loaded[0].market_regime == ""


def test_digest_carries_outcome_regime() -> None:
    case = digest_experience(
        {**OUTCOME, "market_regime": "trend_up"},
        created_iso="2026-09-09T00:00:00+00:00",
    )
    assert case.market_regime == "trend_up"


def test_regime_roundtrip_survives_save_load(tmp_path: Path) -> None:
    case = digest_experience(
        {**OUTCOME, "market_regime": "range"},
        created_iso="2026-09-09T00:00:00+00:00",
    )
    save_experience(case, root=tmp_path)
    loaded = load_experiences("BTC/USDT", "1h", root=tmp_path)
    assert loaded == [case]
    assert loaded[0].market_regime == "range"


def test_regime_filter_three_states(tmp_path: Path) -> None:
    """None → all; matching regime → hit; non-matching → excluded but
    empty-regime legacy cases stay visible (never drop precedent)."""
    trend = digest_experience(
        {**OUTCOME, "market_regime": "trend_up"},
        created_iso="2026-09-01T00:00:00+00:00",
    )
    legacy = digest_experience(OUTCOME, created_iso="2026-09-02T00:00:00+00:00")
    save_experience(trend, root=tmp_path)
    save_experience(legacy, root=tmp_path)

    assert len(load_experiences("BTC/USDT", "1h", root=tmp_path)) == 2
    assert (
        len(load_experiences("BTC/USDT", "1h", root=tmp_path, market_regime="trend_up"))
        == 2
    )
    assert load_experiences("BTC/USDT", "1h", root=tmp_path, market_regime="range") == [
        legacy
    ]

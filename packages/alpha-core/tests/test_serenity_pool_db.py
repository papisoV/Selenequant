"""C1: SerenityPoolDB pool-scan persistence.

Covers JSON round-trip (tuples survive as tuples, theme_name intact), latest-per
-theme semantics, tolerant decode of partial / older / corrupt payloads, the
never-scanned None path, and explicit tmp-path usage.
"""

from __future__ import annotations

import sys
import json
from pathlib import Path

_alpha_core = str(Path(__file__).parent.parent / "selene_space_alpha")
if _alpha_core not in sys.path:
    sys.path.insert(0, _alpha_core)

from serenity.pool import PoolCandidate, PoolLayerSummary, SerenityPoolScan  # noqa: E402
from serenity.pool_db import SerenityPoolDB, canonical_score_db_path  # noqa: E402
from serenity.score_db import canonical_score_db_path as score_db_path  # noqa: E402


def _sample_scan(theme="lending", scanned_at=1735689600000) -> SerenityPoolScan:
    strong = PoolCandidate(
        symbol="AAA", name="Alpha", category="Lending", chains=("eth", "arb"),
        normalized_score=0.72, tier="strong_bullish", scarce_layer_score=0.86,
        evidence_strength=0.7, evidence_label="强", rationale="卡点:高共识稀缺",
        top_penalty="fork_risk:0.55",
        disproof=("收入连续 3 周下滑打破动量",),
    )
    weak = PoolCandidate(
        symbol="BBB", name="Beta", category="DEX", chains=("eth",),
        normalized_score=0.60, tier="bullish", scarce_layer_score=0.40,
        evidence_strength=0.3, evidence_label="中", rationale="无明显卡点特征",
        top_penalty="tvl_concentration:0.70", disproof=(),
    )
    return SerenityPoolScan(
        theme=theme, theme_name="Lending", scanned_at=scanned_at,
        universe_size=20,
        layer_summary=(
            PoolLayerSummary(layer="Lending", count=15, top_scarce_symbols=("AAA",)),
        ),
        ranked=(strong, weak),
        top_picks=("AAA",),
        weak_evidence_count=1,
    )


def test_canonical_pool_db_shares_score_db_path():
    assert canonical_score_db_path() == score_db_path()


def test_default_db_path_is_canonical():
    assert SerenityPoolDB()._db_path == canonical_score_db_path()


def test_round_trip_preserves_scan_and_tuple_shape(tmp_path):
    db = SerenityPoolDB(db_path=tmp_path / "pool.db")
    db.open()
    db.put_scan(_sample_scan())
    restored = db.get_latest("lending")
    assert restored is not None
    assert restored.theme == "lending"
    assert restored.theme_name == "Lending"  # stored column, not re-derived
    assert restored.scanned_at == 1735689600000
    assert restored.universe_size == 20
    assert restored.weak_evidence_count == 1
    assert restored.top_picks == ("AAA",)

    assert isinstance(restored.layer_summary, tuple)
    assert isinstance(restored.layer_summary[0].top_scarce_symbols, tuple)
    assert isinstance(restored.ranked, tuple)
    assert isinstance(restored.ranked[0].chains, tuple)
    assert isinstance(restored.ranked[0].disproof, tuple)

    assert restored.layer_summary[0].layer == "Lending"
    assert restored.layer_summary[0].count == 15
    assert restored.layer_summary[0].top_scarce_symbols == ("AAA",)
    assert [c.symbol for c in restored.ranked] == ["AAA", "BBB"]
    assert restored.ranked[0].chains == ("eth", "arb")
    assert restored.ranked[0].disproof == ("收入连续 3 周下滑打破动量",)
    assert restored.ranked[1].disproof == ()
    assert db.count() == 1
    db.close()


def test_get_latest_returns_none_before_any_scan(tmp_path):
    db = SerenityPoolDB(db_path=tmp_path / "pool.db")
    db.open()
    assert db.get_latest("lending") is None
    assert db.count() == 0
    db.close()


def test_latest_wins_per_theme_and_themes_are_independent(tmp_path):
    db = SerenityPoolDB(db_path=tmp_path / "pool.db")
    db.open()
    db.put_scan(_sample_scan(scanned_at=1000))
    db.put_scan(_sample_scan(scanned_at=2000))
    db.put_scan(_sample_scan(theme="dex", scanned_at=500))

    latest = db.get_latest("lending")
    assert latest.scanned_at == 2000  # newest scan for the theme
    dex = db.get_latest("dex")
    assert dex.scanned_at == 500
    assert db.get_latest("yield") is None

    all_latest = db.get_all_latest()
    by_theme = {s.theme: s for s in all_latest}
    assert by_theme["lending"].scanned_at == 2000
    assert by_theme["dex"].scanned_at == 500
    assert set(by_theme) == {"lending", "dex"}
    assert db.count() == 3
    db.close()


def test_tolerant_decode_fills_missing_fields(tmp_path):
    db = SerenityPoolDB(db_path=tmp_path / "pool.db")
    db.open()
    # Raw insert: candidate missing chains/disproof, layer missing
    # top_scarce_symbols — simulates a schema evolution the decoder tolerates.
    ranked = [
        {
            "symbol": "AAA", "name": "Alpha", "category": "Lending",
            "normalized_score": 0.72, "tier": "strong_bullish",
            "scarce_layer_score": 0.86, "evidence_strength": 0.7,
            "evidence_label": "强", "rationale": "x", "top_penalty": "fork_risk:0.55",
        }
    ]
    layer = [{"layer": "Lending", "count": 15}]
    with db._conn:
        db._conn.execute(
            "INSERT INTO serenity_pool_scans (theme, theme_name, scanned_at,"
            " universe_size, weak_evidence_count, layer_summary, top_picks,"
            " ranked, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "lending", "Lending", 99, 20, 1,
                json.dumps(layer), None, json.dumps(ranked), 1,
            ),
        )
    restored = db.get_latest("lending")
    assert restored.scanned_at == 99
    assert restored.top_picks == ()  # NULL top_picks column
    assert restored.ranked[0].chains == ()
    assert restored.ranked[0].disproof == ()
    assert restored.layer_summary[0].top_scarce_symbols == ()
    db.close()


def test_tolerant_decode_ignores_corrupt_json(tmp_path):
    db = SerenityPoolDB(db_path=tmp_path / "pool.db")
    db.open()
    with db._conn:
        db._conn.execute(
            "INSERT INTO serenity_pool_scans (theme, theme_name, scanned_at,"
            " ranked) VALUES (?, ?, ?, ?)",
            ("lending", "Lending", 1, "{not-json"),
        )
    restored = db.get_latest("lending")
    assert restored is not None
    assert restored.ranked == ()
    assert restored.layer_summary == ()
    assert restored.top_picks == ()
    db.close()


def test_reopen_after_close_reloads(tmp_path):
    path = tmp_path / "pool.db"
    db = SerenityPoolDB(db_path=path)
    db.put_scan(_sample_scan())
    db.close()

    db2 = SerenityPoolDB(db_path=path)
    db2.open()
    restored = db2.get_latest("lending")
    assert restored is not None
    assert restored.theme_name == "Lending"
    db2.close()


def test_pytest_imports_serenity_pool():
    # Guard: canonical path resolver must not depend on the storage migrations
    # framework (that targets a different DB).
    assert SerenityPoolDB
    assert PoolCandidate
    assert PoolLayerSummary
    assert SerenityPoolScan

"""Alert-rule store: persistence, the enabled filter, and corrupt-row survival.

The store is what makes a rule outlive the browser tab, so these tests pin the
three properties the evaluator depends on: a rule written before a restart is
read after one, ``list_enabled`` omits disabled rules, and one corrupt row does
not take the rest of the rule set with it.
"""

from __future__ import annotations

import json
import sqlite3

from app.services.alert_rules_store import AlertRulesStore


def _rule(**overrides):
    base = {
        "name": "BTC 突破",
        "type": "price",
        "threshold": {"operator": "gt", "value": 100000},
        "enabled": True,
        "symbol": "BTC/USDT",
    }
    base.update(overrides)
    return base


def test_upsert_then_read_survives_reopen(tmp_path):
    db = tmp_path / "rules.db"
    store = AlertRulesStore(db)
    stored = store.upsert(_rule())
    rule_id = stored["id"]
    assert rule_id

    # A fresh instance is what a process restart looks like.
    reopened = AlertRulesStore(db)
    got = reopened.get(rule_id)
    assert got is not None
    assert got["name"] == "BTC 突破"
    assert got["threshold"] == {"operator": "gt", "value": 100000}
    assert got["symbol"] == "BTC/USDT"


def test_upsert_generates_id_when_missing_and_keeps_existing(tmp_path):
    store = AlertRulesStore(tmp_path / "rules.db")
    first = store.upsert(_rule())
    second = store.upsert(_rule(id=first["id"], name="改名"))
    assert second["id"] == first["id"]
    assert store.get(first["id"])["name"] == "改名"


def test_upsert_overwrites_same_id_rather_than_duplicating(tmp_path):
    store = AlertRulesStore(tmp_path / "rules.db")
    rule = store.upsert(_rule())
    store.upsert({**rule, "name": "第二版"})
    rows = store.list_all()
    assert len(rows) == 1
    assert rows[0]["name"] == "第二版"


def test_list_enabled_skips_disabled_rules(tmp_path):
    store = AlertRulesStore(tmp_path / "rules.db")
    on = store.upsert(_rule(name="开"))
    off = store.upsert(_rule(name="关", enabled=False))

    enabled_ids = {r["id"] for r in store.list_enabled()}
    assert enabled_ids == {on["id"]}
    # ...but the disabled rule is still listed for the UI.
    assert {r["id"] for r in store.list_all()} == {on["id"], off["id"]}


def test_corrupt_payload_row_is_skipped_others_still_read(tmp_path):
    db = tmp_path / "rules.db"
    store = AlertRulesStore(db)
    good = store.upsert(_rule(name="好的"))
    broken = store.upsert(_rule(name="坏的"))

    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE alert_rules SET payload = ? WHERE id = ?",
            ("{not json", broken["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    rows = store.list_all()
    assert [r["id"] for r in rows] == [good["id"]]
    assert store.get(broken["id"]) is None


def test_non_object_payload_row_is_skipped(tmp_path):
    db = tmp_path / "rules.db"
    store = AlertRulesStore(db)
    rule = store.upsert(_rule())
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE alert_rules SET payload = ? WHERE id = ?",
            (json.dumps([1, 2]), rule["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    assert store.list_all() == []


def test_enabled_column_tracks_payload(tmp_path):
    """The evaluator filters on the column; the UI reads the payload.

    They must never disagree — an enabled rule invisible to the evaluator is
    exactly the silent-no-monitoring failure this feature exists to prevent.
    """
    store = AlertRulesStore(tmp_path / "rules.db")
    rule = store.upsert(_rule(enabled=True))
    conn = sqlite3.connect(tmp_path / "rules.db")
    try:
        column = conn.execute(
            "SELECT enabled FROM alert_rules WHERE id = ?", (rule["id"],)
        ).fetchone()[0]
    finally:
        conn.close()
    assert column == 1
    assert store.get(rule["id"])["enabled"] is True

    store.upsert({**rule, "enabled": False})
    conn = sqlite3.connect(tmp_path / "rules.db")
    try:
        column = conn.execute(
            "SELECT enabled FROM alert_rules WHERE id = ?", (rule["id"],)
        ).fetchone()[0]
    finally:
        conn.close()
    assert column == 0


def test_delete_reports_whether_anything_was_removed(tmp_path):
    store = AlertRulesStore(tmp_path / "rules.db")
    rule = store.upsert(_rule())
    assert store.delete(rule["id"]) is True
    assert store.delete(rule["id"]) is False
    assert store.list_all() == []


def test_seeded_flag_distinguishes_delete_all_from_never_seeded(tmp_path):
    """``docs`` requirement: empty-because-deleted must not look like fresh.

    The client uploads its localStorage rules exactly once. If "empty" meant
    "not yet seeded", a user who deleted every rule would get them all back on
    the next page load.
    """
    db = tmp_path / "rules.db"
    store = AlertRulesStore(db)
    assert store.is_seeded() is False

    rule = store.upsert(_rule())
    store.mark_seeded()
    store.delete(rule["id"])

    reopened = AlertRulesStore(db)
    assert reopened.list_all() == []
    assert reopened.is_seeded() is True


def test_patch_merges_known_fields_and_reports_missing(tmp_path):
    store = AlertRulesStore(tmp_path / "rules.db")
    rule = store.upsert(_rule())
    patched = store.patch(rule["id"], {"enabled": False})
    assert patched is not None
    assert patched["enabled"] is False
    assert patched["name"] == "BTC 突破"
    assert store.patch("does-not-exist", {"enabled": False}) is None


def test_in_memory_path_is_rejected_loudly(tmp_path):
    """A silently-empty in-memory store would look like "no rules configured".

    Passing ":memory:" to a store that opens one connection per operation
    creates the schema on one connection and reads an empty database on the
    next. That failure mode produces zero rules forever with no error, so the
    constructor refuses it instead.
    """
    import pytest

    with pytest.raises(ValueError, match="memory"):
        AlertRulesStore(":memory:")

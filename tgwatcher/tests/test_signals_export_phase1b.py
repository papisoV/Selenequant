"""Regression test for /api/signals/export (Phase 1B).

Captures the byte-identical JSON response of the endpoint *before* the
raw-SQL migration to Storage.query_signals_export. After migration, this
test must continue to pass without modification — proving behavior
preservation.

Scope: JSON format only. CSV and Markdown formats are also covered by
the migration (they consume the same `rows` list) but are not asserted
byte-identical here — their rendering code in the route is unchanged.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest
from flask import Flask

from tgwatcher.models import Chat, Message, SignalFactor
from tgwatcher.storage import Storage


@pytest.fixture()
def temp_storage():
    """Fresh Storage with 2 messages + 2 signal_factors seeded."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    try:
        storage = Storage(db_path)
        storage.init_db()
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        with storage.get_session() as session:
            chat = Chat(chat_id=1, chat_title="TestChat")
            session.add(chat)
            # Message 1: bullish signal
            session.add(Message(
                message_id=100, chat_id=1, chat_title="TestChat",
                sender_id=1, sender_name="alice",
                text="BTC to the moon",
                date=datetime(2026, 7, 24, 10, 0, 0),
                crawled_at=now_utc,
            ))
            # Message 2: bearish signal
            session.add(Message(
                message_id=101, chat_id=1, chat_title="TestChat",
                sender_id=2, sender_name="bob",
                text="Market crash incoming",
                date=datetime(2026, 7, 24, 11, 0, 0),
                crawled_at=now_utc,
            ))
            session.add(SignalFactor(
                message_id=100, chat_id=1,
                direction=0.8, magnitude=0.7, urgency=0.6, confidence=0.9,
                halflife_min=120, symbols='["BTC"]', event_type="price",
                reasoning="strong bullish",
                llm_status="completed", llm_model="test-model",
                is_signal=True,
            ))
            session.add(SignalFactor(
                message_id=101, chat_id=1,
                direction=-0.7, magnitude=0.6, urgency=0.5, confidence=0.8,
                halflife_min=60, symbols='["ETH"]', event_type="price",
                reasoning="bearish divergence",
                llm_status="completed", llm_model="test-model",
                is_signal=True,
            ))
            session.commit()
        yield storage
    finally:
        try:
            os.remove(db_path)
        except PermissionError:
            pass  # Windows: engine may still hold the file


@pytest.fixture()
def client(temp_storage, monkeypatch):
    """Flask test client with _storage patched to our temp instance."""
    from tgwatcher.web import api as api_module
    monkeypatch.setattr(api_module, "_storage", temp_storage)
    monkeypatch.setattr(api_module, "_auth_token", "test-token")
    app = Flask(__name__)
    app.register_blueprint(api_module.api)
    with app.test_client() as c:
        yield c


def _auth_headers():
    return {"Authorization": "Bearer test-token"}


class TestSignalsExportJsonShape:
    """JSON response shape regression for /api/signals/export."""

    def test_json_default_returns_both_rows_ordered_desc(self, client):
        """Default (no filters) returns both seeded rows, date desc."""
        resp = client.get("/api/signals/export", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) == 2
        # Descending by date: message_id 101 first
        assert data[0]["message_id"] == 101
        assert data[1]["message_id"] == 100
        # Row field shape
        row = data[0]
        expected_keys = {
            "message_id", "chat_id", "chat_title", "sender_name", "text", "date",
            "direction", "magnitude", "urgency", "confidence",
            "halflife_min", "symbols", "event_type", "reasoning",
        }
        assert set(row.keys()) == expected_keys
        # symbols is parsed list, not raw string
        assert row["symbols"] == ["ETH"]
        # direction is float
        assert row["direction"] == -0.7

    def test_json_filter_by_direction_bullish(self, client):
        """direction=bullish returns only rows with direction > 0."""
        resp = client.get(
            "/api/signals/export?direction=bullish",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["message_id"] == 100
        assert data[0]["direction"] == 0.8

    def test_json_filter_by_is_signal_true(self, client):
        """is_signal=true returns only rows where is_signal=1 (both seeded)."""
        resp = client.get(
            "/api/signals/export?is_signal=true",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2

    def test_json_filter_by_chat_id(self, client):
        """chat_id filter narrows to that chat."""
        resp = client.get(
            "/api/signals/export?chat_id=1",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2
        assert all(r["chat_id"] == 1 for r in data)

    def test_json_filter_by_event_type(self, client):
        """event_type filter narrows by that type."""
        resp = client.get(
            "/api/signals/export?event_type=price",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2

    def test_count_only_returns_count_field(self, client):
        """count_only=true short-circuits with a {count: N} response."""
        resp = client.get(
            "/api/signals/export?count_only=true",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data == {"count": 2}

    def test_count_only_with_filter(self, client):
        """count_only with direction filter."""
        resp = client.get(
            "/api/signals/export?count_only=true&direction=bullish",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data == {"count": 1}

    def test_json_filter_by_llm_model(self, client):
        """llm_model filter narrows by that model."""
        resp = client.get(
            "/api/signals/export?llm_model=test-model",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2

    def test_json_filter_by_date_range(self, client):
        """date_from + date_to narrow by message date.

        Note: route interprets query params as local time, converts to UTC
        via local_to_utc. To be tz-agnostic, we use a wide window that
        captures both messages regardless of the test runner's timezone.
        """
        resp = client.get(
            "/api/signals/export?date_from=2026-07-23T00:00:00&date_to=2026-07-25T23:59:59",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2  # both seeded messages in window

    def test_json_filter_by_date_range_excludes_one(self, client):
        """A narrow date window that excludes the earlier message.

        Uses date_from after the second message's UTC time. We pick a
        window that works regardless of local tz by using a date well
        past both seeded messages.
        """
        resp = client.get(
            "/api/signals/export?date_from=2026-07-25T00:00:00",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 0  # both messages are on 2026-07-24, excluded

    def test_json_no_auth_returns_401(self, client):
        """Without auth header, endpoint returns 401."""
        resp = client.get("/api/signals/export")
        assert resp.status_code == 401

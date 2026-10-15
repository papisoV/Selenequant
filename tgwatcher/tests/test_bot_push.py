"""Tests for BotPusher — signal→Telegram Bot push dispatcher.

Verifies:
- format_signal uses codenames (标的X codes, no real coin names)
- dispatch sends to all chat_ids (mocked HTTP)
- get_status returns correct snapshot
- update_config changes enabled/chat_ids at runtime
- send_test sends formatted test message
- Disabled by default when no config
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from tgwatcher.bot_push import BotPusher


def _make_config(enabled=True, token="123:ABC", chat_ids=None):
    return {
        "bot": {
            "enabled": enabled,
            "token": token,
            "chat_ids": chat_ids or [123456, -100789],
            "parse_mode": "HTML",
            "timeout_seconds": 5,
        }
    }


def _sample_payload():
    return {
        "message_id": 42,
        "chat_id": 999,
        "chat_title": "Crypto News",
        "text": "BTC暴跌，ETH跟跌",
        "direction": -0.8,
        "magnitude": 0.7,
        "urgency": 0.6,
        "confidence": 0.9,
        "halflife_min": 60,
        "symbols": ["BTC", "ETH"],
        "event_type": "market",
        "reasoning": "BTC跌破关键支撑，ETH跟随下跌",
        "signal_score": -0.8 * 0.7 * 0.9 * (0.5 + 0.5 * 0.6),
        "date": "2026-07-30T12:00:00+00:00",
    }


class TestFormatSignal:
    def test_uses_codenames_not_real_names(self):
        pusher = BotPusher(_make_config())
        payload = _sample_payload()
        text = pusher.format_signal(payload)
        # Must contain codenames (标的X), not real coin names
        assert "标的" in text
        assert "BTC" not in text
        assert "ETH" not in text

    def test_direction_arrow_bearish(self):
        pusher = BotPusher(_make_config())
        payload = _sample_payload()
        payload["direction"] = -0.8
        text = pusher.format_signal(payload)
        assert "利空" in text

    def test_direction_arrow_bullish(self):
        pusher = BotPusher(_make_config())
        payload = _sample_payload()
        payload["direction"] = 0.8
        text = pusher.format_signal(payload)
        assert "利多" in text

    def test_direction_arrow_neutral(self):
        pusher = BotPusher(_make_config())
        payload = _sample_payload()
        payload["direction"] = 0.1
        text = pusher.format_signal(payload)
        assert "中性" in text

    def test_wildcard_symbol_shows_全市场(self):
        pusher = BotPusher(_make_config())
        payload = _sample_payload()
        payload["symbols"] = ["*"]
        text = pusher.format_signal(payload)
        assert "全市场" in text

    def test_event_type_labels(self):
        pusher = BotPusher(_make_config())
        for etype, label in [
            ("security", "安全"), ("regulatory", "监管"), ("macro", "宏观"),
            ("whale", "鲸鱼"), ("market", "市场"), ("listing", "上币"),
            ("partnership", "合作"), ("other", "其他"),
        ]:
            payload = _sample_payload()
            payload["event_type"] = etype
            text = pusher.format_signal(payload)
            assert label in text, f"Event type {etype} should contain {label}"

    def test_reasoning_is_codenamed(self):
        pusher = BotPusher(_make_config())
        payload = _sample_payload()
        text = pusher.format_signal(payload)
        # reasoning mentions BTC — must be replaced
        assert "BTC" not in text
        assert "标的" in text


class TestDispatch:
    @patch("tgwatcher.bot_push.requests.post")
    def test_dispatch_sends_to_all_chats(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        pusher = BotPusher(_make_config(chat_ids=[111, 222]))
        pusher.dispatch(_sample_payload())
        # Give ThreadPoolExecutor time to submit
        import time; time.sleep(0.5)
        assert mock_post.call_count == 2

    @patch("tgwatcher.bot_push.requests.post")
    def test_dispatch_skips_when_disabled(self, mock_post):
        pusher = BotPusher({"bot": {"enabled": False}})
        pusher.dispatch(_sample_payload())
        import time; time.sleep(0.3)
        mock_post.assert_not_called()


class TestGetStatus:
    def test_returns_enabled_and_chat_ids(self):
        pusher = BotPusher(_make_config(chat_ids=[123, 456]))
        status = pusher.get_status()
        assert status["enabled"] is True
        assert status["chat_ids"] == [123, 456]
        assert status["success_count"] == 0
        assert status["fail_count"] == 0

    def test_disabled_when_no_config(self):
        pusher = BotPusher({})
        status = pusher.get_status()
        assert status["enabled"] is False


class TestUpdateConfig:
    def test_update_enabled(self):
        pusher = BotPusher(_make_config(enabled=False))
        assert not pusher.enabled
        pusher.update_config(enabled=True)
        # Still won't be fully enabled without token+chats, but internal flag set
        status = pusher.get_status()
        # enabled is False because no token/chats in disabled config
        # but the internal _enabled flag should be True
        assert pusher._enabled is True

    def test_update_chat_ids(self):
        pusher = BotPusher(_make_config(chat_ids=[1]))
        pusher.update_config(chat_ids=[10, 20, 30])
        status = pusher.get_status()
        assert status["chat_ids"] == [10, 20, 30]


class TestSendTest:
    @patch("tgwatcher.bot_push.requests.post")
    def test_send_test_uses_codenames(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        pusher = BotPusher(_make_config(chat_ids=[123]))
        result = pusher.send_test()
        assert result["status"] == "sent"
        # Verify the text sent contains codenames
        call_args = mock_post.call_args
        sent_text = call_args[1]["json"]["text"] if "json" in call_args[1] else ""
        # BTC and ETH in test payload should be codenamed
        assert "标的" in sent_text
        assert "BTC" not in sent_text
        assert "ETH" not in sent_text

    def test_send_test_disabled(self):
        pusher = BotPusher({"bot": {"enabled": False}})
        result = pusher.send_test()
        assert result["status"] == "disabled"

"""Tests for codename_map (合规代号) and digest (市场摘要) — tgwatcher absorption Step 2."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "tg-ingest"))


# ---------- codename_map ----------

def test_codename_map_stable_and_decode(tmp_path):
    from selene_space_tg_ingest.codename_map import CodenameMap

    cm = CodenameMap(map_file=tmp_path / "cm.json")
    code1 = cm.get_code("BTC")
    code2 = cm.get_code("BTC")
    assert code1 == code2          # same coin → same code
    assert code1.startswith("标的")
    assert cm.decode(code1) == "BTC"
    # different coin → next code
    assert cm.get_code("ETH") != code1


def test_codename_map_persists(tmp_path):
    from selene_space_tg_ingest.codename_map import CodenameMap

    f = tmp_path / "cm.json"
    CodenameMap(map_file=f).get_code("BTC")
    # reload from disk — mapping survives
    cm2 = CodenameMap(map_file=f)
    assert cm2.decode(cm2.get_code("BTC")) == "BTC"


def test_codename_map_replace_names_cjk_safe(tmp_path):
    from selene_space_tg_ingest.codename_map import CodenameMap

    cm = CodenameMap(map_file=tmp_path / "cm.json")
    cm.get_code("BTC")
    # real coin name replaced with 标的X, CJK context preserved (暴跌 retained)
    out = cm.replace_names("BTC暴跌")
    assert "BTC" not in out
    assert "标的" in out and "暴跌" in out
    # partial-name safety: XYZBTC should NOT be replaced
    assert cm.replace_names("XYZBTC") == "XYZBTC"


def test_codename_map_module_singleton():
    from selene_space_tg_ingest.codename_map import codename_map

    assert codename_map.get_code("BTC").startswith("标的")


# ---------- digest: window computation ----------

def _make_storage(tmp_path):
    from selene_space_tg_ingest.storage import Storage
    s = Storage(db_path=tmp_path / "tgwatcher.db")
    s.init_db()
    return s


def test_compute_window_cold_start(tmp_path):
    from selene_space_tg_ingest import digest

    s = _make_storage(tmp_path)
    from_at, to_at = digest._compute_window(s)
    hours = (to_at - from_at).total_seconds() / 3600
    assert hours == digest.COLD_START_HOURS


def test_compute_window_lookback_cap(tmp_path):
    from selene_space_tg_ingest import digest

    s = _make_storage(tmp_path)
    # 999h requested → capped at MAX_LOOKBACK_HOURS (720h)
    from_at, to_at = digest._compute_window(s, lookback_hours=999)
    hours = (to_at - from_at).total_seconds() / 3600
    assert hours == digest.MAX_LOOKBACK_HOURS
    # min floor of 1h
    from_at, to_at = digest._compute_window(s, lookback_hours=0)
    assert (to_at - from_at).total_seconds() / 3600 == 1.0


# ---------- digest: signal fetch + aggregation ----------

def _seed_signals(s, n=6, base=datetime(2026, 8, 10, 0, 0, 0)):
    from selene_space_tg_ingest.models import Message, SignalFactor

    with s.get_session() as sess:
        for i in range(n):
            msg = Message(
                message_id=1000 + i, chat_id=1001,
                text=f"signal {i}", date=base - timedelta(hours=i),
            )
            sess.add(msg)
            sess.flush()
            sess.add(SignalFactor(
                message_id=msg.message_id, chat_id=1001,
                is_signal=True, direction=1.0 if i % 2 == 0 else -1.0,
                magnitude=0.5, urgency=0.3, confidence=0.8,
                halflife_min=60, symbols='["BTC"]',
                event_type="event_whale", reasoning=f"reason {i}",
                llm_status="completed",
            ))
        sess.commit()


def test_fetch_signals_joins_and_filters(tmp_path):
    from selene_space_tg_ingest import digest

    s = _make_storage(tmp_path)
    _seed_signals(s, n=6)
    now = datetime(2026, 8, 10, 6, 0, 0)
    rows = digest._fetch_signals(s, now - timedelta(hours=24), now)
    assert len(rows) == 6
    assert rows[0]["message_date"]  # JOIN pulled m.date
    assert rows[0]["symbols"] == '["BTC"]'


def test_fetch_signals_excludes_non_signal(tmp_path):
    from selene_space_tg_ingest.models import Message, SignalFactor
    from selene_space_tg_ingest import digest

    s = _make_storage(tmp_path)
    now = datetime(2026, 8, 10, 6, 0, 0)
    with s.get_session() as sess:
        msg = Message(message_id=500, chat_id=1001, text="nope", date=now - timedelta(hours=1))
        sess.add(msg)
        sess.flush()
        # is_signal=0 and llm_status pending → must be excluded
        sess.add(SignalFactor(message_id=msg.message_id, chat_id=1001,
                              is_signal=False, direction=0.0, llm_status="pending"))
        sess.commit()
    assert digest._fetch_signals(s, now - timedelta(hours=24), now) == []


def test_aggregate_half_life_weighting(tmp_path):
    from selene_space_tg_ingest import digest

    s = _make_storage(tmp_path)
    _seed_signals(s, n=6)
    now = datetime(2026, 8, 10, 6, 0, 0)
    rows = digest._fetch_signals(s, now - timedelta(hours=24), now)
    agg = digest._aggregate(rows, now - timedelta(hours=24), now)
    assert agg["empty"] is False
    assert agg["total"] == 6
    assert -1.0 <= agg["net_direction"] <= 1.0
    assert "event_whale" in agg["event_type_breakdown"]
    assert "BTC" in agg["symbols_breakdown"]


def test_aggregate_neutral_direction_not_diluted():
    """US-007: net_direction only sums direction!=0 rows — a neutral event
    (direction=0) must NOT pull the direction statistic toward zero."""
    from selene_space_tg_ingest import digest

    now = datetime(2026, 8, 10, 6, 0, 0)
    msg_date = now - timedelta(hours=1)
    rows = [
        {"message_id": 1, "direction": 1.0, "magnitude": 0.5, "urgency": 0.3,
         "confidence": 0.8, "halflife_min": 60, "symbols": '["BTC"]',
         "event_type": "security", "reasoning": "bullish", "created_at": now,
         "message_date": msg_date},
        {"message_id": 2, "direction": 0.0, "magnitude": 0.7, "urgency": 0.5,
         "confidence": 0.9, "halflife_min": 60, "symbols": '["BTC"]',
         "event_type": "security", "reasoning": "neutral event", "created_at": now,
         "message_date": msg_date},
    ]
    agg = digest._aggregate(rows, now - timedelta(hours=24), now)
    # Diluting the neutral in would give ~0.5; signed-only keeps the single
    # signed signal's value (+1.0). The neutral is still counted as a signal.
    assert abs(agg["net_direction"] - 1.0) < 1e-9
    assert agg["total"] == 2


# ---------- digest: generate + persist ----------

class FakeLLM:
    """Mimics SignalLLMClient._call_llm — returns prose that leaks a real coin name."""

    def __init__(self, text="BTC 强势突破，ETH 跟随上涨，市场情绪偏多。"):
        self._text = text
        self.calls = []

    def _call_llm(self, prompt, max_tokens_override=None, json_mode=True):
        self.calls.append({"prompt": prompt, "max_tokens_override": max_tokens_override, "json_mode": json_mode})
        return self._text


def test_generate_digest_stub_below_threshold(tmp_path):
    from selene_space_tg_ingest import digest

    s = _make_storage(tmp_path)
    # 0 signals → stub, no LLM needed
    result = digest.generate_digest(s, llm=None, lookback_hours=24)
    assert result.signal_count == 0
    assert result.summary and "无显著变化" in result.summary
    assert digest.get_latest_digest(s).id == result.id
    assert len(digest.list_digests(s)) == 1


def test_generate_digest_llm_and_codename_enforcement(tmp_path):
    from selene_space_tg_ingest import digest

    s = _make_storage(tmp_path)
    _seed_signals(s, n=6, base=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1))
    llm = FakeLLM()
    result = digest.generate_digest(s, llm=llm, lookback_hours=24)
    assert result.signal_count == 6
    assert result.summary
    # LLM called once with json_mode=False, max_tokens_override=1024
    assert len(llm.calls) == 1
    assert llm.calls[0]["json_mode"] is False
    assert llm.calls[0]["max_tokens_override"] == 1024
    # codename enforcement: no real coin names leaked
    assert "BTC" not in result.summary
    assert "ETH" not in result.summary
    assert "标的" in result.summary


def test_generate_digest_incremental_window_floors(tmp_path):
    from selene_space_tg_ingest import digest

    s = _make_storage(tmp_path)
    _seed_signals(s, n=6)
    r1 = digest.generate_digest(s, llm=FakeLLM(), lookback_hours=24)
    # incremental (no lookback): from_at == last digest's to_at
    from_at, _ = digest._compute_window(s)
    assert from_at == r1.to_at

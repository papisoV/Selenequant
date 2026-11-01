"""Behavior locks for polymarket pure functions.

These tests pin the CURRENT behavior of the module-level pure helpers in
``app.api.v1.polymarket`` before their expressions are rewritten. They were
written against the pre-rewrite implementation; they must stay green through
the rewrite (see the backend rewrite plan, 2026-10-05).

Scope: pure functions only — no network, no FastAPI app, no DB.
Route-shape tests need a stubbed ``_poly_client`` and live in a separate file.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.api.v1.polymarket import (
    _build_current_window_slugs,
    _detect_asset,
    _detect_category,
    _detect_timeframe,
    _event_to_prediction,
    _extract_outcomes_from_market,
    _extract_window_from_slug,
    _translate_title,
)

# ---------------------------------------------------------------------------
# _detect_category — scores title hits 3x, description hits 1x, ties → max
# ---------------------------------------------------------------------------


def test_detect_category_crypto_from_title() -> None:
    assert _detect_category("Will Bitcoin hit $150k by December?") == "crypto"


def test_detect_category_politics_from_title() -> None:
    assert _detect_category("Presidential Election Winner 2028") == "politics"


def test_detect_category_description_only_lower_weight() -> None:
    # Title has no rule hit; description alone still scores (weight 1).
    assert _detect_category("Will it happen?", "Bitcoin related market") == "crypto"


def test_detect_category_title_beats_description() -> None:
    # Title hits (weight 3) must outrank description-only hits (weight 1).
    cat = _detect_category("NBA Champion 2026", "Some bitcoin mention here")
    assert cat == "sports"


def test_detect_category_no_match_is_other() -> None:
    assert _detect_category("Totally unrelated zzzqqq") == "other"


# ---------------------------------------------------------------------------
# _translate_title — sequential regex substitution over _ZH_TERM_MAP
# ---------------------------------------------------------------------------


def test_translate_title_maps_known_terms() -> None:
    assert _translate_title("Bitcoin price") == "比特币 price"


def test_translate_title_multiword_phrase_wins() -> None:
    # "Presidential Election Winner" must beat the bare "Presidential Election".
    assert _translate_title("Presidential Election Winner") == "总统大选获胜者"


def test_translate_title_negative_lookahead_march() -> None:
    # "March Madness" must NOT be turned into "三月 Madness".
    assert "疯狂三月" in _translate_title("March Madness")


def test_translate_title_untouched_when_no_terms() -> None:
    assert _translate_title("zzz qqq") == "zzz qqq"


# ---------------------------------------------------------------------------
# _extract_outcomes_from_market — parses JSON-encoded Gamma fields
# ---------------------------------------------------------------------------


def test_extract_outcomes_json_encoded_lists() -> None:
    out = _extract_outcomes_from_market(
        {
            "clobTokenIds": '["tok-a", "tok-b"]',
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.62", "0.38"]',
        }
    )
    assert [o["token_id"] for o in out] == ["tok-a", "tok-b"]
    assert [o["outcome"] for o in out] == ["Yes", "No"]
    assert out[0]["price"] == pytest.approx(0.62)
    assert out[1]["price"] == pytest.approx(0.38)


def test_extract_outcomes_missing_prices_default_zero() -> None:
    out = _extract_outcomes_from_market(
        {"clobTokenIds": '["t1"]', "outcomes": '["Yes"]'}
    )
    assert len(out) == 1
    assert out[0]["price"] == 0.0


def test_extract_outcomes_empty_market_returns_empty() -> None:
    assert _extract_outcomes_from_market({}) == []


def test_extract_outcomes_extra_outcomes_named_generically() -> None:
    # More token ids than outcome names → fallback "Outcome N".
    out = _extract_outcomes_from_market(
        {"clobTokenIds": '["a", "b"]', "outcomes": '["Yes"]'}
    )
    assert out[1]["outcome"] == "Outcome 1"


# ---------------------------------------------------------------------------
# _detect_asset — first matching pattern wins
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "code"),
    [
        ("Bitcoin Up or Down", "BTC"),
        ("Ethereum Up or Down", "ETH"),
        ("Solana Up or Down", "SOL"),
        ("Hyperliquid Up or Down", "HYPE"),
        ("Some other asset", "OTHER"),
    ],
)
def test_detect_asset(title: str, code: str) -> None:
    got, _zh = _detect_asset(title)
    assert got == code


# ---------------------------------------------------------------------------
# _detect_timeframe — slug checked before title
# ---------------------------------------------------------------------------


def test_detect_timeframe_from_slug_5m() -> None:
    assert _detect_timeframe("btc-updown-5m-1774510200", "Bitcoin Up or Down") == "5m"


def test_detect_timeframe_from_slug_15m() -> None:
    assert _detect_timeframe("eth-updown-15m-1774510200", "") == "15m"


def test_detect_timeframe_unknown_is_other() -> None:
    assert _detect_timeframe("no-tf-here", "no tf here") == "other"


# ---------------------------------------------------------------------------
# _extract_window_from_slug — timestamp from slug, duration from tf table
# ---------------------------------------------------------------------------


def test_extract_window_from_slug_with_known_tf() -> None:
    start, end = _extract_window_from_slug("btc-updown-5m-1774510200", "5m")
    assert start is not None and end is not None
    assert start == datetime.fromtimestamp(1774510200, tz=UTC)
    assert (end - start).total_seconds() == 300


def test_extract_window_from_slug_unknown_tf_has_no_end() -> None:
    start, end = _extract_window_from_slug("btc-updown-5m-1774510200", "other")
    assert start is not None
    assert end is None


def test_extract_window_from_slug_no_timestamp() -> None:
    assert _extract_window_from_slug("no-timestamp-here", "5m") == (None, None)


# ---------------------------------------------------------------------------
# _event_to_prediction — returns None unless title matches Up/Down
# ---------------------------------------------------------------------------


def test_event_to_prediction_rejects_non_updown() -> None:
    assert _event_to_prediction({"title": "Something else entirely"}) is None


def test_event_to_prediction_shapes_updown_event() -> None:
    ev = {
        "id": "42",
        "title": "Bitcoin Up or Down",
        "slug": "btc-updown-5m-1774510200",
        "volume": "1000.5",
        "markets": [
            {
                "clobTokenIds": '["t1"]',
                "outcomes": '["Up"]',
                "outcomePrices": '["0.55"]',
            }
        ],
    }
    out = _event_to_prediction(ev)
    assert out is not None
    assert out["id"] == "42"
    assert out["asset"] == "BTC"
    assert out["timeframe"] == "5m"
    assert out["title_zh"] == "比特币 Up or Down"
    assert out["volume"] == pytest.approx(1000.5)
    assert (
        out["polymarket_url"] == "https://polymarket.com/event/btc-updown-5m-1774510200"
    )
    assert len(out["outcomes"]) == 1
    assert out["outcomes"][0]["token_id"] == "t1"


# ---------------------------------------------------------------------------
# _build_current_window_slugs — asset × rolling timeframe grid
# ---------------------------------------------------------------------------


def test_build_current_window_slugs_grid_shape() -> None:
    now = datetime.fromtimestamp(1774510200, tz=UTC)
    slugs = _build_current_window_slugs(now)
    # 7 assets × 2 rolling timeframes
    assert len(slugs) == 14
    tfs = {tf for _s, tf, _t in slugs}
    assert tfs == {"5m", "15m"}


def test_build_current_window_slugs_aligns_to_bucket() -> None:
    now = datetime.fromtimestamp(1774510207, tz=UTC)  # 7s into a 5m bucket
    slugs = _build_current_window_slugs(now)
    for slug, tf, start_ts in slugs:
        assert start_ts % (300 if tf == "5m" else 900) == 0
        assert slug.endswith(str(start_ts))


def test_build_current_window_slugs_deterministic() -> None:
    now = datetime.fromtimestamp(1774510200, tz=UTC)
    assert _build_current_window_slugs(now) == _build_current_window_slugs(now)

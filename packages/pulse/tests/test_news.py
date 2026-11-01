from __future__ import annotations


from ai_market_pulse import news
from ai_market_pulse.config import NewsSettings
from ai_market_pulse.models import Asset

_SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Google News</title>
    <item>
      <title>Acme Corp beats earnings &amp; raises guidance</title>
      <link>https://news.example/acme-earnings</link>
      <source url="https://example.com">Example Wire</source>
      <pubDate>Mon, 07 Jul 2026 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Regulators review Acme &lt;merger&gt; deal</title>
      <link>https://news.example/acme-merger</link>
      <source url="https://example.com">Another Source</source>
      <pubDate>Mon, 07 Jul 2026 09:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Acme announces new product line</title>
      <link>https://news.example/acme-product</link>
      <source url="https://example.com">Third Source</source>
      <pubDate>Sun, 06 Jul 2026 15:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def _asset() -> Asset:
    return Asset(symbol="ACME", name="Acme Corp")


def _settings(**overrides: object) -> NewsSettings:
    base = NewsSettings()
    return NewsSettings(
        enabled=overrides.get("enabled", base.enabled),
        items_per_asset=overrides.get("items_per_asset", base.items_per_asset),
        language=overrides.get("language", base.language),
        region=overrides.get("region", base.region),
        lookback_days=overrides.get("lookback_days", base.lookback_days),
    )


def test_fetch_news_parses_items_with_html_unescape(monkeypatch) -> None:
    monkeypatch.setattr(
        news.urllib.request, "urlopen", lambda request, timeout: _FakeResponse(_SAMPLE_FEED.encode("utf-8"))
    )

    items = news.fetch_news(_asset(), _settings())

    assert len(items) == 3
    first = items[0]
    assert first.title == "Acme Corp beats earnings & raises guidance"
    assert first.link == "https://news.example/acme-earnings"
    assert first.source == "Example Wire"
    assert first.published == "Mon, 07 Jul 2026 12:00:00 GMT"

    second = items[1]
    assert second.title == "Regulators review Acme <merger> deal"


def test_fetch_news_respects_items_per_asset_cap(monkeypatch) -> None:
    monkeypatch.setattr(
        news.urllib.request, "urlopen", lambda request, timeout: _FakeResponse(_SAMPLE_FEED.encode("utf-8"))
    )

    items = news.fetch_news(_asset(), _settings(items_per_asset=2))

    assert len(items) == 2
    assert items[0].link == "https://news.example/acme-earnings"
    assert items[1].link == "https://news.example/acme-merger"


def test_fetch_news_returns_empty_list_when_disabled(monkeypatch) -> None:
    called = False

    def fake_urlopen(request, timeout):
        nonlocal called
        called = True
        return _FakeResponse(_SAMPLE_FEED.encode("utf-8"))

    monkeypatch.setattr(news.urllib.request, "urlopen", fake_urlopen)

    items = news.fetch_news(_asset(), _settings(enabled=False))

    assert items == []
    assert called is False


def test_fetch_news_returns_empty_list_on_network_error(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise OSError("network unreachable")

    monkeypatch.setattr(news.urllib.request, "urlopen", fake_urlopen)

    items = news.fetch_news(_asset(), _settings())

    assert items == []


def test_fetch_news_returns_empty_list_on_malformed_xml(monkeypatch) -> None:
    monkeypatch.setattr(
        news.urllib.request, "urlopen", lambda request, timeout: _FakeResponse(b"<rss><channel>")
    )

    items = news.fetch_news(_asset(), _settings())

    assert items == []


def test_fetch_news_skips_items_without_title(monkeypatch) -> None:
    payload = """<?xml version="1.0"?>
    <rss><channel>
      <item>
        <link>https://news.example/no-title</link>
        <pubDate>Mon, 07 Jul 2026 12:00:00 GMT</pubDate>
      </item>
      <item>
        <title>Has a title</title>
        <link>https://news.example/has-title</link>
      </item>
    </channel></rss>
    """
    monkeypatch.setattr(
        news.urllib.request, "urlopen", lambda request, timeout: _FakeResponse(payload.encode("utf-8"))
    )

    items = news.fetch_news(_asset(), _settings())

    assert len(items) == 1
    assert items[0].title == "Has a title"
    assert items[0].source is None


def test_fetch_news_builds_google_news_rss_url_with_asset_query(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return _FakeResponse(_SAMPLE_FEED.encode("utf-8"))

    monkeypatch.setattr(news.urllib.request, "urlopen", fake_urlopen)

    news.fetch_news(_asset(), _settings(language="en-US", region="US", lookback_days=3))

    url = captured["url"]
    assert isinstance(url, str)
    assert url.startswith("https://news.google.com/rss/search?")
    assert "hl=en-US" in url
    assert "gl=US" in url
    assert "ceid=US:en" in url
    assert "ACME" in url
    assert "when%3A3d" in url
    assert captured["timeout"] == 12

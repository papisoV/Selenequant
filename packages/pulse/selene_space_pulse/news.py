from __future__ import annotations

import html
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from selene_space_stock.config import NewsSettings
from selene_space_types.stock import NewsItem, StockAsset


def fetch_news(asset: StockAsset, settings: NewsSettings) -> list[NewsItem]:
    if not settings.enabled:
        return []

    query_parts = [asset.symbol]
    if asset.name and asset.name != asset.symbol:
        query_parts.append(asset.name)
    query_parts.extend(["stock", f"when:{settings.lookback_days}d"])
    query = urllib.parse.quote(" ".join(query_parts))
    language = settings.language or "en-US"
    region = settings.region or "US"
    url = (
        "https://news.google.com/rss/search?"
        f"q={query}&hl={urllib.parse.quote(language)}&gl={urllib.parse.quote(region)}"
        f"&ceid={urllib.parse.quote(region)}:{urllib.parse.quote(language.split('-')[0])}"
    )

    request = urllib.request.Request(url, headers={"User-Agent": "selene-space-pulse/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = response.read()
    except Exception:
        return []

    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []

    items: list[NewsItem] = []
    for item in root.findall("./channel/item")[: settings.items_per_asset]:
        title = _text(item, "title")
        link = _text(item, "link")
        source = _text(item, "source")
        published = _text(item, "pubDate")
        if title:
            items.append(
                NewsItem(
                    title=html.unescape(title),
                    link=link or "",
                    source=html.unescape(source) if source else None,
                    published=published,
                )
            )
    return items


def _text(parent: ET.Element, tag: str) -> str | None:
    element = parent.find(tag)
    if element is None or element.text is None:
        return None
    return element.text.strip()

"""RSS news fetcher — normalize feed entries into ParsedMessage dicts.

Feeds Coindesk / CoinTelegraph / CryptoSlate RSS into the existing
SignalEngine.process_message() pipeline. Uses synthetic negative
chat_id per source (-1/-2/-3) to avoid colliding with real TG chat IDs.

Does NOT re-implement LLM logic — reuses SignalEngine.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsSource:
    name: str
    url: str
    chat_id: int  # Negative synthetic ID


_DEFAULT_SOURCES: tuple[NewsSource, ...] = (
    NewsSource(name="coindesk", url="https://www.coindesk.com/arc/outboundfeeds/rss/", chat_id=-1),
    NewsSource(name="cointelegraph", url="https://cointelegraph.com/rss", chat_id=-2),
    NewsSource(name="cryptoslate", url="https://cryptoslate.com/feed/", chat_id=-3),
)


@dataclass
class NewsConfig:
    enabled: bool = False
    poll_interval: int = 300  # seconds between polls
    sources: tuple[NewsSource, ...] = _DEFAULT_SOURCES
    min_text_length: int = 10  # passed to filter via SignalEngine config


class NewsFetcher:
    """Poll RSS feeds and normalize entries for SignalEngine consumption."""

    def __init__(
        self,
        config: NewsConfig,
        on_signal_factor: Callable[[dict], None] | None = None,
    ) -> None:
        self._config = config
        self._on_signal_factor = on_signal_factor
        self._seen_ids: set[int] = set()  # dedupe by message_id hash
        try:
            import feedparser  # type: ignore
            self._feedparser = feedparser
        except ImportError as e:
            raise ImportError(
                "feedparser is required: pip install feedparser>=6.0"
            ) from e

    @property
    def name(self) -> str:
        return "news_fetcher"

    def fetch_once(self, source: NewsSource) -> list[dict]:
        """Fetch one feed, return list of normalized message dicts.

        Each dict matches SignalEngine.process_message() expected shape:
        {message_id, chat_id, text, date}
        """
        try:
            parsed = self._feedparser.parse(source.url)
        except Exception as e:
            logger.warning("fetch_once: feedparser failed for %s: %s", source.name, e)
            return []

        out: list[dict] = []
        for entry in getattr(parsed, "entries", []) or []:
            msg = self._normalize_entry(entry, source)
            if msg is None:
                continue
            msg_id = msg["message_id"]
            if msg_id in self._seen_ids:
                continue
            self._seen_ids.add(msg_id)
            out.append(msg)
        return out

    def _normalize_entry(self, entry: Any, source: NewsSource) -> dict | None:
        """Convert feedparser entry to message dict.

        message_id = stable hash of entry.link (so re-fetches dedupe)
        chat_id = source.chat_id (synthetic negative)
        text = entry.title + " " + entry.summary (LLM gets full context)
        date = entry.published_parsed → datetime
        """
        link = getattr(entry, "link", None) or getattr(entry, "id", None)
        if not link:
            return None
        title = getattr(entry, "title", "") or ""
        summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
        text = f"{title} {summary}".strip()
        if not text:
            return None

        # Stable hash of link → int (positive, fits in INTEGER)
        h = hashlib.md5(link.encode("utf-8")).hexdigest()
        message_id = int(h[:12], 16)  # 48-bit, well within int64

        date_str = getattr(entry, "published", None) or getattr(entry, "updated", None)
        date = None
        if date_str:
            try:
                # feedparser provides published_parsed struct_time
                pt = getattr(entry, "published_parsed", None)
                if pt:
                    date = time.strftime("%Y-%m-%d %H:%M:%S", pt)
            except Exception:
                pass

        return {
            "message_id": message_id,
            "chat_id": source.chat_id,
            "text": text,
            "date": date,
            "source": source.name,
            "link": link,
        }

    def run_forever(self, process_message_fn: Callable[[dict], dict | None]) -> None:
        """Poll loop. process_message_fn typically = SignalEngine.process_message."""
        if not self._config.enabled:
            logger.info("NewsFetcher disabled (config.enabled=False), exiting")
            return

        logger.info(
            "NewsFetcher starting: %d sources, poll every %ds",
            len(self._config.sources), self._config.poll_interval,
        )
        while True:
            for source in self._config.sources:
                msgs = self.fetch_once(source)
                for msg in msgs:
                    try:
                        result = process_message_fn(msg)
                        if result and self._on_signal_factor:
                            self._on_signal_factor(result)
                    except Exception as e:
                        logger.warning(
                            "NewsFetcher: process_message failed for %s entry: %s",
                            source.name, e,
                        )
            time.sleep(self._config.poll_interval)


def load_news_config_from_env() -> NewsConfig:
    """Load NewsConfig from env vars. Mirrors tg-ingest hybrid env+yaml pattern."""
    import os

    enabled = os.environ.get("SELENE_NEWS_ENABLED", "").lower() in ("1", "true", "yes")
    poll_interval = int(os.environ.get("SELENE_NEWS_POLL_INTERVAL", "300"))
    return NewsConfig(
        enabled=enabled,
        poll_interval=poll_interval,
        sources=_DEFAULT_SOURCES,
    )

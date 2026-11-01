import asyncio
import logging
import random
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat

from tgwatcher.parsers import parse_telethon_message
from tgwatcher.schemas import ParsedMessage

logger = logging.getLogger(__name__)


class TGClient:
    def __init__(self, config: dict, loop: asyncio.AbstractEventLoop | None = None):
        tg = config["telegram"]
        proxy_cfg = config["proxy"]

        self.api_id = tg["api_id"]
        self.api_hash = tg["api_hash"]
        self.phone = tg["phone"]
        self.session_dir = Path(tg.get("session_dir", "./sessions"))
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._loop = loop

        self.proxy = None
        if proxy_cfg.get("enabled", False):
            self.proxy = (
                proxy_cfg.get("protocol", "socks5"),
                proxy_cfg["host"],
                proxy_cfg["port"],
            )

        crawl = config.get("crawl", {})
        self.min_delay = crawl.get("min_delay", 1)
        self.max_delay = crawl.get("max_delay", 3)

        self.client: TelegramClient | None = None

    def _session_path(self) -> str:
        safe_phone = self.phone.replace("+", "")
        return str(self.session_dir / f"tgwatcher_{safe_phone}")

    async def connect(self) -> None:
        from tgwatcher.tg_session import WALSQLiteSession
        self.client = TelegramClient(
            session=WALSQLiteSession(self._session_path()),
            api_id=self.api_id,
            api_hash=self.api_hash,
            proxy=self.proxy,
            loop=self._loop,
        )
        await self.client.start(phone=self.phone)
        me = await self.client.get_me()
        logger.info("Logged in as %s (ID: %s)", me.first_name, me.id)

    async def disconnect(self) -> None:
        if self.client and self.client.is_connected():
            await self.client.disconnect()

    async def list_dialogs(self) -> list[dict]:
        if not self.client:
            raise RuntimeError("Client not connected. Call connect() first.")
        dialogs = []
        async for dialog in self.client.iter_dialogs():
            entity = dialog.entity
            if isinstance(entity, (Channel, Chat)):
                dialogs.append({
                    "id": entity.id,
                    "title": dialog.name,
                    "username": getattr(entity, "username", None),
                    "is_channel": isinstance(entity, Channel) and not entity.megagroup,
                    "is_group": isinstance(entity, Chat) or (isinstance(entity, Channel) and entity.megagroup),
                    "members": getattr(entity, "participants_count", None),
                })
        return dialogs

    async def fetch_messages(
        self,
        chat_id: int | str,
        limit: int = 100,
        min_id: int = 0,
        offset_date: datetime | None = None,
        until_date: datetime | None = None,
    ) -> list[ParsedMessage]:
        if not self.client:
            raise RuntimeError("Client not connected. Call connect() first.")

        entity = await self.client.get_entity(chat_id)

        chat_title = getattr(entity, "title", str(chat_id))

        messages: list[ParsedMessage] = []
        async for msg in self.client.iter_messages(
            entity, limit=limit, min_id=min_id, offset_date=offset_date, reverse=True
        ):
            if msg.text is None and msg.media is None:
                continue

            if until_date and msg.date:
                msg_dt = msg.date.replace(tzinfo=None) if msg.date.tzinfo else msg.date
                until_dt = until_date.replace(tzinfo=None) if until_date.tzinfo else until_date
                if msg_dt > until_dt:
                    continue

            parsed = parse_telethon_message(msg, entity)
            messages.append(parsed)

            delay = random.uniform(self.min_delay, self.max_delay)
            await asyncio.sleep(delay)

        logger.info("Fetched %d messages from %s", len(messages), chat_title)
        return messages


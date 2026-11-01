"""Real-time message listener.

Enable with: python scripts/tg_listen.py
"""
import logging
from typing import TYPE_CHECKING

from telethon import events

from selene_space_tg_ingest.parsers import parse_telethon_message
from selene_space_tg_ingest.schemas import EditUpdate
from selene_space_tg_ingest.storage import Storage

if TYPE_CHECKING:
    from telethon import TelegramClient as TGClient

logger = logging.getLogger(__name__)


async def start_listener(client: "TGClient", storage: Storage, groups: list[dict],
                          on_new_message=None, signal_engine=None) -> None:
    """Listen for new messages in real-time and save to DB.

    Args:
        on_new_message: Optional callback invoked with msg dict after saving.
                        Used to push real-time updates via SSE or other sinks.
        signal_engine: Optional SignalEngine for real-time factor extraction.
                       Its on_signal_factor hook (registered at construction)
                       will fire on each completed/skipped factor.
    """
    tg = client.client
    if tg is None:
        raise RuntimeError("Client not connected")

    chat_ids = set()
    for g in groups:
        chat_id = g.get("id") or g.get("username")
        if chat_id:
            chat_ids.add(chat_id)

    @tg.on(events.NewMessage)
    async def handler(event):
        chat_id = event.chat_id
        if chat_id not in chat_ids:
            return

        if not event.raw_text and not event.message.media:
            return

        chat = event.chat
        if chat is None:
            return

        parsed = parse_telethon_message(event.message, chat)
        storage.save_messages([parsed])
        if on_new_message:
            try:
                on_new_message(parsed.to_dict())
            except Exception:
                pass
        if signal_engine:
            try:
                signal_engine.process_new_message(parsed.to_dict())
            except Exception as e:
                logger.warning("Signal processing failed for msg %d: %s", parsed.message_id, e)
        logger.info("[LIVE] %s in %s: %s", parsed.sender_name or "Unknown", parsed.chat_title, (parsed.text or "[media]")[:50])

    @tg.on(events.MessageEdited)
    async def edit_handler(event):
        chat_id = event.chat_id
        if chat_id not in chat_ids:
            return
        update = EditUpdate(
            message_id=event.message.id,
            chat_id=chat_id,
            text=event.raw_text,
            edit_date=event.message.edit_date,
        )
        storage.update_edited_message(update)
        logger.info("[EDIT] msg %d in chat %d", event.message.id, chat_id)

    @tg.on(events.MessageDeleted)
    async def delete_handler(event):
        chat_id = getattr(event, "chat_id", None)
        if chat_id is None or chat_id not in chat_ids:
            return
        for msg_id in event.messages:
            storage.mark_message_deleted(msg_id, chat_id)
        logger.info("[DEL] %d messages in chat %d", len(event.messages), chat_id)

    logger.info("Real-time listener started for %d chats", len(chat_ids))
    await tg.run_until_disconnected()

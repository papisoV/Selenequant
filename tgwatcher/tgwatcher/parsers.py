"""Unified Telethon object parsers.

Single source of truth for converting raw Telethon message/entity objects
into typed ParsedMessage / ParsedChat dataclasses.  Both client.py and
listener.py call these functions instead of duplicating parsing logic.
"""
from __future__ import annotations


from tgwatcher.schemas import ParsedChat, ParsedMessage


def extract_media_type(media) -> str | None:
    if media is None:
        return None
    from telethon.tl.types import (
        MessageMediaContact,
        MessageMediaDocument,
        MessageMediaPhoto,
        MessageMediaWebPage,
    )
    if isinstance(media, MessageMediaPhoto):
        return "photo"
    if isinstance(media, MessageMediaDocument):
        doc = media.document
        if doc:
            from telethon.tl.types import (
                DocumentAttributeAudio,
                DocumentAttributeSticker,
                DocumentAttributeVideo,
            )
            for attr in doc.attributes:
                if isinstance(attr, DocumentAttributeVideo):
                    return "video"
                if isinstance(attr, DocumentAttributeAudio):
                    return "voice" if attr.voice else "audio"
                if isinstance(attr, DocumentAttributeSticker):
                    return "sticker"
        return "document"
    if isinstance(media, MessageMediaWebPage):
        return "webpage"
    if isinstance(media, MessageMediaContact):
        return "contact"
    return "other"


def get_chat_type(entity) -> str | None:
    from telethon.tl.types import Channel, Chat
    if isinstance(entity, Channel):
        return "megagroup" if entity.megagroup else "channel"
    if isinstance(entity, Chat):
        return "group"
    return None


def parse_telethon_entity(entity) -> ParsedChat:
    """Parse a Telethon Channel/Chat entity into ParsedChat."""
    return ParsedChat(
        chat_id=entity.id,
        chat_title=getattr(entity, "title", None),
        chat_username=getattr(entity, "username", None),
        chat_type=get_chat_type(entity),
        members=getattr(entity, "participants_count", None),
    )


def _parse_forward_from(msg) -> str | None:
    if not msg.forward:
        return None
    if msg.forward.sender:
        return (
            getattr(msg.forward.sender, "first_name", None)
            or getattr(msg.forward.sender, "title", None)
        )
    if msg.forward.chat:
        return getattr(msg.forward.chat, "title", None)
    return None


def _parse_sender(msg) -> tuple[int | None, str | None, str | None]:
    """Return (sender_id, sender_name, sender_username)."""
    sender_id = msg.sender_id
    sender_name = None
    sender_username = None
    if msg.sender:
        sender_name = (
            getattr(msg.sender, "first_name", None)
            or getattr(msg.sender, "title", None)
        )
        last = getattr(msg.sender, "last_name", None)
        if last:
            sender_name = f"{sender_name} {last}"
        sender_username = getattr(msg.sender, "username", None)
    return sender_id, sender_name, sender_username


def parse_telethon_message(msg, entity) -> ParsedMessage:
    """Parse a Telethon Message + its entity into ParsedMessage.

    Args:
        msg: A telethon.tl.custom.Message object.
        entity: The resolved Channel/Chat entity for this message.
    """
    chat_id = entity.id
    chat_title = getattr(entity, "title", str(chat_id))
    chat_username = getattr(entity, "username", None)
    chat_type = get_chat_type(entity)
    members = getattr(entity, "participants_count", None)

    sender_id, sender_name, sender_username = _parse_sender(msg)
    forward_from = _parse_forward_from(msg)
    media_type = extract_media_type(msg.media)
    media_id = (
        str(getattr(msg.media, "id", None))
        if msg.media and hasattr(msg.media, "id")
        else None
    )

    return ParsedMessage(
        message_id=msg.id,
        chat_id=chat_id,
        chat_title=chat_title,
        chat_username=chat_username,
        chat_type=chat_type,
        members=members,
        sender_id=sender_id,
        sender_name=sender_name,
        sender_username=sender_username,
        text=msg.text,
        reply_to_msg_id=msg.reply_to_msg_id if msg.is_reply else None,
        forward_from=forward_from,
        date=msg.date,
        has_media=msg.media is not None,
        edit_date=msg.edit_date,
        is_edited=msg.edit_date is not None,
        media_type=media_type,
        media_id=media_id,
    )

"""Typed dataclass contracts between all TG-ingest layers.

Every module that produces or consumes message/chat/sender data uses these
frozen dataclasses instead of raw dicts.  A typo like ``msg.edit_dat`` raises
AttributeError immediately instead of silently returning None.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ParsedChat:
    chat_id: int
    chat_title: str | None = None
    chat_username: str | None = None
    chat_type: str | None = None
    members: int | None = None

    def to_dict(self) -> dict:
        return {
            "chat_id": self.chat_id,
            "chat_title": self.chat_title,
            "chat_username": self.chat_username,
            "chat_type": self.chat_type,
            "members": self.members,
        }


@dataclass(frozen=True)
class ParsedSender:
    sender_id: int
    sender_name: str | None = None
    sender_username: str | None = None

    def to_dict(self) -> dict:
        return {
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "sender_username": self.sender_username,
        }


@dataclass(frozen=True)
class ParsedMessage:
    message_id: int
    chat_id: int
    chat_title: str | None = None
    chat_username: str | None = None
    chat_type: str | None = None
    members: int | None = None
    sender_id: int | None = None
    sender_name: str | None = None
    sender_username: str | None = None
    text: str | None = None
    reply_to_msg_id: int | None = None
    forward_from: str | None = None
    date: datetime | None = None
    has_media: bool = False
    edit_date: datetime | None = None
    is_edited: bool = False
    media_type: str | None = None
    media_id: str | None = None

    @property
    def chat(self) -> ParsedChat:
        return ParsedChat(
            chat_id=self.chat_id,
            chat_title=self.chat_title,
            chat_username=self.chat_username,
            chat_type=self.chat_type,
            members=self.members,
        )

    @property
    def sender(self) -> ParsedSender | None:
        if self.sender_id is None:
            return None
        return ParsedSender(
            sender_id=self.sender_id,
            sender_name=self.sender_name,
            sender_username=self.sender_username,
        )

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "chat_id": self.chat_id,
            "chat_title": self.chat_title,
            "chat_username": self.chat_username,
            "chat_type": self.chat_type,
            "members": self.members,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "sender_username": self.sender_username,
            "text": self.text,
            "reply_to_msg_id": self.reply_to_msg_id,
            "forward_from": self.forward_from,
            "date": self.date,
            "has_media": self.has_media,
            "edit_date": self.edit_date,
            "is_edited": self.is_edited,
            "media_type": self.media_type,
            "media_id": self.media_id,
        }


@dataclass(frozen=True)
class EditUpdate:
    message_id: int
    chat_id: int
    text: str | None = None
    edit_date: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "chat_id": self.chat_id,
            "text": self.text,
            "edit_date": self.edit_date,
        }

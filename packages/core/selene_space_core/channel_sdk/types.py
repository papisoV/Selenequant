"""Delivery-channel plugin contract (Telegram, Discord, webhooks, ...).

A "channel" is any transport that carries an outbound notification to a
human operator: messaging platforms, webhook relays, email gateways.
This ABC fixes the minimal surface -- two lifecycle calls, two send
shapes -- so the notifier layer can fan one alert out to N channels
without knowing their wire formats.

Contract notes:
  * Every member is a coroutine; all channels do network I/O and must be
    awaited from the notifier's event loop.
  * The abstract set is exactly {start, stop, send_text, send_payload};
    Python's ABC instantiation guard rejects subclasses missing any one.
  * ``start`` is resumable: after ``stop``, calling ``start`` again must
    yield a working channel.
  * Send methods return None and should raise only on terminal delivery
    failure; retry/backoff policy lives in the caller, not the channel.
"""

from __future__ import annotations

from typing import Any

from abc import ABC as _ABC, abstractmethod as _abstractmethod


class ChannelPlugin(_ABC):
    """Lifecycle plus two send shapes."""

    @_abstractmethod
    async def start(self) -> None:
        """Allocate sessions/pollers; afterwards the channel is deliverable."""

    @_abstractmethod
    async def stop(self) -> None:
        """Flush in-flight sends; release every resource. Idempotent."""

    @_abstractmethod
    async def send_text(self, target: str, text: str) -> None:
        """Deliver plain, unformatted ``text`` to destination ``target``."""
        return None

    @_abstractmethod
    async def send_payload(self, target: str, payload: dict[str, Any]) -> None:
        """Deliver a structured body -- cards, embeds, JSON -- to ``target``.

        ``payload`` is deliberately transport-shaped: each SDK interprets
        the mapping per its platform's rich-message format, so callers
        building cross-channel payloads keep format-specific branches.
        """
        return None

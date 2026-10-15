"""XTP TCP client — async connection to miniQmt for A-share trading.

Manages the TCP connection lifecycle, message framing, and
request/response correlation for the XTP protocol.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from selene_space_exchange.exchanges.qmt.protocol import (
    XTPMessage,
    build_heartbeat,
    build_login,
    build_logout,
    MSG_LOGIN_RESP,
)

logger = logging.getLogger(__name__)


class XTPClient:
    """Async TCP client for miniQmt XTP protocol.

    Connects to a locally running miniQmt instance via TCP.
    Handles login, heartbeat, and request/response correlation.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 6001,
        account_id: str = "",
        password: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._account_id = account_id
        self._password = password
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._running = False
        self._seq = 0
        self._pending: dict[int, asyncio.Future[XTPMessage]] = {}
        self._notify_callbacks: list[Callable[[XTPMessage], Any]] = []

    def on_notify(self, callback: Callable[[XTPMessage], Any]) -> None:
        self._notify_callbacks.append(callback)

    async def connect(self) -> bool:
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self._host, self._port,
            )
            self._running = True
            asyncio.create_task(self._read_loop())
            asyncio.create_task(self._heartbeat_loop())

            login_msg = build_login(self._account_id, self._password)
            resp = await self._send_and_wait(login_msg, MSG_LOGIN_RESP, timeout=5.0)
            if resp and resp.body.get("success", False):
                logger.info("xtp login success: account=%s", self._account_id)
                return True
            logger.warning("xtp login failed: %s", resp)
            return False
        except Exception as e:
            logger.error("xtp connect failed: %s", e)
            return False

    async def disconnect(self) -> None:
        self._running = False
        if self._writer:
            try:
                logout_msg = build_logout()
                self._writer.write(logout_msg)
                await self._writer.drain()
            except Exception:
                pass
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def test_connectivity(self) -> bool:
        if self._reader is None or self._writer is None:
            return False
        try:
            hb = build_heartbeat()
            self._writer.write(hb)
            await self._writer.drain()
            return True
        except Exception:
            return False

    async def send_request(self, data: bytes, expected_msg_type: int, timeout: float = 10.0) -> XTPMessage | None:
        return await self._send_and_wait(data, expected_msg_type, timeout)

    async def _send_and_wait(self, data: bytes, expected_msg_type: int, timeout: float = 10.0) -> XTPMessage | None:
        if self._writer is None:
            raise RuntimeError("XTP client not connected")

        self._seq += 1
        seq = self._seq
        future: asyncio.Future[XTPMessage] = asyncio.get_event_loop().create_future()
        self._pending[seq] = future

        try:
            self._writer.write(data)
            await self._writer.drain()
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("xtp request timeout: seq=%d, msg_type=0x%04x", seq, expected_msg_type)
            return None
        finally:
            self._pending.pop(seq, None)

    async def _read_loop(self) -> None:
        while self._running and self._reader:
            try:
                header = await self._reader.readexactly(6)
                import struct
                msg_type, body_len = struct.unpack("!HI", header)
                body_data = await self._reader.readexactly(body_len) if body_len > 0 else b""

                import json
                body = json.loads(body_data.decode("utf-8")) if body_data else {}
                msg = XTPMessage(msg_type=msg_type, body=body)

                # Check if this is a response to a pending request
                resolved = False
                for seq, future in list(self._pending.items()):
                    if not future.done():
                        future.set_result(msg)
                        resolved = True
                        break

                if not resolved:
                    # It's a push notification
                    for cb in self._notify_callbacks:
                        try:
                            cb(msg)
                        except Exception as e:
                            logger.warning("xtp notify callback error: %s", e)

            except asyncio.IncompleteReadError:
                if self._running:
                    logger.warning("xtp connection lost, will not auto-reconnect")
                break
            except Exception as e:
                if self._running:
                    logger.warning("xtp read error: %s", e)
                break

        self._running = False

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(30)
                if self._writer and self._running:
                    self._writer.write(build_heartbeat())
                    await self._writer.drain()
            except Exception:
                break

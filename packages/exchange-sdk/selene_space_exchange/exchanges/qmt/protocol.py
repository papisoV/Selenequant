"""XTP binary protocol — message serialization/deserialization for miniQmt.

The XTP protocol uses a binary frame format over TCP:
  [2 bytes: msg_type] [4 bytes: body_len] [body_len bytes: body]

Body encoding is JSON for most request/response types.
This module provides encode/decode helpers for the wire format.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any

# ── Message types ────────────────────────────────────────────────────────────

MSG_LOGIN = 0x0001
MSG_LOGIN_RESP = 0x0002
MSG_LOGOUT = 0x0003
MSG_HEARTBEAT = 0x0004

MSG_ORDER_INSERT = 0x0101
MSG_ORDER_CANCEL = 0x0102
MSG_ORDER_QUERY = 0x0103
MSG_ORDER_RESP = 0x0110
MSG_ORDER_NOTIFY = 0x0111

MSG_TRADE_QUERY = 0x0201
MSG_TRADE_RESP = 0x0210

MSG_POSITION_QUERY = 0x0301
MSG_POSITION_RESP = 0x0310

MSG_ASSET_QUERY = 0x0401
MSG_ASSET_RESP = 0x0410

MSG_ORDER_BOOK_QUERY = 0x0501
MSG_ORDER_BOOK_RESP = 0x0510

# ── Frame format ─────────────────────────────────────────────────────────────

_HEADER_FORMAT = "!HI"  # big-endian: uint16 msg_type + uint32 body_len
_HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)


@dataclass(frozen=True)
class XTPMessage:
    """A single XTP protocol message."""

    msg_type: int
    body: dict[str, Any]

    def encode(self) -> bytes:
        body_bytes = json.dumps(self.body, ensure_ascii=False).encode("utf-8")
        header = struct.pack(_HEADER_FORMAT, self.msg_type, len(body_bytes))
        return header + body_bytes

    @staticmethod
    def decode(data: bytes) -> XTPMessage:
        if len(data) < _HEADER_SIZE:
            raise ValueError(f"XTP frame too short: {len(data)} < {_HEADER_SIZE}")
        msg_type, body_len = struct.unpack(_HEADER_FORMAT, data[:_HEADER_SIZE])
        body_data = data[_HEADER_SIZE : _HEADER_SIZE + body_len]
        body = json.loads(body_data.decode("utf-8")) if body_data else {}
        return XTPMessage(msg_type=msg_type, body=body)


def encode_message(msg_type: int, body: dict[str, Any]) -> bytes:
    return XTPMessage(msg_type=msg_type, body=body).encode()


def decode_message(data: bytes) -> XTPMessage:
    return XTPMessage.decode(data)


# ── Request builders ─────────────────────────────────────────────────────────


def build_login(account_id: str, password: str = "") -> bytes:
    return encode_message(MSG_LOGIN, {
        "account_id": account_id,
        "password": password,
    })


def build_logout() -> bytes:
    return encode_message(MSG_LOGOUT, {})


def build_heartbeat() -> bytes:
    return encode_message(MSG_HEARTBEAT, {})


def build_order_insert(
    *,
    account_id: str,
    symbol: str,
    exchange: int,
    price: float,
    quantity: int,
    side: int,
    order_type: int,
) -> bytes:
    return encode_message(MSG_ORDER_INSERT, {
        "account_id": account_id,
        "symbol": symbol,
        "exchange": exchange,
        "price": price,
        "quantity": quantity,
        "side": side,
        "order_type": order_type,
    })


def build_order_cancel(*, account_id: str, order_id: str) -> bytes:
    return encode_message(MSG_ORDER_CANCEL, {
        "account_id": account_id,
        "order_id": order_id,
    })


def build_order_query(*, account_id: str) -> bytes:
    return encode_message(MSG_ORDER_QUERY, {"account_id": account_id})


def build_position_query(*, account_id: str) -> bytes:
    return encode_message(MSG_POSITION_QUERY, {"account_id": account_id})


def build_asset_query(*, account_id: str) -> bytes:
    return encode_message(MSG_ASSET_QUERY, {"account_id": account_id})


# ── A-share exchange codes ───────────────────────────────────────────────────

EXCHANGE_SH = 1  # Shanghai
EXCHANGE_SZ = 2  # Shenzhen

SIDE_BUY = 1
SIDE_SELL = 2

ORDER_TYPE_LIMIT = 1
ORDER_TYPE_MARKET = 2


def ashare_symbol_to_xtp(symbol: str) -> tuple[str, int]:
    """Convert A-share symbol (e.g. '600519.SH') to XTP (symbol, exchange) pair."""
    parts = symbol.split(".")
    if len(parts) != 2:
        raise ValueError(f"Invalid A-share symbol format: {symbol}")
    code, suffix = parts[0], parts[1].upper()
    if suffix == "SH":
        return code, EXCHANGE_SH
    if suffix in ("SZ", "BJ"):
        return code, EXCHANGE_SZ
    raise ValueError(f"Unknown A-share exchange suffix: {suffix}")

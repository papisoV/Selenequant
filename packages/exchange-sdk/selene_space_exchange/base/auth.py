"""Credential holder and per-venue request signers.

Binance signs a sorted query string with HMAC-SHA256 (hex digest); OKX signs
the concatenation timestamp+method+path+body with HMAC-SHA256 and encodes it
base64. Both schemes follow the venues' published API documentation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, SecretStr

logger = logging.getLogger(__name__)

#: Binance rejects requests whose timestamp is older than recvWindow; the
#: venue default is 5000ms and we allow widening via env for slow links.
DEFAULT_BINANCE_RECV_WINDOW = 60000

_ENV_RECV_WINDOW = "SELENE_BINANCE_RECV_WINDOW"

#: Warn once local clock is more than half a recvWindow away from the server.
_DRIFT_WARN_RATIO = 0.5


def _get_binance_recv_window() -> int:
    raw = os.environ.get(_ENV_RECV_WINDOW, "")
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_BINANCE_RECV_WINDOW


def _check_clock_drift(
    server_time: int | float, local_time: int | float | None = None
) -> int:
    """Return signed drift in ms; log a warning past half the recv window."""
    if local_time is None:
        local_time = int(time.time() * 1000)
    drift = int(server_time - local_time)
    threshold = DEFAULT_BINANCE_RECV_WINDOW * _DRIFT_WARN_RATIO
    if abs(drift) > threshold:
        logger.warning(
            "Clock drift %dms exceeds %.0fms — sync system time or widen "
            "recvWindow to avoid -1021 rejections",
            drift,
            threshold,
        )
    return drift


class ExchangeCredentials(BaseModel):
    """Key/secret pair, optionally with a passphrase (OKX needs one)."""

    api_key: SecretStr
    api_secret: SecretStr
    passphrase: SecretStr | None = None


class BaseAuthenticator(ABC):
    """Common plumbing: hold credentials, expose them redacted."""

    def __init__(self, credentials: ExchangeCredentials) -> None:
        self._credentials = credentials

    @property
    def api_key(self) -> str:
        return self._credentials.api_key.get_secret_value()

    @property
    def api_secret(self) -> str:
        return self._credentials.api_secret.get_secret_value()

    @abstractmethod
    def sign_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: str | None = None,
        timestamp: int | str | None = None,
    ) -> dict[str, str]:
        """Produce the header map for an authenticated request."""


class BinanceAuthenticator(BaseAuthenticator):
    """HMAC-SHA256 over the alphabetically sorted query string."""

    def __init__(self, credentials: ExchangeCredentials) -> None:
        super().__init__(credentials)
        self._recv_window = _get_binance_recv_window()

    def sign_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: str | None = None,
        timestamp: int | str | None = None,
        recv_window: int | None = None,
    ) -> dict[str, str]:
        # Contract: the caller's params mapping is filled in place so the
        # REST layer can serialise exactly what was signed.
        work: dict[str, Any] = params if params is not None else {}
        if timestamp is None:
            timestamp = int(time.time() * 1000)
        elif isinstance(timestamp, int):
            _check_clock_drift(server_time=timestamp)
        work["timestamp"] = str(timestamp)

        window = recv_window if recv_window is not None else self._recv_window
        work["recvWindow"] = str(window)

        query = "&".join(f"{k}={v}" for k, v in sorted(work.items()))
        work["signature"] = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {"X-MBX-APIKEY": self.api_key}


class OKXAuthenticator(BaseAuthenticator):
    """Base64(HMAC-SHA256(timestamp + method + path + body)) with OK-* headers."""

    @property
    def passphrase(self) -> str:
        value = self._credentials.passphrase
        if value is None:
            raise ValueError("OKX credentials require a passphrase")
        return value.get_secret_value()

    def sign_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: str | None = None,
        timestamp: int | str | None = None,
    ) -> dict[str, str]:
        from urllib.parse import urlencode

        request_path = path
        if params:
            request_path = f"{path}?{urlencode(params)}"

        ts = (
            timestamp
            if isinstance(timestamp, str)
            else (
                time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                + f".{int((time.time() % 1) * 1000):03d}Z"
                if timestamp is None
                else str(timestamp)
            )
        )
        prehash = f"{ts}{method.upper()}{request_path}{body or ''}"
        signature = base64.b64encode(
            hmac.new(
                self.api_secret.encode("utf-8"),
                prehash.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        return {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
        }

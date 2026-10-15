"""Tests for detect_local_proxy in polymarket/client.py.

Covers 8 cases per Plan v4 (phase6-mainnet-8bugs-v4.md L121-133):
- env var priority
- SELENE_FORCE_PROXY override (no fallback)
- Windows registry branches (ProxyEnable 1/0, connectivity probe)
- non-win32 fallback
- httpx non-200 handling
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from selene_space_exchange.exchanges.polymarket.client import (
    _test_proxy_connectivity,
    detect_local_proxy,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

PROXY_ENV_VARS = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "SELENE_FORCE_PROXY",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip proxy-related env vars before each test."""
    for var in PROXY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Case 1: HTTPS_PROXY set → returns that value
# ---------------------------------------------------------------------------


def test_case1_https_proxy_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:8080")
    assert detect_local_proxy() == "http://10.0.0.1:8080"


# ---------------------------------------------------------------------------
# Case 2: SELENE_FORCE_PROXY=127.0.0.1:7897 → returns http://127.0.0.1:7897
# ---------------------------------------------------------------------------


def test_case2_force_proxy_no_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SELENE_FORCE_PROXY", "127.0.0.1:7897")
    assert detect_local_proxy() == "http://127.0.0.1:7897"


# ---------------------------------------------------------------------------
# Case 3: win32 mock, ProxyEnable=1 + ProxyServer set → returns server
# ---------------------------------------------------------------------------


def _make_winreg_mock(enable: int, server: str) -> MagicMock:
    """Build a fake winreg module with a single ProxyEnable/ProxyServer pair.

    Real winreg.OpenKey returns a handle; winreg.QueryValueEx(handle, name)
    returns (value, type). We mirror that module-level API.
    """
    fake = MagicMock()
    fake.HKEY_CURRENT_USER = "HKCU"

    # OpenKey returns an opaque handle (we don't care about its value)
    fake.OpenKey.return_value = MagicMock(name="key_handle")

    # QueryValueEx is called twice in order: ProxyEnable, then ProxyServer
    fake.QueryValueEx.side_effect = [(enable, 4), (server, 1)]
    return fake


def test_case3_win32_proxy_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    fake_winreg = _make_winreg_mock(enable=1, server="127.0.0.1:7897")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    assert detect_local_proxy() == "http://127.0.0.1:7897"


# ---------------------------------------------------------------------------
# Case 4: ProxyEnable=0 + ProxyServer set + _test_proxy_connectivity True
# ---------------------------------------------------------------------------


def test_case4_win32_proxy_disabled_but_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    fake_winreg = _make_winreg_mock(enable=0, server="127.0.0.1:7897")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(
        "selene_space_exchange.exchanges.polymarket.client._test_proxy_connectivity",
        lambda proxy: True,
    )
    assert detect_local_proxy() == "http://127.0.0.1:7897"


# ---------------------------------------------------------------------------
# Case 5: ProxyEnable=0 + ProxyServer set + _test_proxy_connectivity False
# ---------------------------------------------------------------------------


def test_case5_win32_proxy_disabled_and_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    fake_winreg = _make_winreg_mock(enable=0, server="127.0.0.1:7897")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(
        "selene_space_exchange.exchanges.polymarket.client._test_proxy_connectivity",
        lambda proxy: False,
    )
    assert detect_local_proxy() is None


# ---------------------------------------------------------------------------
# Case 6: no env + non-win32 → returns None
# ---------------------------------------------------------------------------


def test_case6_non_win32_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert detect_local_proxy() is None


# ---------------------------------------------------------------------------
# Case 7: mock httpx.Client.get returns 500 → _test_proxy_connectivity False
# ---------------------------------------------------------------------------


class _FakeResponse500:
    status_code = 500


class _FakeClient500:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> "_FakeClient500":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False

    def get(self, *args: Any, **kwargs: Any) -> _FakeResponse500:
        return _FakeResponse500()


def test_case7_httpx_500_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setattr(httpx, "Client", _FakeClient500)
    assert _test_proxy_connectivity("http://127.0.0.1:7897") is False


# ---------------------------------------------------------------------------
# Case 8: SELENE_FORCE_PROXY=dead proxy + ProxyEnable=0 + alive ProxyServer
#         → returns SELENE_FORCE_PROXY value unconditionally (no fallback)
# ---------------------------------------------------------------------------


def test_case8_force_proxy_no_fallback_to_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SELENE_FORCE_PROXY", "127.0.0.1:9999")
    monkeypatch.setattr(sys, "platform", "win32")
    fake_winreg = _make_winreg_mock(enable=0, server="127.0.0.1:7897")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(
        "selene_space_exchange.exchanges.polymarket.client._test_proxy_connectivity",
        lambda proxy: True,
    )
    # SELENE_FORCE_PROXY wins; registry is never consulted
    assert detect_local_proxy() == "http://127.0.0.1:9999"

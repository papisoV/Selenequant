"""Outbound proxy policy: local endpoints must bypass the system proxy."""

from __future__ import annotations

import pytest
from app.core.net_policy import is_local_endpoint, proxy_for


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:7863/v1",
        "http://localhost:8088",
        "http://LOCALHOST:3000",
        "http://[::1]:7863/v1",
        "http://10.0.0.5:8000",
        "http://192.168.1.20:8080",
        "http://172.16.4.2",
        "http://169.254.10.10",
        "127.0.0.1:7863",  # scheme-less form
    ],
)
def test_local_endpoints_detected(url):
    assert is_local_endpoint(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://8.8.8.8/v1",
        "https://api.deepseek.com",
        "http://172.32.0.1",  # outside 172.16/12 private range
        "",
        None,
    ],
)
def test_remote_endpoints_not_local(url):
    assert is_local_endpoint(url) is False


def test_proxy_dropped_for_local_base_url():
    assert proxy_for("http://127.0.0.1:7863/v1", "http://127.0.0.1:7897") == ""


def test_proxy_kept_for_remote_base_url():
    assert (
        proxy_for("https://api.openai.com/v1", "http://127.0.0.1:7897")
        == "http://127.0.0.1:7897"
    )


def test_no_proxy_configured_stays_direct():
    assert proxy_for("https://api.openai.com/v1", "") == ""

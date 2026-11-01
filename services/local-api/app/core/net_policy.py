"""Outbound-connection policy for the local API.

A configured system proxy must not swallow traffic that never leaves the
machine.  When the proxy port is stale (or Clash is down), routing a
loopback LLM gateway through it takes the whole AI chain offline even
though the gateway itself is healthy.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

_LOCAL_HOSTNAMES = frozenset({"localhost", "localhost.localdomain"})


def is_local_endpoint(url: str | None) -> bool:
    """True when ``url`` addresses this machine or a private network.

    Accepts URLs with or without a scheme (``127.0.0.1:7863`` parses as a
    scheme by ``urlsplit`` otherwise, which would hide the host).
    """
    if not url:
        return False
    parsed = urlsplit(url if "://" in url else f"//{url}")
    host = parsed.hostname
    if not host:
        return False
    host = host.rstrip(".").lower()
    if host in _LOCAL_HOSTNAMES or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def proxy_for(base_url: str | None, proxy_url: str) -> str:
    """Return the proxy to use for ``base_url``; "" means connect directly."""
    if not proxy_url:
        return ""
    return "" if is_local_endpoint(base_url) else proxy_url

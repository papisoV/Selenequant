"""Local proxy resolution, discovery and connectivity probing.

The settings page used to offer a single text box and a hard-coded
``127.0.0.1:7890`` placeholder — an example that is wrong on most machines and
that the page could not verify. Worse, the backend builds every fetcher from
the proxy at startup (``main.py::_init_market_data``), so a bad value stays
invisible until the next restart.

This module gives the UI three things it could not do before:

* :func:`describe_status` — which of the four precedence layers is winning and
  whether the saved value has actually been picked up yet.
* :func:`detect_candidates` — which localhost ports look like a working proxy.
* :func:`probe_targets` — does traffic through a given proxy actually reach the
  venues the backend needs.

Everything is bounded: no call here may block a request for more than a few
seconds, and every failure is reported per target instead of raised, because
"the proxy is fine but Binance is blocked" is a different problem from
"the proxy is dead" and the user has to be able to tell them apart.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse, urlunparse

import httpx

#: Localhost ports worth trying. Ordered by how common they are for the local
#: proxy clients people actually run on Windows/macOS.
PLACEHOLDER_PORTS: tuple[int, ...] = (7890, 7891, 7897, 7899, 7900, 1080, 10808, 8080)

#: How long a single TCP connect may take while discovering ports.
_DETECT_CONNECT_TIMEOUT_S = 0.4

#: How long a candidate port gets to prove it speaks HTTP-proxy.
_DETECT_VERIFY_TIMEOUT_S = 3.0

#: Per-target budget for the connectivity test.
_PROBE_TIMEOUT_S = 8.0

#: Slack on top of the per-target budget before the route answers anyway.
#: Cancelling a probe does not stop DNS resolution — httpx resolves on a worker
#: thread that asyncio cannot interrupt — so the wall clock, not the task
#: completion, is what bounds the request.
_PROBE_GRACE_S = 1.0

#: A small, cheap, CORS-free endpoint per service the backend depends on.
#: Three different operators on purpose: one failing can be the venue, not the proxy.
PROBE_TARGETS: tuple[tuple[str, str], ...] = (
    ("binance", "https://api.binance.com/api/v3/ping"),
    ("github", "https://api.github.com/zen"),
    ("defillama", "https://api.llama.fi/ping"),
)

#: Used by :func:`detect_candidates` to check a port really is a proxy.
_VERIFY_URL = "https://api.github.com/zen"


# ── URL hygiene ──────────────────────────────────────────────────────────────


def normalize_proxy(url: str | None) -> str:
    """Canonical form used for comparisons: scheme present, no trailing slash.

    ``127.0.0.1:7899`` and ``http://127.0.0.1:7899/`` are the same proxy and
    must compare equal when deciding whether a restart is pending.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    return raw.rstrip("/")


def mask_proxy(url: str | None) -> str:
    """Hide inline credentials for anything shown to a human.

    ``http://user:secret@host:7899`` → ``http://user:***@host:7899``. Values
    without a userinfo part come back unchanged. Unparseable input degrades to
    ``***`` rather than echoing something we could not reason about.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    has_scheme = "://" in raw
    try:
        parsed = urlparse(raw if has_scheme else f"http://{raw}")
        if not parsed.hostname:
            return "***"
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        if parsed.username:
            host = f"{parsed.username}:***@{host}"
        if not has_scheme:
            return host
        return urlunparse(parsed._replace(netloc=host))
    except (ValueError, UnicodeError):
        return "***"


def env_layers() -> dict[str, str]:
    """The environment variables ``main.py`` consults, in its own order.

    Mirrors ``_init_market_data`` exactly: it reads ``SELENE_PROXY_URL`` then
    lowercase ``https_proxy`` and nothing else. Reporting a variable the
    backend ignores would make the status panel lie about what is in effect.
    """
    return {
        "selene_proxy_url": os.environ.get("SELENE_PROXY_URL", "").strip(),
        "https_proxy": os.environ.get("https_proxy", "").strip(),
    }


def describe_status(
    *,
    saved: str | None,
    effective: str | None = None,
    effective_known: bool = True,
) -> dict[str, Any]:
    """Explain which layer is in effect and whether a restart is still pending.

    ``effective`` is what the running process actually built its fetchers with;
    ``saved`` is what is on disk. When they differ, the user has saved a value
    that no fetcher is using yet — the exact confusion the old UI created by
    flashing "takes effect after restart" for three seconds and then hiding it.

    ``effective_known=False`` means the process never published what it built
    with; every "in effect" claim would then be a guess, so the caller gets
    ``source="unknown"`` and ``pending_restart=None`` instead of a plausible lie.
    """
    layers = env_layers()
    saved_n = normalize_proxy(saved)
    effective_n = normalize_proxy(effective)

    if layers["selene_proxy_url"]:
        source = "env:selene"
    elif layers["https_proxy"]:
        source = "env:https"
    elif not effective_known:
        source = "unknown"
    elif saved_n and effective_n == saved_n:
        source = "saved"
    elif saved_n and effective_n and effective_n != saved_n:
        # Saved after startup, or an old value is still running.
        source = "saved-stale"
    elif effective_n:
        source = "detected"
    else:
        source = "direct"

    if effective_known:
        # An environment variable outranks the saved value, so a restart would
        # not change what the fetchers use — saying "restart to apply" there
        # would send the user to do something that cannot work. They have to
        # clear the env var first; the source label says which one.
        if source.startswith("env:"):
            pending_restart = False
        else:
            pending_restart = saved_n != effective_n
    else:
        pending_restart = None

    return {
        "env": {
            "selene_proxy_url": bool(layers["selene_proxy_url"]),
            "https_proxy": bool(layers["https_proxy"]),
        },
        "saved": saved or "",
        "effective": effective or "",
        "saved_masked": mask_proxy(saved),
        "effective_masked": mask_proxy(effective),
        "source": source,
        "pending_restart": pending_restart,
        "effective_known": effective_known,
    }


# ── Discovery ────────────────────────────────────────────────────────────────


#: Injectable seam for tests: (url, timeout) → whether the proxy is usable.
VerifyFn = Callable[[str, float], Awaitable[bool]]


async def _tcp_listening(port: int, timeout: float) -> bool:
    """Is anything accepting connections on 127.0.0.1:port?"""
    writer: asyncio.StreamWriter | None = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout
        )
        return True
    except (OSError, asyncio.TimeoutError, ValueError):
        return False
    finally:
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


async def _proxy_speaks_http(url: str, timeout: float) -> bool:
    try:
        async with httpx.AsyncClient(
            proxy=url, timeout=timeout, trust_env=False
        ) as client:
            response = await client.get(_VERIFY_URL)
            return response.status_code < 500
    except Exception:
        return False


async def detect_candidates(
    ports: tuple[int, ...] | None = None,
    *,
    verify: VerifyFn | None = None,
) -> list[dict[str, Any]]:
    """Find localhost ports that are both listening and usable as a proxy.

    A port being open is not enough — plenty of things listen on 8080 — so each
    listening candidate gets one real request through it. Both phases run
    concurrently and are individually bounded, keeping the whole call near
    :data:`_DETECT_VERIFY_TIMEOUT_S` in the worst case.
    """
    chosen = ports if ports is not None else PLACEHOLDER_PORTS
    verify_fn = verify or _proxy_speaks_http

    listening_flags = await asyncio.gather(
        *(_tcp_listening(port, _DETECT_CONNECT_TIMEOUT_S) for port in chosen)
    )
    open_ports = [p for p, listening in zip(chosen, listening_flags) if listening]
    if not open_ports:
        return []

    urls = [f"http://127.0.0.1:{port}" for port in open_ports]
    verdicts = await asyncio.gather(
        *(verify_fn(url, _DETECT_VERIFY_TIMEOUT_S) for url in urls)
    )
    return [
        {"url": url, "port": port, "listening": True, "reachable": bool(ok)}
        for url, port, ok in zip(urls, open_ports, verdicts)
    ]


# ── Connectivity test ────────────────────────────────────────────────────────


#: Injectable seam for tests: (proxy, url, timeout) → (status, error, ms).
ProbeFn = Callable[
    [str | None, str, float], Awaitable[tuple[int | None, str | None, int]]
]


async def _bounded(coro: Awaitable[Any], timeout: float) -> Any:
    """Run ``coro`` for at most ``timeout`` seconds, then abandon it.

    ``asyncio.wait_for`` is the obvious tool and the wrong one here: on timeout
    it cancels *and then awaits* the task, and a request stuck in a worker
    thread (DNS resolution, a connect that ignores the socket timeout) cannot
    answer that cancellation. Measured live, an 8 s budget came back after
    17.3 s — the bound was decoration. ``asyncio.wait`` returns at the deadline
    regardless; the abandoned task is left to finish and its result is dropped.
    """
    task = asyncio.ensure_future(coro)
    done, _pending = await asyncio.wait({task}, timeout=timeout)
    if not done:
        task.cancel()
        # Consume the outcome so an abandoned task cannot surface later as
        # "Task exception was never retrieved".
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        raise asyncio.TimeoutError
    return task.result()


async def _http_probe(
    proxy: str | None, url: str, timeout: float
) -> tuple[int | None, str | None, int]:
    started = time.perf_counter()
    # httpx applies its timeout per phase (connect/read/pool), so a dead proxy
    # can burn several times the nominal value. Bound the whole attempt instead.
    deadline = httpx.Timeout(timeout, connect=min(timeout, 4.0), pool=2.0)

    async def _attempt() -> httpx.Response:
        # trust_env=False on purpose: httpx would otherwise read HTTPS_PROXY and
        # send even the "direct" probe through it, so the empty-proxy control
        # group silently tested the same proxy twice and could never show that
        # a proxy was the problem.
        async with httpx.AsyncClient(
            proxy=proxy or None, timeout=deadline, trust_env=False
        ) as client:
            return await client.get(url)

    try:
        response = await _bounded(_attempt(), timeout=timeout + _PROBE_GRACE_S)
        elapsed = int((time.perf_counter() - started) * 1000)
        # 4xx from /zen or /ping still proves the request left the machine and
        # came back — that is what this test is about.
        return response.status_code, None, elapsed
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return None, "timeout", int((time.perf_counter() - started) * 1000)
    except Exception as exc:  # noqa: BLE001 — reported, never raised
        return None, type(exc).__name__, int((time.perf_counter() - started) * 1000)


@dataclass(frozen=True)
class TargetOutcome:
    name: str
    ok: bool
    status: int | None
    ms: int
    error: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "status": self.status,
            "ms": self.ms,
            "error": self.error,
        }


async def probe_targets(
    proxy: str | None,
    targets: tuple[tuple[str, str], ...] | None = None,
    *,
    probe: ProbeFn | None = None,
    timeout: float = _PROBE_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """Try each target through ``proxy`` and report every result separately.

    Targets are probed concurrently and each is independently bounded, so one
    hanging endpoint costs at most ``timeout`` and never swallows the others.
    The gather itself is bounded too: when the budget runs out the slow targets
    are reported as timeouts rather than holding the HTTP request open.
    """
    chosen = targets if targets is not None else PROBE_TARGETS
    probe_fn = probe or _http_probe

    async def _one(name: str, url: str) -> TargetOutcome:
        started = time.perf_counter()
        status, error, ms = await probe_fn(proxy, url, timeout)
        return TargetOutcome(
            name=name,
            # A 5xx means we reached the venue and it is unhappy; for a proxy
            # check that still counts as "the path works".
            ok=error is None and status is not None and status < 500,
            status=status,
            ms=ms,
            error=error,
        )

    # One budget for the whole gather, not just per target: a worker thread
    # stuck in DNS keeps its coroutine alive past its own deadline (see
    # :func:`_bounded`), and three of those in parallel is how an 8 s test took
    # 17 s. Whatever has not answered by then is reported as a timeout.
    hard_deadline = timeout + _PROBE_GRACE_S
    outcome_tasks = {
        asyncio.ensure_future(_one(name, url)): name for name, url in chosen
    }
    done, pending = await asyncio.wait(outcome_tasks, timeout=hard_deadline)

    outcomes: list[TargetOutcome] = []
    for task in done:
        outcomes.append(task.result())
    for task in pending:
        task.cancel()
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        outcomes.append(
            TargetOutcome(
                name=outcome_tasks[task],
                ok=False,
                status=None,
                ms=int(hard_deadline * 1000),
                error="timeout",
            )
        )

    order = {name: i for i, (name, _url) in enumerate(chosen)}
    outcomes.sort(key=lambda o: order[o.name])
    return [outcome.as_dict() for outcome in outcomes]

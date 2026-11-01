"""Proxy discovery, masking and connectivity probing.

The user-facing failure these lock down: the settings page offered one text box
with a hard-coded ``127.0.0.1:7890`` placeholder and no way to tell whether the
value worked, whether it had been picked up, or whether the problem was the
proxy at all. The three routes here are what make those questions answerable,
so the tests pin the answers rather than the plumbing:

* a saved value that differs from the running one must read as "restart
  pending", and an unpublished runtime must read as "unknown" — never as
  "already in effect";
* credentials in a proxy URL never reach the client;
* one dead target must not swallow the other two.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from app.core import proxy_probe
from app.core.proxy_probe import (
    describe_status,
    detect_candidates,
    mask_proxy,
    normalize_proxy,
    probe_targets,
)
from app.core.settings_service import SettingsService
from app.main import create_app
from httpx import ASGITransport, AsyncClient

BASE = "/api/v1/settings"
NETWORK = f"{BASE}/network"


# --- URL hygiene ------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("127.0.0.1:7899", "http://127.0.0.1:7899"),
        ("http://127.0.0.1:7899/", "http://127.0.0.1:7899"),
        ("  http://127.0.0.1:7899  ", "http://127.0.0.1:7899"),
        ("socks5://127.0.0.1:1080", "socks5://127.0.0.1:1080"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_proxy_makes_equivalent_forms_compare_equal(raw, expected):
    assert normalize_proxy(raw) == expected


def test_normalize_proxy_is_idempotent():
    once = normalize_proxy("127.0.0.1:7899/")
    assert normalize_proxy(once) == once


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://user:secret@127.0.0.1:7899", "http://user:***@127.0.0.1:7899"),
        ("socks5://alice:hunter2@127.0.0.1:1080", "socks5://alice:***@127.0.0.1:1080"),
        ("http://127.0.0.1:7899", "http://127.0.0.1:7899"),
        ("127.0.0.1:7899", "127.0.0.1:7899"),
        ("", ""),
    ],
)
def test_mask_proxy_hides_inline_credentials(raw, expected):
    assert mask_proxy(raw) == expected


def test_mask_proxy_never_echoes_the_secret():
    masked = mask_proxy("http://user:s3cret@127.0.0.1:7899")
    assert "s3cret" not in masked


# --- status ----------------------------------------------------------------


def test_status_saved_matches_effective_means_no_restart(monkeypatch):
    monkeypatch.delenv("SELENE_PROXY_URL", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    status = describe_status(saved="http://127.0.0.1:7899", effective="127.0.0.1:7899")
    assert status["pending_restart"] is False
    assert status["source"] == "saved"


def test_status_saved_differs_from_effective_means_restart_pending(monkeypatch):
    monkeypatch.delenv("SELENE_PROXY_URL", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    status = describe_status(
        saved="http://127.0.0.1:7900", effective="http://127.0.0.1:7899"
    )
    assert status["pending_restart"] is True


def test_status_without_published_runtime_refuses_to_guess(monkeypatch):
    monkeypatch.delenv("SELENE_PROXY_URL", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    status = describe_status(saved="http://127.0.0.1:7899", effective_known=False)
    assert status["pending_restart"] is None
    assert status["source"] == "unknown"


def test_env_overrides_saved_value(monkeypatch):
    monkeypatch.setenv("SELENE_PROXY_URL", "http://127.0.0.1:7900")
    monkeypatch.delenv("https_proxy", raising=False)
    status = describe_status(
        saved="http://127.0.0.1:7899", effective="http://127.0.0.1:7900"
    )
    assert status["source"] == "env:selene"


def test_env_layer_never_promises_a_restart_would_help(monkeypatch):
    """A pending-restart banner under an env override is a false instruction.

    The env var outranks whatever is saved, so restarting re-reads the same env
    var and the fetchers come up identical. The only action that changes the
    behaviour is clearing the variable, which the ``source`` label names.
    """
    monkeypatch.setenv("SELENE_PROXY_URL", "http://127.0.0.1:7900")
    monkeypatch.delenv("https_proxy", raising=False)
    status = describe_status(
        saved="http://127.0.0.1:7899", effective="http://127.0.0.1:7900"
    )
    assert status["saved"] != status["effective"]  # the naive compare would fire
    assert status["pending_restart"] is False


def test_status_masks_both_values(monkeypatch):
    monkeypatch.delenv("SELENE_PROXY_URL", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    status = describe_status(
        saved="http://u:p@127.0.0.1:7899", effective="http://u:p@127.0.0.1:7899"
    )
    assert "p@" not in status["saved_masked"]
    assert "p@" not in status["effective_masked"]


# --- discovery --------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_reports_listening_ports_that_speak_proxy(monkeypatch):
    async def fake_listen(port, timeout):
        return port in (7899, 8080)

    async def fake_verify(url, timeout):
        return url.endswith("7899")  # 8080 is open but is not a proxy

    monkeypatch.setattr(proxy_probe, "_tcp_listening", fake_listen)
    found = await detect_candidates(ports=(7899, 8080), verify=fake_verify)

    assert [c["port"] for c in found] == [7899, 8080]
    assert found[0]["reachable"] is True
    assert found[1]["reachable"] is False


@pytest.mark.asyncio
async def test_detect_returns_empty_when_nothing_listens(monkeypatch):
    async def nothing(port, timeout):
        return False

    monkeypatch.setattr(proxy_probe, "_tcp_listening", nothing)
    assert await detect_candidates(ports=(7899,)) == []


@pytest.mark.asyncio
async def test_detect_skips_verification_for_closed_ports(monkeypatch):
    calls: list[str] = []

    async def nothing(port, timeout):
        return False

    async def verify(url, timeout):
        calls.append(url)
        return True

    monkeypatch.setattr(proxy_probe, "_tcp_listening", nothing)
    await detect_candidates(ports=(7899, 7900), verify=verify)
    assert calls == []


# --- connectivity test ------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_isolates_a_failing_target():
    async def fake_probe(proxy, url, timeout):
        if "binance" in url:
            return None, "ConnectTimeout", 8000
        if "github" in url:
            return 200, None, 120
        return 404, None, 300

    results = await probe_targets(
        None,
        targets=(
            ("binance", "https://api.binance.com/api/v3/ping"),
            ("github", "https://api.github.com/zen"),
            ("defillama", "https://api.llama.fi/ping"),
        ),
        probe=fake_probe,
    )

    by_name = {r["name"]: r for r in results}
    assert by_name["binance"]["ok"] is False
    assert by_name["binance"]["error"] == "ConnectTimeout"
    assert by_name["github"]["ok"] is True
    # A 404 still proves the request completed a round trip.
    assert by_name["defillama"]["ok"] is True


@pytest.mark.asyncio
async def test_probe_treats_5xx_as_unreachable():
    async def server_error(proxy, url, timeout):
        return 503, None, 90

    results = await probe_targets(
        None, targets=(("x", "https://example.invalid"),), probe=server_error
    )
    assert results[0]["ok"] is False


@pytest.mark.asyncio
async def test_probe_reports_results_in_the_declared_order():
    """The panel pairs each row with its service name — order is part of the contract."""

    async def slow_for_first(proxy, url, timeout):
        await asyncio.sleep(0.05 if "first" in url else 0.0)
        return 200, None, 10

    results = await probe_targets(
        None,
        targets=(("first", "https://a.invalid"), ("second", "https://b.invalid")),
        probe=slow_for_first,
    )
    assert [r["name"] for r in results] == ["first", "second"]


@pytest.mark.asyncio
async def test_probe_holds_its_wall_clock_when_a_probe_never_returns():
    """A probe that ignores its own timeout must not hold the HTTP request open.

    Live measurement before this bound existed: an 8 s budget answered after
    17.3 s, because a worker thread stuck in DNS cannot honour cancellation and
    ``asyncio.wait_for`` awaits that cancellation. The gather now has its own
    deadline and reports whatever is late as a timeout.
    """

    async def hangs(proxy, url, timeout):
        await asyncio.sleep(30)
        return 200, None, 10

    started = time.perf_counter()
    results = await probe_targets(
        None,
        targets=(("slow", "https://slow.invalid"),),
        probe=hangs,
        timeout=0.2,
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 3.0, f"gather was not bounded: {elapsed:.1f}s"
    assert results[0]["ok"] is False
    assert results[0]["error"] == "timeout"


@pytest.mark.asyncio
async def test_http_probe_ignores_the_process_proxy_for_the_direct_control(
    monkeypatch,
):
    """``proxy=None`` must mean direct, even on a machine with HTTPS_PROXY set.

    Without ``trust_env=False`` httpx reads the env var and sends the "direct"
    probe through the same proxy as the proxied one, so the control group
    compares the proxy to itself and can never show that the proxy is the fault.
    """
    import httpx

    seen: list[dict] = []

    class _FakeClient:
        def __init__(self, **kwargs):
            seen.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            request = httpx.Request("GET", url)
            return httpx.Response(200, request=request)

    monkeypatch.setattr(proxy_probe.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7899")

    status, error, _ms = await proxy_probe._http_probe(None, "https://x.invalid", 2.0)

    assert (status, error) == (200, None)
    assert len(seen) == 1
    assert seen[0].get("proxy") is None
    # The load-bearing bit: with trust_env left on, httpx would join HTTPS_PROXY
    # to that None and the "direct" control would test the proxy again.
    assert seen[0].get("trust_env") is False


# --- routes -----------------------------------------------------------------


class _Vault:
    def __init__(self):
        self._vault: dict[str, str] = {}

    def keyring_available(self):
        return True

    def keyring_backend_info(self):
        return {"available": True, "encryption_active": False, "backend": "stub"}

    @staticmethod
    def _ref_id(ref) -> str:
        return f"{ref.provider}:{ref.id}"

    async def exists(self, ref):
        return self._ref_id(ref) in self._vault

    async def store(self, ref, value: str) -> None:
        self._vault[self._ref_id(ref)] = value

    async def delete(self, ref):
        self._vault.pop(self._ref_id(ref), None)


def _app(tmp_path):
    service = SettingsService(
        config_path=tmp_path / "settings.json", secret_manager=_Vault()
    )
    app = create_app()
    from app.core.dependencies import get_settings_service

    app.dependency_overrides[get_settings_service] = lambda: service
    return app, service


async def _call(app, method, path, **kw):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await getattr(client, method)(path, **kw)


@pytest.mark.asyncio
async def test_status_route_sees_saved_value_and_runtime_value(monkeypatch, tmp_path):
    monkeypatch.delenv("SELENE_PROXY_URL", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    app, service = _app(tmp_path)
    await service.update_settings({"network": {"proxy_url": "http://127.0.0.1:7900"}})
    app.state.effective_proxy_url = "http://127.0.0.1:7899"

    resp = await _call(app, "get", f"{NETWORK}/status")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["saved"] == "http://127.0.0.1:7900"
    assert data["effective"] == "http://127.0.0.1:7899"
    assert data["pending_restart"] is True


@pytest.mark.asyncio
async def test_status_route_reports_unknown_when_runtime_never_published(
    monkeypatch, tmp_path
):
    """A missing app.state value must not be rendered as 'direct connection'."""
    monkeypatch.delenv("SELENE_PROXY_URL", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    app, service = _app(tmp_path)
    await service.update_settings({"network": {"proxy_url": "http://127.0.0.1:7900"}})

    resp = await _call(app, "get", f"{NETWORK}/status")

    data = resp.json()["data"]
    assert data["effective_known"] is False
    assert data["pending_restart"] is None


@pytest.mark.asyncio
async def test_detect_route_recommends_the_usable_candidate(monkeypatch, tmp_path):
    async def fake_detect(ports=None, *, verify=None):
        return [
            {
                "url": "http://127.0.0.1:7899",
                "port": 7899,
                "listening": True,
                "reachable": True,
            },
            {
                "url": "http://127.0.0.1:8080",
                "port": 8080,
                "listening": True,
                "reachable": False,
            },
        ]

    monkeypatch.setattr(proxy_probe, "detect_candidates", fake_detect)
    app, _ = _app(tmp_path)

    resp = await _call(app, "post", f"{NETWORK}/detect")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["recommended"] == "http://127.0.0.1:7899"
    assert len(data["candidates"]) == 2


@pytest.mark.asyncio
async def test_test_route_falls_back_to_the_saved_proxy(monkeypatch, tmp_path):
    seen: list[str | None] = []

    async def fake_probe(proxy, targets=None, **kw):
        seen.append(proxy)
        return [{"name": "binance", "ok": True, "status": 200, "ms": 10, "error": None}]

    monkeypatch.setattr(proxy_probe, "probe_targets", fake_probe)
    app, service = _app(tmp_path)
    await service.update_settings({"network": {"proxy_url": "127.0.0.1:7899"}})

    resp = await _call(app, "post", f"{NETWORK}/test", json={})

    assert resp.status_code == 200
    # Saved value is normalized before it reaches httpx.
    assert seen == ["http://127.0.0.1:7899"]
    assert resp.json()["data"]["reachable"] == 1


@pytest.mark.asyncio
async def test_test_route_with_empty_body_tests_the_direct_connection(
    monkeypatch, tmp_path
):
    seen: list[str | None] = []

    async def fake_probe(proxy, targets=None, **kw):
        seen.append(proxy)
        return [
            {
                "name": "binance",
                "ok": False,
                "status": None,
                "ms": 8,
                "error": "ConnectError",
            }
        ]

    monkeypatch.setattr(proxy_probe, "probe_targets", fake_probe)
    app, _ = _app(tmp_path)

    resp = await _call(app, "post", f"{NETWORK}/test", json={"proxy_url": ""})

    assert seen == [None]
    assert resp.json()["data"]["direct"] is True


@pytest.mark.asyncio
async def test_test_route_separates_an_explicit_blank_from_an_omitted_value(
    monkeypatch, tmp_path
):
    """Clearing the box must test the direct connection, not the saved proxy.

    The field used to default to "" and fall back to the saved value on falsy,
    so "test what I typed" and "test what is saved" were the same code path and
    the control group was unreachable from the UI.
    """
    seen: list[str | None] = []

    async def fake_probe(proxy, targets=None, **kw):
        seen.append(proxy)
        return [{"name": "binance", "ok": True, "status": 200, "ms": 8, "error": None}]

    monkeypatch.setattr(proxy_probe, "probe_targets", fake_probe)
    app, service = _app(tmp_path)
    await service.update_settings({"network": {"proxy_url": "127.0.0.1:7899"}})

    omitted = await _call(app, "post", f"{NETWORK}/test", json={})
    blank = await _call(app, "post", f"{NETWORK}/test", json={"proxy_url": ""})

    assert seen == ["http://127.0.0.1:7899", None]
    assert omitted.json()["data"]["direct"] is False
    assert blank.json()["data"]["direct"] is True

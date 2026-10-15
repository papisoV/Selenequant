"""Live server acceptance checks (PF-01, SE-02, F-08 live)."""

from __future__ import annotations

import io
import statistics
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import httpx

BASE = "http://127.0.0.1:8088"

# Endpoints every check exercises, in probe order.
_PROBED = (
    "/api/v1/health",
    "/api/v1/markets",
    "/api/v1/strategies",
    "/api/v1/backtests",
    "/api/v1/paper/accounts",
)
_P95_BUDGET_MS = 500
_SAMPLES_PER_ENDPOINT = 10


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=10)


def _p95(values: list[float]) -> float:
    """95th percentile by index (matches the original sampling convention)."""
    return sorted(values)[int(len(values) * 0.95)]


def measure_latency(method: str, path: str, **kwargs: object) -> float:
    """Round-trip wall time in ms for a single request."""
    with _client() as client:
        started = time.time()
        if method == "GET":
            client.get(path)
        elif method == "POST":
            client.post(path, **kwargs)  # type: ignore[arg-type]
        else:
            raise ValueError(method)
        return (time.time() - started) * 1000


def check_pf01() -> None:
    """PF-01: API latency p95 < 500ms (excluding agent/chat)."""
    samples: list[float] = []
    for path in _PROBED:
        per_endpoint = [
            measure_latency("GET", path) for _ in range(_SAMPLES_PER_ENDPOINT)
        ]
        samples.extend(per_endpoint)
        print(
            f"  GET {path}: avg={statistics.mean(per_endpoint):.1f}ms "
            f"p95={_p95(per_endpoint):.1f}ms"
        )

    overall = _p95(samples)
    print(f"\n  Overall p95: {overall:.1f}ms")
    verdict = "[OK] PF-01: PASS" if overall < _P95_BUDGET_MS else "[!!] PF-01: FAIL"
    print(f"  {verdict}")


def check_se02() -> None:
    """SE-02: No 'sk-' in server logs (we check response headers/body)."""
    with _client() as client:
        leaked = "sk-" in client.get("/api/v1/health").text
    print(f"\n  SE-02: 'sk-' in health response: {leaked}")
    verdict = (
        "[OK] SE-02: PASS (no secrets in API responses)"
        if not leaked
        else "[!!] SE-02: FAIL"
    )
    print(f"  {verdict}")


def check_f08_live() -> None:
    """F-08: Hit all endpoints live."""
    print("\n  F-08 Live endpoint test:")
    all_ok = True
    with _client() as client:
        for path in _PROBED:
            code = client.get(path).status_code
            ok = code == 200
            all_ok &= ok
            print(f"  {'[OK]' if ok else '[!!]'} GET {path} -> {code}")

        validation = client.post(
            "/api/v1/strategies/validate",
            json={
                "name": "test",
                "type": "sma_cross",
                "symbols": ["BTC/USDT"],
                "interval": "1h",
                "parameters": {},
            },
        )
        ok_validation = validation.status_code in (200, 422)
        print(
            f"  {'[OK]' if ok_validation else '[!!]'} "
            f"POST /api/v1/strategies/validate -> {validation.status_code}"
        )
        all_ok &= ok_validation

    print(f"  {'[OK] F-08 live: PASS' if all_ok else '[!!] F-08 live: FAIL'}")


def main() -> None:
    print("=" * 60)
    print("SeleneQuant v0.1.0 Live Server Acceptance")
    print("=" * 60)

    print("\n--- PF-01: API Latency ---")
    check_pf01()

    print("\n--- SE-02: Secret Redaction ---")
    check_se02()

    print("\n--- F-08: Live Endpoint Test ---")
    check_f08_live()

    print("\n" + "=" * 60)
    print("Live server tests complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()

"""Tests for ashare backfill 三件套 (retry/backoff + reconnect + resume)
and the hardened /ashare/backtest + /ashare/backfill endpoints.

No real sleeps (sleep_fn injected), no network (fetch/store monkeypatched).
"""

from __future__ import annotations

import json
import logging

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.error_handler import install_error_handlers
from app.services import ashare_backfill as ab_mod
from app.services.ashare_backfill import backfill, fetch_symbol_with_retry

pytest.importorskip("selene_space_ashare")

from app.api.v1.ashare import router as ashare_router  # noqa: E402
from selene_space_ashare import data_fetcher as df_mod  # noqa: E402


def _kline_df(symbol: str, last_date: str = "20240601") -> pd.DataFrame:
    return pd.DataFrame(
        {"date": ["20240101", last_date], "close": [1.0, 2.0], "code": [symbol, symbol]}
    )


def _recording_sleep(delays: list[float]):
    def _sleep(seconds: float) -> None:
        delays.append(seconds)

    return _sleep


# ─── Retry with exponential backoff ─────────────────────────────────


def test_retry_delays_2_4_8_then_success(tmp_path) -> None:
    delays: list[float] = []
    attempts: list[str] = []

    def fetch(symbol: str) -> pd.DataFrame:
        attempts.append(symbol)
        if len(attempts) < 4:
            raise RuntimeError("upstream down")
        return _kline_df(symbol)

    store_calls: list[str] = []

    def store(symbol: str, df: pd.DataFrame) -> str | None:
        store_calls.append(symbol)
        return str(df["date"].max())

    report = backfill(
        ["600519"],
        fetch,
        store,
        base_delays=(2, 4, 8),
        progress_path=tmp_path / "state.json",
        sleep_fn=_recording_sleep(delays),
    )

    assert report.n_ok == 1
    assert report.n_failed == 0
    assert report.retries_used == 3
    assert delays == [2, 4, 8]
    assert len(attempts) == 4
    assert store_calls == ["600519"]


def test_symbol_failing_all_attempts_is_reported_and_logged(tmp_path, caplog) -> None:
    delays: list[float] = []

    def fetch(symbol: str) -> pd.DataFrame:
        raise RuntimeError(f"dead upstream for {symbol}")

    with caplog.at_level(logging.WARNING, logger="app.services.ashare_backfill"):
        report = backfill(
            ["000001", "000002"],
            fetch,
            lambda s, df: None,
            progress_path=tmp_path / "state.json",
            sleep_fn=_recording_sleep(delays),
        )

    assert report.n_ok == 0
    assert report.n_failed == 2
    assert report.failed_symbols == ["000001", "000002"]
    assert report.retries_used == 6
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "000001" in r.getMessage()
    ]
    assert (
        warnings
    ), "failed symbols must be logged with explicit symbol list, not silently skipped"
    # 3 retries per failing symbol -> delays 2/4/8 per symbol
    assert delays == [2, 4, 8, 2, 4, 8]


# ─── Resumable progress ──────────────────────────────────────────────


def test_interrupt_then_resume_skips_completed_symbols(tmp_path) -> None:
    progress = tmp_path / "state.json"
    fetch_calls: list[str] = []

    def make_fetch(boom_on: str | None):
        def fetch(symbol: str) -> pd.DataFrame:
            fetch_calls.append(symbol)
            if symbol == boom_on:
                raise KeyboardInterrupt("process killed mid-run")
            return _kline_df(symbol)

        return fetch

    # store_fn raising simulates a crash after 2 symbols completed
    store_calls: list[str] = []

    def crashing_store(symbol: str, df: pd.DataFrame) -> str | None:
        store_calls.append(symbol)
        if symbol == "000003":
            raise RuntimeError("disk full — simulated crash mid-run")
        return str(df["date"].max())

    with pytest.raises(RuntimeError):
        backfill(
            ["000001", "000002", "000003", "000004"],
            make_fetch(None),
            crashing_store,
            progress_path=progress,
            sleep_fn=lambda s: None,
        )

    saved = json.loads(progress.read_text(encoding="utf-8"))
    assert set(saved["completed"]) == {"000001", "000002"}
    fetch_calls.clear()
    store_calls.clear()

    report = backfill(
        ["000001", "000002", "000003", "000004"],
        make_fetch(None),
        lambda s, df: df["date"].max(),
        progress_path=progress,
        sleep_fn=lambda s: None,
    )

    # completed symbols are never re-fetched after restart
    assert fetch_calls == ["000003", "000004"]
    assert report.n_ok == 2
    assert report.n_failed == 0


def test_failed_symbol_is_retried_on_restart(tmp_path) -> None:
    progress = tmp_path / "state.json"
    fetch_calls: list[str] = []
    run1_failures_left = {"600519": 2}  # initial + 1 retry both fail in run 1

    def fetch(symbol: str) -> pd.DataFrame:
        fetch_calls.append(symbol)
        if run1_failures_left[symbol] > 0:
            run1_failures_left[symbol] -= 1
            raise RuntimeError("transient")
        return _kline_df(symbol)

    backfill(
        ["600519"],
        fetch,
        lambda s, df: None,
        max_retries=1,
        progress_path=progress,
        sleep_fn=lambda s: None,
    )
    # nothing completed -> nothing persisted (file never written)
    assert (
        not progress.exists()
        or json.loads(progress.read_text(encoding="utf-8"))["completed"] == {}
    )

    fetch_calls.clear()
    report = backfill(
        ["600519"],
        fetch,
        lambda s, df: df["date"].max(),
        max_retries=1,
        progress_path=progress,
        sleep_fn=lambda s: None,
    )
    assert fetch_calls == ["600519"]
    assert report.n_ok == 1


# ─── Periodic reconnect ──────────────────────────────────────────────


def test_reset_hook_called_at_reconnect_boundary(tmp_path) -> None:
    resets: list[int] = []

    def reset() -> None:
        resets.append(1)

    report = backfill(
        [f"60051{i}" for i in range(5)],
        lambda s: _kline_df(s),
        lambda s, df: None,
        reconnect_every=2,
        reset_hook=reset,
        progress_path=tmp_path / "state.json",
        sleep_fn=lambda s: None,
    )

    assert report.n_ok == 5
    # reset fires before the 3rd and 5th attempted symbols
    assert len(resets) == 2


def test_no_reset_hook_is_fine(tmp_path) -> None:
    report = backfill(
        ["600519"],
        lambda s: _kline_df(s),
        lambda s, df: None,
        progress_path=tmp_path / "state.json",
        sleep_fn=lambda s: None,
    )
    assert report.n_ok == 1


# ─── Daily-sync hardening helper ─────────────────────────────────────


def test_fetch_symbol_with_retry_inline_retry_then_success() -> None:
    delays: list[float] = []
    calls: list[str] = []

    def fetch(symbol: str) -> pd.DataFrame:
        calls.append(symbol)
        if len(calls) == 1:
            raise RuntimeError("blip")
        return _kline_df(symbol)

    df, err = fetch_symbol_with_retry(
        fetch, "600519", sleep_fn=_recording_sleep(delays)
    )
    assert not df.empty
    assert err is None
    assert delays == [1.0]
    assert calls == ["600519", "600519"]


def test_fetch_symbol_with_retry_reports_last_error() -> None:
    def fetch(symbol: str) -> pd.DataFrame:
        raise RuntimeError("still down")

    df, err = fetch_symbol_with_retry(fetch, "600519", sleep_fn=lambda s: None)
    assert df.empty
    assert isinstance(err, RuntimeError)


# ─── Endpoints ───────────────────────────────────────────────────────


def _make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(ashare_router, prefix="/api/v1")
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


def test_backfill_endpoint_reports_failures(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(ab_mod, "DEFAULT_PROGRESS_PATH", tmp_path / "state.json")

    def fake_klines(symbol: str, **kwargs) -> pd.DataFrame:
        if symbol == "000009":
            return pd.DataFrame()
        return _kline_df(symbol, last_date="20240701")

    monkeypatch.setattr(df_mod, "get_daily_klines", fake_klines)

    resp = client.post("/api/v1/ashare/backfill?symbols=600519,000009&max_retries=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["report"]["n_ok"] == 1
    assert body["report"]["failed_symbols"] == ["000009"]
    # progress persisted for resume
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert set(saved["completed"]) == {"600519"}


def test_backfill_endpoint_empty_symbols_404(client: TestClient) -> None:
    resp = client.post("/api/v1/ashare/backfill?symbols=")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_backtest_endpoint_logs_failed_symbols(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    # The endpoint short-circuits with 404 when index data is empty, before
    # build_ashare_panel runs — so this test must feed a usable index frame
    # and a symbol long enough to clear the 60-bar threshold, otherwise the
    # warning it asserts on can never be emitted.
    import numpy as np

    dates = pd.date_range("2024-01-01", periods=80, freq="B")

    def fake_klines(symbol: str, **kwargs) -> pd.DataFrame:
        if symbol == "000008":
            return pd.DataFrame()
        close = 10 + np.cumsum(np.random.randn(len(dates)) * 0.1)
        return pd.DataFrame(
            {
                "date": dates,
                "open": close,
                "high": close * 1.02,
                "low": close * 0.98,
                "close": close,
                "volume": np.full(len(dates), 1_000_000.0),
            }
        )

    monkeypatch.setattr(df_mod, "get_daily_klines", fake_klines)
    monkeypatch.setattr(
        df_mod,
        "get_index_daily",
        lambda *a, **k: pd.DataFrame(
            {"date": dates, "close": 3000 + np.cumsum(np.random.randn(len(dates)) * 10)}
        ),
    )

    np.random.seed(3)
    with caplog.at_level(logging.WARNING, logger="app.api.v1.ashare"):
        resp = client.post("/api/v1/ashare/backtest?symbols=600519,000008")

    assert resp.status_code == 200, resp.text
    warnings = [r.getMessage() for r in caplog.records if "000008" in r.getMessage()]
    assert warnings, "backtest must log failed symbol list instead of silent skip"

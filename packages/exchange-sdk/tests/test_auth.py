"""Credential masking and Binance/OKX request signing."""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest
from pydantic import SecretStr

from selene_space_exchange.base.auth import (
    BinanceAuthenticator,
    ExchangeCredentials,
    OKXAuthenticator,
    _check_clock_drift,
    _get_binance_recv_window,
    DEFAULT_BINANCE_RECV_WINDOW,
)


def _creds(key: str, secret: str, passphrase: str | None = None) -> ExchangeCredentials:
    return ExchangeCredentials(
        api_key=SecretStr(key),
        api_secret=SecretStr(secret),
        passphrase=SecretStr(passphrase) if passphrase else None,
    )


class TestCredentialMasking:
    def test_repr_leaks_nothing(self) -> None:
        creds = _creds("my-key", "my-secret")
        rendered = str(creds)
        assert ("my-key" in rendered, "my-secret" in rendered) == (False, False)
        assert creds.api_key.get_secret_value() == "my-key"

    def test_passphrase_defaults_absent(self) -> None:
        assert _creds("k", "s").passphrase is None


class TestBinanceSigning:
    KEY, SECRET = "test-api-key", "test-secret"
    TS = 1000

    def _auth(self) -> BinanceAuthenticator:
        return BinanceAuthenticator(_creds(self.KEY, self.SECRET))

    @staticmethod
    def _canonical_query(params: dict) -> str:
        return "&".join(f"{k}={v}" for k, v in sorted(params.items()) if k != "signature")

    def test_api_key_header_present(self) -> None:
        headers = self._auth().sign_request(
            "POST", "/api/v3/order",
            params={"symbol": "BTCUSDT", "side": "BUY"},
            timestamp=1_000_000,
        )
        assert headers["X-MBX-APIKEY"] == self.KEY

    def test_timestamp_and_signature_injected_into_params(self) -> None:
        params = {"symbol": "BTCUSDT", "side": "BUY"}
        self._auth().sign_request("POST", "/api/v3/order", params=params, timestamp=self.TS)
        assert ("signature" in params, params["timestamp"]) == (True, str(self.TS))

    def test_signature_matches_hmac_of_sorted_query(self) -> None:
        params = {"symbol": "BTCUSDT", "side": "BUY"}
        self._auth().sign_request("POST", "/api/v3/order", params=params, timestamp=12345)
        digest = hmac.new(
            self.SECRET.encode(),
            self._canonical_query(params).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert params["signature"] == digest

    def test_missing_timestamp_becomes_now(self) -> None:
        params: dict = {}
        self._auth().sign_request("GET", "/test", params=params)
        assert int(params["timestamp"]) > 0


class TestBinanceRecvWindow:
    def _signed_params(self, **sign_kwargs) -> dict:
        auth = BinanceAuthenticator(_creds("test-api-key", "test-secret"))
        params = {"symbol": "BTCUSDT"}
        auth.sign_request("POST", "/api/v3/order", params=params, timestamp=1000, **sign_kwargs)
        return params

    def test_default_window_appended(self) -> None:
        assert self._signed_params()["recvWindow"] == str(DEFAULT_BINANCE_RECV_WINDOW)

    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SELENE_BINANCE_RECV_WINDOW", "15000")
        assert self._signed_params()["recvWindow"] == "15000"

    def test_explicit_kwarg_beats_everything(self) -> None:
        assert self._signed_params(recv_window=99999)["recvWindow"] == "99999"

    def test_garbage_env_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SELENE_BINANCE_RECV_WINDOW", "not-a-number")
        assert _get_binance_recv_window() == DEFAULT_BINANCE_RECV_WINDOW


class TestClockDriftGuard:
    HALF_DEFAULT = DEFAULT_BINANCE_RECV_WINDOW // 2

    def test_large_skew_warns_and_reports(self, caplog: pytest.LogCaptureFixture) -> None:
        server, local = 100_000, 1_000
        with caplog.at_level("WARNING"):
            drift = _check_clock_drift(server_time=server, local_time=local)
        assert drift == server - local
        assert any("Clock drift" in r.message for r in caplog.records)

    def test_small_skew_stays_quiet(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("WARNING"):
            drift = _check_clock_drift(server_time=2_000, local_time=1_000)
        assert drift == 1_000 <= self.HALF_DEFAULT
        assert not any("Clock drift" in r.message for r in caplog.records)


class TestOKXSigning:
    KEY, SECRET, PASS = "okx-key", "okx-secret", "okx-pass"
    TS = "2020-12-08T09:08:57.000Z"
    PATH = "/api/v5/trade/order"
    BODY = '{"instId":"BTC-USDT"}'

    def _auth(self) -> OKXAuthenticator:
        return OKXAuthenticator(_creds(self.KEY, self.SECRET, self.PASS))

    def test_full_header_set_returned(self) -> None:
        headers = self._auth().sign_request("POST", self.PATH, body=self.BODY, timestamp=self.TS)
        checks = {
            "key": headers["OK-ACCESS-KEY"] == self.KEY,
            "pass": headers["OK-ACCESS-PASSPHRASE"] == self.PASS,
            "ts": headers["OK-ACCESS-TIMESTAMP"] == self.TS,
            "sign": "OK-ACCESS-SIGN" in headers,
        }
        assert all(checks.values()), f"missing: {[k for k, ok in checks.items() if not ok]}"

    def test_signature_is_base64_hmac_over_prehash(self) -> None:
        headers = self._auth().sign_request("POST", self.PATH, body=self.BODY, timestamp=self.TS)
        prehash = self.TS + "POST" + self.PATH + self.BODY
        want = base64.b64encode(
            hmac.new(self.SECRET.encode(), prehash.encode("utf-8"), hashlib.sha256).digest()
        ).decode("utf-8")
        assert headers["OK-ACCESS-SIGN"] == want

    def test_get_query_participates_in_signing(self) -> None:
        headers = self._auth().sign_request(
            "GET", "/api/v5/account/balance",
            params={"ccy": "BTC"},
            timestamp=self.TS,
        )
        assert "OK-ACCESS-SIGN" in headers

    def test_passphrase_required_for_okx(self) -> None:
        auth = OKXAuthenticator(_creds("k", "s"))
        with pytest.raises(ValueError, match="passphrase"):
            auth.sign_request("GET", "/test")

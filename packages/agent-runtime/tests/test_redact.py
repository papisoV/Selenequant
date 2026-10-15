"""Behaviour locks for redact_secrets: recursive masking of sensitive keys."""

from __future__ import annotations

from typing import Any

from selene_space_agent.redact import redact_secrets

_REDACTED = "***REDACTED***"


class TestRedactSecrets:
    def test_nested_sensitive_keys_redacted(self) -> None:
        payload: dict[str, Any] = {
            "user": {
                "api_key": "sk-live-1234",
                "access_key": "AKIA1234",
                "profile": {"secret_token": "abc"},
            },
            "settings": {"read_only": True},
            "list": [
                {"token": "tok-1", "name": "alice"},
                {"name": "bob"},
            ],
        }
        result = redact_secrets(payload)

        checks = (
            result["user"]["api_key"] == _REDACTED,
            result["user"]["access_key"] == _REDACTED,
            result["user"]["profile"]["secret_token"] == _REDACTED,
            result["settings"]["read_only"] is True,
            result["list"][0]["token"] == _REDACTED,
            result["list"][0]["name"] == "alice",
            result["list"][1]["name"] == "bob",
        )
        assert all(checks)
        # Original must be untouched (immutability).
        assert payload["user"]["api_key"] == "sk-live-1234"

    def test_no_sensitive_keys_unchanged(self) -> None:
        payload = {"a": 1, "b": {"c": [1, 2, 3]}, "d": "plain"}
        assert redact_secrets(payload) == {"a": 1, "b": {"c": [1, 2, 3]}, "d": "plain"}

    def test_scalar_passthrough(self) -> None:
        assert redact_secrets(42) == 42
        assert redact_secrets("hello") == "hello"
        assert redact_secrets(None) is None

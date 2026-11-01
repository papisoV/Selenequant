"""Recursive secret redaction for outbound payloads.

QD's redact_secrets idea: any value carried under a sensitive key name is
masked before it crosses a service boundary, so API keys, tokens and secrets
never leak through MCP tool responses or skill listings.
"""

from __future__ import annotations

from typing import Any

_SENSITIVE_KEY_WORDS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "passwd",
    "access_key",
)

_REDACTED = "***REDACTED***"


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(word in lowered for word in _SENSITIVE_KEY_WORDS)


def redact_secrets(obj: Any) -> Any:
    """Return a deep copy of *obj* with sensitive values masked in place.

    - dict values whose key contains api_key/secret/token/password are replaced
      with ``***REDACTED***``
    - lists/tuples are traversed recursively
    - scalars are returned unchanged

    Mutating nothing: a fresh structure is built on the way out.
    """
    if isinstance(obj, dict):
        redacted: dict[str, Any] = {}
        for key, value in obj.items():
            if _is_sensitive_key(str(key)):
                redacted[key] = _REDACTED
            else:
                redacted[key] = redact_secrets(value)
        return redacted
    if isinstance(obj, (list, tuple)):
        return type(obj)(redact_secrets(item) for item in obj)
    return obj

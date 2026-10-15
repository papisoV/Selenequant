"""Application-layer field encryption for stored secrets.

The platform encrypts secret material at rest with Fernet when an
encryption key is configured. This module answers the single operational
question the settings surface needs: is application-layer encryption
currently active?

Activation is driven by the ``SELENE_ENCRYPTION_KEY`` environment
variable: present (and non-blank) means active; absent or blank means
secrets fall back to OS-keychain-only protection and callers should warn.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from collections.abc import Mapping
from typing import Any

__all__ = [
    "ENCRYPTION_KEY_ENV",
    "decrypt_value",
    "encrypt_value",
    "encryption_status",
]

logger = logging.getLogger(__name__)

ENCRYPTION_KEY_ENV = "SELENE_ENCRYPTION_KEY"
_SALT_ENV = "SELENE_ENCRYPTION_SALT"

# Prefix marking a value as Fernet ciphertext. Its absence means the value
# was stored before encryption was enabled (or while it was off) and must be
# passed through untouched — see decrypt_value.
_FERNET_PREFIX = "fernet:"

# Development fallback salt. Production must set SELENE_ENCRYPTION_SALT; a
# shared default salt would let anyone with the source derive the same key.
_DEV_SALT = b"selenespace-dev-salt-v1"

_fernet_cache: Any = None  # cryptography.fernet.Fernet | None, resolved lazily


def _salt() -> bytes:
    """PBKDF2 salt from the environment, with a guarded dev default."""
    raw = os.environ.get(_SALT_ENV, "").strip()
    if raw:
        return raw.encode("utf-8")
    if os.environ.get("SELENE_ENV", "development").strip().lower() == "production":
        raise RuntimeError(
            f"{_SALT_ENV} must be set in production. Generate one with: "
            "python -c \"import secrets; print(secrets.token_hex(16))\""
        )
    logger.warning("Using development encryption salt — not secure for production")
    return _DEV_SALT


def _derive_key(raw: str) -> bytes:
    """Turn the configured secret into a Fernet-compatible key.

    Accepts either a ready-made urlsafe-base64 32-byte key, or an arbitrary
    passphrase (stretched with PBKDF2). This keeps ops simple: any strong
    string works, and a properly generated key also works unchanged.
    """
    candidate = raw.strip()
    try:
        decoded = base64.urlsafe_b64decode(candidate)
        if len(decoded) == 32:
            return candidate.encode("ascii")
    except Exception:
        pass  # not a base64 key — fall through to derivation

    derived = hashlib.pbkdf2_hmac("sha256", candidate.encode("utf-8"), _salt(), 100_000)
    return base64.urlsafe_b64encode(derived)


def _get_fernet() -> Any:
    """Return a cached Fernet instance, or None when encryption is unavailable.

    Unavailable covers two cases: the ``cryptography`` package is missing, or
    no key is configured. Both mean "store plaintext and rely on the OS
    keychain"; callers must not treat that as an error.
    """
    global _fernet_cache
    if _fernet_cache is not None:
        return _fernet_cache

    raw = os.environ.get(ENCRYPTION_KEY_ENV, "").strip()
    if not raw:
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        logger.warning(
            "cryptography not installed; field encryption disabled — "
            "secrets rely on the OS keychain alone"
        )
        return None

    try:
        _fernet_cache = Fernet(_derive_key(raw))
    except Exception:
        logger.error("Invalid %s — cannot initialise field encryption", ENCRYPTION_KEY_ENV)
        return None
    return _fernet_cache


def encrypt_value(plaintext: str) -> str:
    """Encrypt ``plaintext``, returning a ``fernet:``-prefixed token.

    Returns the input unchanged when encryption is unavailable, so callers
    can store the result without branching. Empty input is returned as-is.
    """
    if not plaintext:
        return plaintext
    fernet = _get_fernet()
    if fernet is None:
        return plaintext
    token = fernet.encrypt(plaintext.encode("utf-8"))
    return _FERNET_PREFIX + token.decode("ascii")


def decrypt_value(stored: str) -> str:
    """Decrypt a value produced by :func:`encrypt_value`.

    Values without the ``fernet:`` prefix are returned unchanged — that is
    how secrets stored before encryption was enabled stay readable.

    Raises:
        ValueError: the prefix is present but the payload cannot be decrypted
            (wrong key, or corrupted data). Callers that would rather degrade
            than fail should catch this and fall back to the raw value.
            ``cryptography`` raises its own ``InvalidToken`` for this case, so
            it is translated here — the documented contract is ``ValueError``,
            and a caller catching only that must not be surprised by a crash.
    """
    if not stored or not stored.startswith(_FERNET_PREFIX):
        return stored
    fernet = _get_fernet()
    if fernet is None:
        raise ValueError(
            f"value is {_FERNET_PREFIX}-encrypted but {ENCRYPTION_KEY_ENV} is not set"
        )
    token = stored[len(_FERNET_PREFIX) :].encode("ascii")
    try:
        return fernet.decrypt(token).decode("utf-8")
    except Exception as exc:  # InvalidToken, UnicodeDecodeError, bad base64 …
        raise ValueError(f"could not decrypt stored value: {exc}") from exc


def encryption_status() -> Mapping[str, Any]:
    """Report whether application-layer (Fernet) field encryption is on.

    Returns a mapping whose required key ``field_encryption_active`` is:
      - True  — encryption key configured; secrets are Fernet-encrypted;
      - False — key missing; secrets rely on the OS keychain alone.

    Extra informational keys may be present; consumers must not depend on
    them.
    """
    active = _get_fernet() is not None
    return {
        "field_encryption_active": active,
        "scheme": "fernet" if active else None,
    }

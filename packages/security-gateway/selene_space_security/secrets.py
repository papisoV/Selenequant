"""Secret resolution with leak-resistant handles.

Design:

- :class:`SecretRef` is a plain immutable *pointer* to where a secret
  lives (env var, file, OS keyring). Refs are hashable and carry no
  sensitive material, so they can flow freely through caches and logs.
- :class:`ResolvedSecret` is the opposite: it holds plaintext but makes
  accidental disclosure hard — its ``str``/``repr`` never contain the
  value, and the only way out is an explicit ``use()`` call.
- :class:`SecretManager` performs async resolution with per-instance
  caching keyed by ref equality.
"""

from __future__ import annotations

import enum
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "ResolvedSecret",
    "SecretManager",
    "SecretRef",
    "SecretResolutionError",
    "SecretSource",
]

# A file secret larger than this is refused — secret files are small by
# nature; anything bigger usually indicates a wrong path or an attack.
_MAX_SECRET_FILE_BYTES = 2_000_000

# POSIX permission mask: any read bit for group/other is rejected.
_GROUP_OTHER_READ = 0o044


class SecretSource(enum.Enum):
    """Where a secret physically lives."""

    ENV = "env"
    FILE = "file"
    KEYRING = "keyring"


class SecretResolutionError(Exception):
    """Raised when a secret cannot be resolved, stored, or removed.

    Messages are written for ``pytest.raises(match=...)`` style checks:
    they name the failure cause plainly ("not found", "exceeds",
    "readable by group", ...) without ever embedding the secret value.
    """


@dataclass(frozen=True)
class SecretRef:
    """Immutable pointer to a secret location. Safe as a dict key."""

    source: SecretSource
    id: str
    provider: Optional[str] = None


class ResolvedSecret:
    """Plaintext secret wrapped in a disclosure-resistant shell."""

    __slots__ = ("_value", "source", "ref")

    def __init__(self, value: str, source: SecretSource, ref: SecretRef) -> None:
        self._value = value
        self.source = source
        self.ref = ref

    def use(self) -> str:
        """The only sanctioned way to obtain the plaintext."""
        return self._value

    def __str__(self) -> str:
        return "***"

    def __repr__(self) -> str:
        return (
            f"<ResolvedSecret redacted source={self.source.value} "
            f"ref_id={self.ref.id!r}>"
        )


def _require_keyring_module():
    """Import and return the ``keyring`` module or raise the contract error."""
    try:
        import keyring  # noqa: PLC0415 — deliberately lazy: optional dep
    except ImportError as exc:
        raise SecretResolutionError(
            "keyring library not installed; install it to use KEYRING-backed "
            "secrets (e.g. pip install keyring)"
        ) from exc
    return keyring


class SecretManager:
    """Resolves, stores, and deletes secrets across ENV / FILE / KEYRING."""

    def __init__(
        self,
        *,
        base_dir: Path | None = None,
        keyring_required_for_store: bool = True,
    ) -> None:
        self._base_dir = base_dir
        self._keyring_required_for_store = keyring_required_for_store
        self._cache: dict[SecretRef, ResolvedSecret] = {}

    # -- resolution ---------------------------------------------------------

    async def resolve(self, ref: SecretRef) -> ResolvedSecret:
        """Fetch a secret value by reference, memoizing successful lookups.

        Args:
            ref: Typed pointer to the secret (env var, file, or keyring
                entry). The source enum on the ref selects the backend.

        Returns:
            The resolved secret; repeated calls with the same ref hit the
            in-memory cache and never re-read the backend.

        Raises:
            SecretResolutionError: Backend has no such entry, or a FILE
                secret is missing/oversized/group-readable.
        """
        cached = self._cache.get(ref)
        if cached is not None:
            return cached
        resolved = await self._resolve_uncached(ref)
        self._cache[ref] = resolved
        return resolved

    async def _resolve_uncached(self, ref: SecretRef) -> ResolvedSecret:
        if ref.source is SecretSource.ENV:
            value = os.environ.get(ref.id)
            if value is None:
                raise SecretResolutionError(
                    f"Environment variable {ref.id!r} not found"
                )
        elif ref.source is SecretSource.FILE:
            value = self._read_file_secret(ref)
        elif ref.source is SecretSource.KEYRING:
            keyring = _require_keyring_module()
            value = keyring.get_password(ref.provider or "", ref.id)
            if value is None:
                raise SecretResolutionError(
                    f"No keyring entry for provider={ref.provider!r} id={ref.id!r}"
                )
            # Values written with encryption on carry a `fernet:` prefix; plain
            # ones (written before encryption was enabled) pass through. A wrong
            # or rotated key must not brick the app — degrade to the raw value.
            from selene_space_security.encryption import decrypt_value

            try:
                value = decrypt_value(value)
            except ValueError:
                logger.warning(
                    "keyring entry provider=%r id=%r could not be decrypted; "
                    "using the stored value as-is",
                    ref.provider,
                    ref.id,
                )
        else:  # pragma: no cover - enum exhaustiveness guard
            raise SecretResolutionError(f"Unsupported secret source: {ref.source}")

        return ResolvedSecret(value=value, source=ref.source, ref=ref)

    def _file_path(self, ref: SecretRef) -> Path:
        if self._base_dir is None:
            raise SecretResolutionError(
                f"FILE secrets require base_dir on SecretManager "
                f"(ref id={ref.id!r}, provider={ref.provider!r})"
            )
        path = (
            self._base_dir / ref.id
            if not ref.provider
            else self._base_dir / ref.provider / ref.id
        )
        return path

    def _read_file_secret(self, ref: SecretRef) -> str:
        path = self._file_path(ref)
        if not path.is_file():
            raise SecretResolutionError(f"Secret file {str(path)!r} not found")

        if os.name == "posix":
            mode = path.stat().st_mode & 0o777
            if mode & _GROUP_OTHER_READ:
                raise SecretResolutionError(
                    f"Secret file {str(path)!r} is readable by group or others "
                    f"(mode {oct(mode)})"
                )

        size = path.stat().st_size
        if size >= _MAX_SECRET_FILE_BYTES:
            raise SecretResolutionError(
                f"Secret file {str(path)!r} exceeds {_MAX_SECRET_FILE_BYTES} bytes"
            )

        # Trailing newline stripped so `echo value > file` round-trips.
        return path.read_text(encoding="utf-8").rstrip("\n")

    # -- storage -------------------------------------------------------------

    async def store(self, ref: SecretRef, value: str) -> None:
        """Persist ``value`` under ``ref``, invalidating any cache entry.

        Args:
            ref: Destination pointer. KEYRING always works; FILE requires
                the manager to be built with
                ``keyring_required_for_store=False``; ENV cannot be stored.

        Raises:
            SecretResolutionError: Source does not support storage or the
                keyring opt-out is missing.
        """
        if ref.source is SecretSource.KEYRING:
            keyring = _require_keyring_module()
            # Encrypt before writing when a key is configured; encrypt_value
            # returns the value unchanged otherwise, so the OS keychain is the
            # only protection in that case (see encryption_status).
            from selene_space_security.encryption import encrypt_value

            keyring.set_password(ref.provider or "", ref.id, encrypt_value(value))
            self._cache.pop(ref, None)
            return

        if self._keyring_required_for_store:
            raise SecretResolutionError(
                f"Storing to source {ref.source.value} requires keyring backend; "
                "construct SecretManager(keyring_required_for_store=False) to allow file storage"
            )

        if ref.source is SecretSource.FILE:
            path = self._file_path(ref)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
            # Tighten to owner-only immediately. Without this the file lands at
            # the process umask default (0o644 on Linux), which the read path
            # rejects outright — and until it is fixed the secret is readable by
            # every other user on the box. POSIX-only; Windows has no mode bits.
            if os.name == "posix":
                try:
                    os.chmod(path, 0o600)
                except OSError:  # pragma: no cover - unusual fs/permissions
                    logger.warning(
                        "could not restrict permissions on %r; secret file may be "
                        "readable by other users",
                        str(path),
                    )
            self._cache.pop(ref, None)
            return

        raise SecretResolutionError(
            f"Storing to source {ref.source.value} requires keyring backend"
        )

    async def delete(self, ref: SecretRef) -> None:
        """Remove the secret behind ``ref`` from its backend and cache.

        Raises:
            SecretResolutionError: Source does not support deletion (e.g. ENV).
        """
        if ref.source is SecretSource.KEYRING:
            keyring = _require_keyring_module()
            keyring.delete_password(ref.provider or "", ref.id)
        elif ref.source is SecretSource.FILE:
            path = self._file_path(ref)
            path.unlink(missing_ok=True)
        else:
            raise SecretResolutionError(
                f"Deleting from source {ref.source.value} requires keyring backend"
            )
        self._cache.pop(ref, None)

    async def exists(self, ref: SecretRef) -> bool:
        try:
            await self.resolve(ref)
            return True
        except SecretResolutionError:
            return False

    # -- cache ---------------------------------------------------------------

    def clear_cache(self) -> None:
        self._cache.clear()

    # -- introspection -------------------------------------------------------

    def keyring_available(self) -> bool:
        try:
            import keyring  # noqa: F401,PLC0415
        except ImportError:
            return False
        return True

    def keyring_backend_info(self) -> dict[str, Any]:
        """Describe the active keyring backend for settings UIs.

        Returns at least ``{"backend": <name>, "is_secure_backend": <bool>}``.
        """
        if not self.keyring_available():
            return {"backend": "unknown", "is_secure_backend": False}
        try:
            import keyring  # noqa: PLC0415

            backend = getattr(keyring, "get_keyring", lambda: None)()
            name = type(backend).__name__ if backend is not None else "unknown"
            module = type(backend).__module__ if backend is not None else ""
            insecure_markers = ("fail", "null", "chainer")
            is_secure = not any(
                marker in module.lower() or marker in name.lower()
                for marker in insecure_markers
            )
            return {"backend": name, "is_secure_backend": is_secure}
        except Exception:  # pragma: no cover - defensive: never crash settings UI
            return {"backend": "unknown", "is_secure_backend": False}

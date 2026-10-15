"""Directory-persisted device tokens with per-device replacement semantics."""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path

TOKENS_FILE_NAME = "tokens.json"

_DEFAULT_TTL_SECONDS = 86400.0


@dataclass(frozen=True)
class PairingToken:
    token: str
    device_id: str
    scopes: list[str]
    created_at: float
    expires_at: float


class TokenStore:
    def __init__(self, store_dir: Path) -> None:
        self._store_dir = Path(store_dir)
        self._file_path = self._store_dir / TOKENS_FILE_NAME
        self._tokens_by_device: dict[str, PairingToken] = {}
        self._load()

    def issue_token(
        self,
        device_id: str,
        scopes: list[str] | None = None,
        ttl_seconds: float | None = None,
    ) -> PairingToken:
        now = time.time()
        effective_ttl = _DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        issued = PairingToken(
            token=secrets.token_urlsafe(32),
            device_id=device_id,
            scopes=list(scopes) if scopes is not None else ["read"],
            created_at=now,
            expires_at=now + effective_ttl,
        )
        self._tokens_by_device[device_id] = issued
        self._flush()
        return issued

    def validate_token(self, token_str: str) -> PairingToken | None:
        for held in list(self._tokens_by_device.values()):
            if held.token != token_str or held.expires_at <= time.time():
                continue
            return held
        return None

    def revoke_token(self, device_id: str) -> bool:
        if device_id not in self._tokens_by_device:
            return False
        del self._tokens_by_device[device_id]
        self._flush()
        return True

    def revoke_all(self) -> int:
        count = len(self._tokens_by_device)
        self._tokens_by_device.clear()
        self._flush()
        return count

    def _load(self) -> None:
        self._store_dir.mkdir(parents=True, exist_ok=True)
        try:
            raw = self._file_path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return
        try:
            payload = json.loads(raw)
            assert isinstance(payload, dict)
        except (ValueError, AssertionError):
            return
        revived: dict[str, PairingToken] = {}
        for device_id, entry in payload.items():
            try:
                revived[device_id] = PairingToken(
                    token=entry["token"],
                    device_id=entry["device_id"],
                    scopes=list(entry.get("scopes") or []),
                    created_at=float(entry["created_at"]),
                    expires_at=float(entry["expires_at"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
        self._tokens_by_device = revived

    def _flush(self) -> None:
        payload = {
            device_id: asdict(held)
            for device_id, held in self._tokens_by_device.items()
        }
        tmp_path = self._file_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(self._file_path)

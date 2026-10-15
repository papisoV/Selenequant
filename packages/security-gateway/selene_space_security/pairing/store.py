"""Directory-persisted pending pairing requests."""

from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

PENDING_FILE_NAME = "pending.json"

CODE_LENGTH = 8

UNAMBIGUOUS_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz"

MAX_PENDING = 20

_DEFAULT_TTL_SECONDS = 300.0


@dataclass(frozen=True)
class PairingRequest:
    code: str
    created_at: float
    expires_at: float
    device_info: dict = field(default_factory=dict)


class PairingStore:
    def __init__(
        self,
        store_dir: Path,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
    ) -> None:
        self._store_dir = Path(store_dir)
        self._ttl_seconds = ttl_seconds
        self._file_path = self._store_dir / PENDING_FILE_NAME
        self._requests: dict[str, PairingRequest] = {}
        self._load()

    def generate_code(self) -> str:
        return "".join(
            random.SystemRandom().choice(UNAMBIGUOUS_ALPHABET)
            for _ in range(CODE_LENGTH)
        )

    def create_request(
        self,
        device_info: dict | None = None,
    ) -> PairingRequest:
        self._purge_expired()
        now = time.time()
        code = self.generate_code()
        while code in self._requests:
            code = self.generate_code()
        request = PairingRequest(
            code=code,
            created_at=now,
            expires_at=now + self._ttl_seconds,
            device_info=dict(device_info or {}),
        )
        if len(self._requests) >= MAX_PENDING:
            self._drop_oldest()
        self._requests[code] = request
        self._flush()
        return request

    def get_pending(self) -> list[PairingRequest]:
        self._purge_expired()
        self._flush()
        ordered = sorted(self._requests.values(), key=lambda r: r.created_at)
        return list(ordered)

    def remove_request(self, code: str) -> bool:
        key = code.strip().lower()
        for stored_code in list(self._requests):
            if stored_code.lower() == key:
                del self._requests[stored_code]
                self._flush()
                return True
        return False

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [
            code
            for code, request in self._requests.items()
            if request.expires_at <= now
        ]
        for code in expired:
            del self._requests[code]
        if expired:
            self._flush()

    def _drop_oldest(self) -> None:
        if not self._requests:
            return
        oldest = min(self._requests.values(), key=lambda r: r.created_at)
        del self._requests[oldest.code]

    def _load(self) -> None:
        self._store_dir.mkdir(parents=True, exist_ok=True)
        try:
            raw = self._file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError:
            return
        try:
            payload = json.loads(raw)
            assert isinstance(payload, list)
        except (ValueError, AssertionError):
            self._requests = {}
            return
        revived: dict[str, PairingRequest] = {}
        for entry in payload:
            try:
                request = PairingRequest(
                    code=entry["code"],
                    created_at=float(entry["created_at"]),
                    expires_at=float(entry["expires_at"]),
                    device_info=dict(entry.get("device_info") or {}),
                )
            except (KeyError, TypeError, ValueError):
                continue
            revived[request.code] = request
        self._requests = revived

    def _flush(self) -> None:
        payload = [asdict(request) for request in self._requests.values()]
        tmp_path = self._file_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(self._file_path)

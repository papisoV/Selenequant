"""One-shot pairing code verification."""

from __future__ import annotations

from dataclasses import dataclass

from selene_space_security.pairing.store import PairingStore

# Security invariant: every failure mode shares one message so the error
# surface cannot be used as an oracle for whether a code exists.
_GENERIC_ERROR = "配对码无效或已过期，请重新发起配对。"


@dataclass(frozen=True)
class ChallengeResult:
    success: bool
    device_info: dict | None = None
    error: str | None = None


class PairingChallenge:
    def __init__(self, store: PairingStore) -> None:
        self._store = store

    def verify_code(self, code: str) -> ChallengeResult:
        normalized = code.strip()
        pending = {
            request.code.lower(): request for request in self._store.get_pending()
        }
        match = pending.get(normalized.lower())
        if match is None:
            return ChallengeResult(
                success=False,
                device_info=None,
                error=_GENERIC_ERROR,
            )
        self._store.remove_request(match.code)
        return ChallengeResult(
            success=True,
            device_info=dict(match.device_info),
            error=None,
        )

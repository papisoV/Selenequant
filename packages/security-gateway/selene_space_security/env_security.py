"""Subprocess environment sanitization.

Guards the boundary between this application and any child process it
spawns. Each environment variable key is checked against three
independent questions:

1. Is the key outright dangerous to inject into any child environment?
   (dynamic-loader hijacks, interpreter path smuggling, shell hooks)
2. Is the key dangerous specifically as an *override* written on top of
   an inherited environment? (credential/agent redirection vectors that
   are benign when merely inherited but hostile when replaced)
3. Does the key look like it carries a credential? Such keys are still
   passed through (children frequently need them to operate) but are
   flagged so callers can log/monitor their use.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

__all__ = [
    "EnvSanitizationResult",
    "is_dangerous_env_key",
    "is_dangerous_override_key",
    "is_secret_env_key",
    "sanitize_env",
]

# --- injection vectors: forbidden in ANY injected environment -------------

_BASE_EXACT_DANGEROUS: frozenset[str] = frozenset(
    {
        "NODE_OPTIONS",  # arbitrary preload flags into every node child
        "PYTHONPATH",  # shadow stdlib/site modules
        "BASH_ENV",  # sourced by non-interactive bash
        "IFS",  # field-splitting tampering for shell scripts
        "SSLKEYLOGFILE",  # TLS session key exfiltration
    }
)

_BASE_PREFIX_DANGEROUS: tuple[str, ...] = (
    "LD_PRELOAD",  # ELF preload
    "LD_LIBRARY_PATH",  # shared library shadowing
    "DYLD_INSERT_LIBRARIES",  # macOS Mach-O preload
    "BASH_FUNC_",  # exported-function (Shellshock-style) definitions
)

# --- escalation vectors: forbidden only for OVERRIDE writes ---------------

_OVERRIDE_EXACT_DANGEROUS: frozenset[str] = frozenset(
    {
        "HOME",  # redirect dotfile/config resolution
        "GIT_SSH_COMMAND",  # arbitrary command execution via git transport
        "EDITOR",  # often spawned with attacker-influenced content
        "VISUAL",
        "PROMPT_COMMAND",  # executed by interactive bash after each command
    }
)

_OVERRIDE_PREFIX_DANGEROUS: tuple[str, ...] = (
    "GIT_CONFIG_",  # git config injection (incl. GIT_CONFIG_GLOBAL)
    "NPM_CONFIG_",  # npm registry/scripts redirection
)

# --- credential-looking keys: flagged, not blocked -------------------------

_KNOWN_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "AWS_SECRET_ACCESS_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "BINANCE_API_SECRET",
    }
)

_SECRET_MARKERS: tuple[str, ...] = (
    "API_KEY",
    "PASSWORD",
    "ACCESS_TOKEN",
    "TOKEN",
    "PRIVATE_KEY",
)

# Values above this length are rejected outright: they are never legitimate
# environment payloads and usually signal a blob smuggled through env.
_MAX_VALUE_CHARS = 32_768


def _normalize_key(key: str) -> str:
    """Canonical comparison form: trimmed and case-folded."""
    return key.strip().upper()


def is_dangerous_env_key(key: str) -> bool:
    """True if ``key`` must never enter a child-process environment."""
    k = _normalize_key(key)
    if k in _BASE_EXACT_DANGEROUS:
        return True
    return any(k.startswith(prefix) for prefix in _BASE_PREFIX_DANGEROUS)


def is_dangerous_override_key(key: str) -> bool:
    """True if ``key`` must not *overwrite* an existing environment entry.

    Strict superset of :func:`is_dangerous_env_key`.
    """
    if is_dangerous_env_key(key):
        return True
    k = _normalize_key(key)
    if k in _OVERRIDE_EXACT_DANGEROUS:
        return True
    return any(k.startswith(prefix) for prefix in _OVERRIDE_PREFIX_DANGEROUS)


def is_secret_env_key(key: str) -> bool:
    """True if ``key`` looks like it carries a credential value."""
    k = _normalize_key(key)
    if k in _KNOWN_SECRET_KEYS:
        return True
    return any(marker in k for marker in _SECRET_MARKERS)


@dataclass(frozen=True)
class EnvSanitizationResult:
    """Outcome of filtering an environment mapping.

    Attributes:
        allowed: key/value pairs cleared for hand-off to a child process.
        blocked: keys refused outright (dangerous, NUL payload, oversized).
        secrets_detected: keys that were allowed but look like credentials;
            informational only, the value remains in ``allowed``.
    """

    allowed: dict[str, str] = field(default_factory=dict)
    blocked: list[str] = field(default_factory=list)
    secrets_detected: list[str] = field(default_factory=list)


def _admit(
    key: str,
    value: str,
    *,
    override_semantics: bool,
    allowed: dict[str, str],
    blocked: list[str],
    secrets_detected: list[str],
) -> None:
    """Apply the per-key admission rules, mutating the accumulators."""
    danger_check = (
        is_dangerous_override_key if override_semantics else is_dangerous_env_key
    )
    if danger_check(key):
        blocked.append(key)
        return
    if "\x00" in value:
        blocked.append(key)
        return
    if len(value) > _MAX_VALUE_CHARS:
        blocked.append(key)
        return
    if is_secret_env_key(key):
        secrets_detected.append(key)
    allowed[key] = value


def sanitize_env(
    env: Mapping[str, str],
    *,
    overrides: Mapping[str, str] | None = None,
) -> EnvSanitizationResult:
    """Filter a baseline environment (plus optional overrides) for spawning.

    Dangerous base keys are dropped; dangerous override keys are dropped;
    NUL-bearing or oversized values are dropped; credential-looking keys
    pass through but are reported in ``secrets_detected``.
    """
    allowed: dict[str, str] = {}
    blocked: list[str] = []
    secrets_detected: list[str] = []

    for key, value in env.items():
        _admit(
            key,
            value,
            override_semantics=False,
            allowed=allowed,
            blocked=blocked,
            secrets_detected=secrets_detected,
        )

    if overrides:
        for key, value in overrides.items():
            _admit(
                key,
                value,
                override_semantics=True,
                allowed=allowed,
                blocked=blocked,
                secrets_detected=secrets_detected,
            )

    return EnvSanitizationResult(
        allowed=allowed,
        blocked=blocked,
        secrets_detected=secrets_detected,
    )

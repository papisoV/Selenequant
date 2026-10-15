"""Startup-time config schema validation for TGWatcher.

Prevents silent failures from misspelled config fields (e.g. `max_tokens_btach`).
Stdlib-only — no pydantic/marshmallow.
"""
from __future__ import annotations

import difflib
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Top-level keys that must be present.
REQUIRED_TOP_LEVEL: tuple[str, ...] = ("storage", "groups", "signal")

# Known top-level keys (for typo detection via close matches).
KNOWN_TOP_LEVEL: tuple[str, ...] = (
    "storage",
    "groups",
    "signal",
    "llm",
    "crawl",
    "proxy",
    "timezone",
    "telegram",
    "catchup",
    "webhook",
)

# Known keys per top-level section (for typo detection).
KNOWN_SECTION_KEYS: dict[str, tuple[str, ...]] = {
    "storage": ("db_path",),
    "groups": ("id", "name", "username", "auto_catchup", "auto_poll",
               "poll_interval_seconds", "auto_listen"),
    "signal": ("enabled", "llm", "dedup", "filter", "batch_size",
               "llm_batch_size", "llm_delay", "factor_version", "keywords",
               "auto_llm", "auto_digest_interval_minutes", "auto_llm_min_signals"),
    "signal_llm": ("provider", "providers", "timeout_connect", "timeout_read",
                   "timeout_write", "timeout_pool", "max_retries",
                   "max_batch_size", "checkpoint_dir", "fallback_order"),
    "crawl": ("interval_minutes", "limit", "max_delay", "min_delay", "mode"),
    "proxy": ("enabled", "host", "port", "protocol"),
    "timezone": ("utc_offset_hours",),
    "telegram": ("api_hash", "api_id", "phone", "session_dir"),
    "catchup": ("enabled", "limit"),
    "webhook": ("enabled", "timeout_seconds", "max_workers", "endpoints"),
}

# Known provider credential keys (for typo detection inside providers.<name>).
KNOWN_PROVIDER_KEYS: tuple[str, ...] = (
    "api_key", "base_url", "model", "temperature", "max_tokens",
    "max_tokens_batch",
)

# Specific known-misspelled field names → canonical suggestion.
KNOWN_TYPOS: dict[str, str] = {
    "max_tokens_btach": "max_tokens_batch",
    "max_tokens_bach": "max_tokens_batch",
    "interval_seconds": "poll_interval_seconds",
    "max_batch": "max_batch_size",
    "batch_size": "llm_batch_size",  # signal.batch_size exists, but at signal.llm level this is the wrong key
    "auto_llm_interval": "auto_digest_interval_minutes",
    "auto_llm_interval_minutes": "auto_digest_interval_minutes",
    "auto_digest_interval": "auto_digest_interval_minutes",
    "auto_llm_min_signal": "auto_llm_min_signals",
}


def _is_dict(v: Any) -> bool:
    return isinstance(v, dict)


def _typo_warnings_for(
    actual_keys: list[str],
    known_keys: tuple[str, ...],
    context: str,
) -> list[str]:
    """Emit warnings for close-match field names (likely typos)."""
    warnings: list[str] = []
    known_lower = {k.lower() for k in known_keys}
    for key in actual_keys:
        # Exact known (case-insensitive) — fine.
        if key.lower() in known_lower:
            continue
        # Specific known typo → direct suggestion.
        if key in KNOWN_TYPOS:
            warnings.append(
                f"{context}: suspicious key '{key}' — did you mean "
                f"'{KNOWN_TYPOS[key]}'?"
            )
            continue
        # Close-match heuristic.
        matches = difflib.get_close_matches(key, known_keys, n=1, cutoff=0.7)
        if matches:
            warnings.append(
                f"{context}: suspicious key '{key}' — did you mean "
                f"'{matches[0]}'?"
            )
    return warnings


def validate_config(config: dict) -> list[str]:
    """Validate a TGWatcher config dict.

    Returns a list of error messages. Empty list = valid.
    Side effect: logs warnings for suspected typos (close-match keys).
    """
    errors: list[str] = []

    if not isinstance(config, dict):
        return ["Config root must be a dict (got "
                f"{type(config).__name__})."]

    # Required top-level keys.
    for key in REQUIRED_TOP_LEVEL:
        if key not in config:
            errors.append(f"Missing required top-level key: '{key}'.")

    # Top-level typo detection.
    for w in _typo_warnings_for(list(config.keys()), KNOWN_TOP_LEVEL, "config root"):
        logger.warning(w)

    # storage.db_path
    storage = config.get("storage")
    if storage is not None:
        if not _is_dict(storage):
            errors.append("'storage' must be a dict.")
        else:
            for w in _typo_warnings_for(list(storage.keys()),
                                        KNOWN_SECTION_KEYS["storage"], "storage"):
                logger.warning(w)
            db_path = storage.get("db_path")
            if not isinstance(db_path, str):
                errors.append("'storage.db_path' must be a string.")

    # groups
    groups = config.get("groups")
    if groups is not None:
        if not isinstance(groups, list):
            errors.append("'groups' must be a list.")
        else:
            for i, g in enumerate(groups):
                if not _is_dict(g):
                    errors.append(f"groups[{i}] must be a dict.")
                    continue
                for w in _typo_warnings_for(
                    list(g.keys()), KNOWN_SECTION_KEYS["groups"],
                    f"groups[{i}]",
                ):
                    logger.warning(w)
                if not isinstance(g.get("id"), int):
                    errors.append(f"groups[{i}].id must be an int.")
                if not isinstance(g.get("name"), str):
                    errors.append(f"groups[{i}].name must be a string.")

    # signal
    signal = config.get("signal")
    signal_enabled: bool | None = None
    if signal is not None:
        if not _is_dict(signal):
            errors.append("'signal' must be a dict.")
        else:
            for w in _typo_warnings_for(
                list(signal.keys()), KNOWN_SECTION_KEYS["signal"], "signal"
            ):
                logger.warning(w)
            se = signal.get("enabled")
            if not isinstance(se, bool):
                errors.append("'signal.enabled' must be a bool.")
            else:
                signal_enabled = se

            # auto_llm — chain LLM+digest after crawl (default: True when signal enabled)
            al = signal.get("auto_llm")
            if al is not None and not isinstance(al, bool):
                errors.append("'signal.auto_llm' must be a bool.")

            # auto_digest_interval_minutes — rate limit for digest generation
            adi = signal.get("auto_digest_interval_minutes")
            if adi is not None:
                if not isinstance(adi, int) or adi < 0:
                    errors.append(
                        "'signal.auto_digest_interval_minutes' must be a "
                        "non-negative int (0 disables auto-digest)."
                    )

            # auto_llm_min_signals — skip LLM batch if pending < this
            ms = signal.get("auto_llm_min_signals")
            if ms is not None:
                if not isinstance(ms, int) or ms < 0:
                    errors.append(
                        "'signal.auto_llm_min_signals' must be a "
                        "non-negative int."
                    )

            # signal.llm (nested) — provider credentials.
            sig_llm = signal.get("llm")
            if sig_llm is not None:
                if not _is_dict(sig_llm):
                    errors.append("'signal.llm' must be a dict.")
                else:
                    for w in _typo_warnings_for(
                        list(sig_llm.keys()),
                        KNOWN_SECTION_KEYS["signal_llm"],
                        "signal.llm",
                    ):
                        logger.warning(w)
                    providers = sig_llm.get("providers")
                    active = sig_llm.get("provider")
                    if active is not None and not isinstance(active, str):
                        errors.append(
                            "'signal.llm.provider' must be a string "
                            "(provider name)."
                        )
                    # max_batch_size — optional positive int for batch checkpoint
                    mbs = sig_llm.get("max_batch_size")
                    if mbs is not None:
                        if not isinstance(mbs, int) or isinstance(mbs, bool):
                            errors.append(
                                "'signal.llm.max_batch_size' must be an int "
                                "(positive, optional)."
                            )
                        elif mbs <= 0:
                            errors.append(
                                "'signal.llm.max_batch_size' must be > 0."
                            )
                    # checkpoint_dir — optional string path for batch checkpoint JSONs
                    cdir = sig_llm.get("checkpoint_dir")
                    if cdir is not None and not isinstance(cdir, str):
                        errors.append(
                            "'signal.llm.checkpoint_dir' must be a string "
                            "(path, optional)."
                        )
                    # fallback_order — optional list of provider name strings.
                    # Each entry must be a key in providers.
                    fbo = sig_llm.get("fallback_order")
                    if fbo is not None:
                        if not isinstance(fbo, list) or not all(
                            isinstance(x, str) for x in fbo
                        ):
                            errors.append(
                                "'signal.llm.fallback_order' must be a list "
                                "of provider name strings."
                            )
                        elif not fbo:
                            errors.append(
                                "'signal.llm.fallback_order' must not be "
                                "empty — omit the key to use only the "
                                "primary provider."
                            )
                        else:
                            # Validate entries against providers dict (when
                            # providers is present and non-empty).
                            if isinstance(providers, dict) and providers:
                                unknown = [
                                    p for p in fbo if p not in providers
                                ]
                                if unknown:
                                    errors.append(
                                        "'signal.llm.fallback_order' "
                                        f"references unknown provider(s): "
                                        f"{unknown}. Add their credentials "
                                        f"under 'providers:' or remove "
                                        f"from fallback_order."
                                    )
                    if providers is not None:
                        if not _is_dict(providers):
                            errors.append(
                                "'signal.llm.providers' must be a dict."
                            )
                        elif len(providers) == 0:
                            if signal_enabled:
                                errors.append(
                                    "'signal.llm.providers' is empty but "
                                    "signal.enabled=true."
                                )
                        else:
                            if active is not None and active not in providers:
                                errors.append(
                                    f"'signal.llm.provider' ('{active}') is "
                                    f"not a key in 'signal.llm.providers' "
                                    f"(keys: {sorted(providers)})."
                                )
                            for pname, pcfg in providers.items():
                                if not _is_dict(pcfg):
                                    errors.append(
                                        f"signal.llm.providers.{pname} must "
                                        f"be a dict."
                                    )
                                    continue
                                for w in _typo_warnings_for(
                                    list(pcfg.keys()),
                                    KNOWN_PROVIDER_KEYS,
                                    f"signal.llm.providers.{pname}",
                                ):
                                    logger.warning(w)
                                if signal_enabled:
                                    for req in ("api_key", "base_url", "model"):
                                        if req not in pcfg:
                                            errors.append(
                                                f"signal.llm.providers."
                                                f"{pname}.{req} is required "
                                                f"(signal.enabled=true)."
                                            )
                                        elif not isinstance(pcfg[req], str):
                                            errors.append(
                                                f"signal.llm.providers."
                                                f"{pname}.{req} must be a "
                                                f"string."
                                            )

    # llm (top-level) — only validated if present (some configs may put it
    # here). In the canonical schema it lives under signal.llm.
    llm = config.get("llm")
    if llm is not None:
        if not _is_dict(llm):
            errors.append("'llm' must be a dict.")
        else:
            logger.warning(
                "Top-level 'llm' key detected — canonical schema nests it "
                "under 'signal.llm'."
            )

    return errors

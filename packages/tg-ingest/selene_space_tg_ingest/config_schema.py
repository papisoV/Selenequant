"""Startup-time config typo detection (port of tgwatcher/config_schema.py).

Advisory-only: flags suspicious field names (close matches + known
misspellings) as warnings, never hard errors.

Port scope (2026-08-10): tgwatcher's hard-error checks are NOT ported —
(1) REQUIRED_TOP_LEVEL missing-key errors, and (2) api_key/base_url/model
"required when signal.enabled" checks would both produce false positives in
tg-ingest, where every field is resolved via defaults or env-var fallback
(SELENE_TG_LLM_API_KEY etc.). Only typo detection survives.

Usage:
    from selene_space_tg_ingest.config_schema import validate_config

    validate_config(raw_yaml_dict)   # logs warnings; returns list[str]
"""
from __future__ import annotations

import difflib
import logging
from typing import Any

logger = logging.getLogger(__name__)

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
)

# Known keys per top-level section. `signal_llm` merges tgwatcher's keys
# with tg-ingest's flat legacy fields (base_url/api_key/model/...) which live
# at signal.llm top level when no providers dict is used.
KNOWN_SECTION_KEYS: dict[str, tuple[str, ...]] = {
    "storage": ("db_path",),
    "groups": ("id", "name", "username", "auto_catchup", "auto_poll",
               "poll_interval_seconds", "auto_listen"),
    "signal": ("enabled", "llm", "dedup", "filter", "batch_size",
               "llm_batch_size", "llm_delay", "factor_version", "keywords",
               "auto_llm", "auto_digest_interval_minutes", "auto_llm_min_signals"),
    "signal_llm": ("provider", "providers", "timeout_connect", "timeout_read",
                   "timeout_write", "timeout_pool", "max_retries",
                   "max_batch_size", "checkpoint_dir", "fallback_order",
                   "base_url", "api_key", "model", "temperature",
                   "max_tokens", "max_tokens_batch"),
    "crawl": ("interval_minutes", "limit", "max_delay", "min_delay", "mode"),
    "proxy": ("enabled", "host", "port", "protocol"),
    "timezone": ("utc_offset_hours",),
    "telegram": ("api_hash", "api_id", "phone", "session_dir"),
    "catchup": ("enabled", "limit"),
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
    """Return advisory typo warnings for a tg-ingest config dict.

    Empty list = no suspicious keys. Side effect: logs each warning.
    Never raises and never rejects the config — typo detection is
    advisory only (tg-ingest resolves defaults + env-var fallbacks).
    """
    warnings: list[str] = []

    if not isinstance(config, dict):
        return warnings

    warnings += _typo_warnings_for(list(config.keys()), KNOWN_TOP_LEVEL, "config root")

    storage = config.get("storage")
    if isinstance(storage, dict):
        warnings += _typo_warnings_for(
            list(storage.keys()), KNOWN_SECTION_KEYS["storage"], "storage")

    groups = config.get("groups")
    if isinstance(groups, list):
        for i, g in enumerate(groups):
            if _is_dict(g):
                warnings += _typo_warnings_for(
                    list(g.keys()), KNOWN_SECTION_KEYS["groups"], f"groups[{i}]")

    signal = config.get("signal")
    if isinstance(signal, dict):
        warnings += _typo_warnings_for(
            list(signal.keys()), KNOWN_SECTION_KEYS["signal"], "signal")
        sig_llm = signal.get("llm")
        if isinstance(sig_llm, dict):
            warnings += _typo_warnings_for(
                list(sig_llm.keys()), KNOWN_SECTION_KEYS["signal_llm"],
                "signal.llm")
            providers = sig_llm.get("providers")
            if isinstance(providers, dict):
                for pname, pcfg in providers.items():
                    if _is_dict(pcfg):
                        warnings += _typo_warnings_for(
                            list(pcfg.keys()), KNOWN_PROVIDER_KEYS,
                            f"signal.llm.providers.{pname}")

    for w in warnings:
        logger.warning(w)
    return warnings

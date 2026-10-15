"""Behavior locks for selene_space_security.env_security key triage
and the sanitize_env pipeline verdicts."""

from selene_space_security.env_security import (
    is_dangerous_env_key,
    is_dangerous_override_key,
    is_secret_env_key,
    sanitize_env,
)

# --- is_dangerous_env_key ---------------------------------------------------

BLOCKED_EXACT = ["NODE_OPTIONS", "PYTHONPATH", "BASH_ENV", "IFS", "SSLKEYLOGFILE"]
BLOCKED_PREFIXED = ["LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "BASH_FUNC_something"]
HARMLESS_KEYS = ["PATH", "HOME", "LANG", "TZ", "TERM"]


def test_exact_blocklist_hits() -> None:
    flagged = [k for k in BLOCKED_EXACT if is_dangerous_env_key(k)]
    assert flagged == BLOCKED_EXACT


def test_injection_prefixes_hit() -> None:
    assert all(is_dangerous_env_key(k) for k in BLOCKED_PREFIXED)


def test_matching_ignores_letter_case() -> None:
    assert is_dangerous_env_key("pythonpath") is True
    assert is_dangerous_env_key("Bash_Env") is True


def test_benign_keys_pass_through() -> None:
    assert [k for k in HARMLESS_KEYS if is_dangerous_env_key(k)] == []


def test_surrounding_whitespace_tolerated() -> None:
    assert is_dangerous_env_key("  PYTHONPATH  ") is True


# --- is_dangerous_override_key ----------------------------------------------


def test_override_list_superset_of_base() -> None:
    assert is_dangerous_override_key("PYTHONPATH") is True


OVERRIDE_BLOCKED = ["HOME", "GIT_SSH_COMMAND", "EDITOR", "VISUAL", "PROMPT_COMMAND"]


def test_override_only_entries_flagged() -> None:
    assert [k for k in OVERRIDE_BLOCKED if not is_dangerous_override_key(k)] == []


def test_override_prefix_rules() -> None:
    assert is_dangerous_override_key("GIT_CONFIG_GLOBAL") is True
    assert is_dangerous_override_key("NPM_CONFIG_REGISTRY") is True


def test_app_specific_key_is_fine() -> None:
    assert is_dangerous_override_key("MY_APP_CONFIG") is False


# --- is_secret_env_key -------------------------------------------------------

NAMED_PROVIDER_SECRETS = [
    "AWS_SECRET_ACCESS_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "BINANCE_API_SECRET",
]
PATTERN_MATCHED_SECRETS = [
    "MY_SERVICE_API_KEY",
    "DB_PASSWORD",
    "AUTH_ACCESS_TOKEN",
    "SIGNING_PRIVATE_KEY",
]


def test_known_provider_secret_names_detected() -> None:
    assert all(is_secret_env_key(k) for k in NAMED_PROVIDER_SECRETS)


def test_generic_secret_shapes_detected() -> None:
    assert all(is_secret_env_key(k) for k in PATTERN_MATCHED_SECRETS)


def test_infra_keys_are_not_secrets() -> None:
    for plain in ("PATH", "HOME", "PYTHONPATH"):
        assert is_secret_env_key(plain) is False


# --- sanitize_env ------------------------------------------------------------


class TestSanitizeEnvVerdicts:
    def test_dangerous_entry_diverted_to_blocked(self) -> None:
        outcome = sanitize_env({"PATH": "/usr/bin", "LD_PRELOAD": "/evil.so", "HOME": "/home/user"})
        assert outcome.blocked == ["LD_PRELOAD"]
        assert {"PATH", "HOME"} <= set(outcome.allowed)

    def test_blocked_override_kept_out_of_result(self) -> None:
        outcome = sanitize_env({"PATH": "/usr/bin"}, overrides={"HOME": "/tmp/evil", "MY_VAR": "ok"})
        assert "HOME" in outcome.blocked
        assert outcome.allowed["MY_VAR"] == "ok"

    def test_secret_flagged_but_still_forwarded(self) -> None:
        # Secrets pass through (needed for operation) but are flagged.
        outcome = sanitize_env({"OPENAI_API_KEY": "sk-test123", "PATH": "/usr/bin"})
        assert outcome.secrets_detected == ["OPENAI_API_KEY"]
        assert "OPENAI_API_KEY" in outcome.allowed

    def test_embedded_null_byte_blocks_the_variable(self) -> None:
        outcome = sanitize_env({"SAFE_VAR": "normal", "BAD_VAR": "evil\x00payload"})
        assert outcome.blocked == ["BAD_VAR"]
        assert outcome.allowed["SAFE_VAR"] == "normal"

    def test_value_over_size_limit_blocks_the_variable(self) -> None:
        outcome = sanitize_env({"BIG_VAR": "x" * 40_000})
        assert "BIG_VAR" in outcome.blocked

    def test_no_input_yields_empty_verdict(self) -> None:
        outcome = sanitize_env({})
        assert outcome.allowed == {}
        assert outcome.blocked == []

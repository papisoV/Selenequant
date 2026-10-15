"""Behavior locks for selene_space_security.redaction: token masking,
provider-prefix scrubbing, structured patterns, PEM/JWT, and chunking."""

from selene_space_security.redaction import (
    CHUNK_THRESHOLD,
    mask_token,
    redact_text,
)

# --- mask_token ---------------------------------------------------------------


def test_tiny_token_fully_masked() -> None:
    assert mask_token("abc123") == "***"


def test_threshold_length_uses_head_tail_form() -> None:
    eighteen = "a" * 18
    assert mask_token(eighteen) == "aaaaaa...aaaa"


def test_long_token_keeps_head_and_tail_only() -> None:
    masked = mask_token("sk-abc123def456ghi789jkl012")
    assert masked.startswith("sk-abc")
    assert masked.endswith("l012")
    assert "..." in masked


# --- provider prefixes ---------------------------------------------------------


def _scrubbed(token: str, wrapper: str) -> str:
    return redact_text(wrapper.format(token=token))


def test_openai_key_scrubbed_but_prefix_visible() -> None:
    leaked = "sk-abc123xyz456qrs789tuvw"
    out = _scrubbed(leaked, "My key is {token}")
    assert leaked not in out
    assert "sk-abc" in out  # first 6 chars preserved


def test_github_classic_pat_scrubbed() -> None:
    tok = "ghp_" + "A" * 30
    assert tok not in _scrubbed(tok, "token={token}")


def test_github_fine_grained_pat_scrubbed() -> None:
    tok = "github_pat_" + "B" * 30
    assert tok not in _scrubbed(tok, "Using {token} for auth")


def test_slack_bot_token_scrubbed() -> None:
    tok = "xoxb-" + "1234567890-" * 3
    assert tok not in _scrubbed(tok, "SLACK_TOKEN={token}")


def test_groq_key_scrubbed() -> None:
    tok = "gsk_" + "X" * 30
    assert tok not in _scrubbed(tok, "key: {token}")


def test_google_key_scrubbed() -> None:
    tok = "AIza" + "Y" * 30
    assert tok not in _scrubbed(tok, "GOOGLE_API_KEY={token}")


def test_perplexity_key_scrubbed() -> None:
    tok = "pplx-" + "Z" * 20
    assert tok not in _scrubbed(tok, "api_key={token}")


def test_npm_registry_auth_token_scrubbed() -> None:
    tok = "npm_" + "N" * 20
    assert tok not in _scrubbed(tok, "//registry/:_authToken={token}")


def test_aws_access_key_id_scrubbed() -> None:
    tok = "AKIA" + "A" * 16
    assert tok not in _scrubbed(tok, "AWS_ACCESS_KEY_ID={tok}".replace("{tok}", "{token}"))


# --- structured patterns ---------------------------------------------------------


def test_env_style_assignment_redacted() -> None:
    secret = "sk-reallyreallylongsecretkey1234"
    out = redact_text(f"OPENAI_API_KEY={secret}")
    assert secret not in out
    assert "OPENAI_API_KEY=" in out


def test_json_field_value_redacted() -> None:
    secret = "my-super-secret-api-key-value"
    assert secret not in redact_text('{"apiKey": "%s"}' % secret)


def test_cli_flag_value_redacted() -> None:
    secret = "sk-verylongsecretkeythatneedsredaction"
    assert secret not in redact_text(f"--api-key {secret}")


def test_bearer_jwt_in_header_redacted() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.payload.signature"
    assert jwt not in redact_text(f"Authorization: Bearer {jwt}")


# --- PEM blocks -------------------------------------------------------------------


def test_pem_body_replaced_marker_lines_kept() -> None:
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA0Z3VS5JJcds3xfn/ygWep4PAtGoSo0gI\n"
        "dummydata1234567890abcdefghijklmnopqrstuvwxyz\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out = redact_text(pem)
    assert "-----BEGIN RSA PRIVATE KEY-----" in out
    assert "-----END RSA PRIVATE KEY-----" in out
    assert "MIIEowIBAAKCAQEA0Z3VS5JJcds3xfn" not in out
    assert "...redacted..." in out


# --- JWT ---------------------------------------------------------------------------


def test_standalone_jwt_removed_from_text() -> None:
    jwt = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature_part_here"
    assert jwt not in redact_text(f"token: {jwt}")


# --- edge cases ----------------------------------------------------------------------


def test_empty_input_is_noop() -> None:
    assert redact_text("") == ""


def test_clean_log_line_passes_through_unmodified() -> None:
    line = "This is a normal log message with no secrets"
    assert redact_text(line) == line


def test_ordinary_filesystem_path_not_touched() -> None:
    line = "Loading file from /usr/local/share/data"
    assert redact_text(line) == line


# --- chunked processing ----------------------------------------------------------------


def test_secret_hidden_inside_oversized_document() -> None:
    # Text larger than CHUNK_THRESHOLD forces the chunked scan path.
    filler = "x" * (CHUNK_THRESHOLD + 1000)
    secret = "sk-" + "a" * 40
    out = redact_text(filler + f" key={secret} " + filler)
    assert secret not in out


def test_short_document_with_key_still_scrubbed() -> None:
    assert "sk-abcdef123456789012345678" not in redact_text("short text with sk-abcdef123456789012345678")

"""Credential redaction for free-form text (logs, prompts, exports).

Two public entry points:

- :func:`mask_token` — display form of a single token (head + ellipsis + tail).
- :func:`redact_text` — sweep a blob of text and replace anything that
  looks like a credential with a masked placeholder, while preserving the
  surrounding context.

Detection combines three complementary nets:

1. provider prefixes — tokens minted by known vendors (OpenAI, GitHub,
   Slack, AWS, ...) are recognized by shape and redacted wherever found;
2. structured assignments — ``KEY=value``, JSON fields, CLI flags, and
   Authorization headers carry secrets under names that advertise them;
3. envelope formats — PEM key blocks and JWTs have rigid structure and
   are redacted as whole units.

Very long inputs switch to a chunked scan so detection cost stays linear
and bounded regardless of input size; chunk overlap guarantees a secret
straddling a boundary is still caught.
"""

from __future__ import annotations

import re

__all__ = ["CHUNK_THRESHOLD", "mask_token", "redact_text"]

# Inputs longer than this are scanned in overlapping chunks. The value is
# an internal tuning knob; only the name and the chunked-mode semantics
# are part of the module's contract.
CHUNK_THRESHOLD = 50_000

_CHUNK_OVERLAP = 256

_MASK_SHORT = "***"

# --- single-token masking --------------------------------------------------

_MASK_MIN_LEN = 18


def mask_token(token: str) -> str:
    """Display form of one token: short ones collapse to ``***``; long ones
    keep their first 6 and last 4 characters separated by ``...``."""
    if len(token) < _MASK_MIN_LEN:
        return _MASK_SHORT
    return f"{token[:6]}...{token[-4:]}"


# --- provider-shaped tokens -------------------------------------------------
#
# Each entry: (compiled pattern, replacement builder). Patterns match the
# full token; replacements preserve a recognizable head via mask_token.


def _masked(match: re.Match[str]) -> str:
    return mask_token(match.group(0))


_PROVIDER_PATTERNS: list[tuple[re.Pattern[str], object]] = [
    # OpenAI-style sk- keys. Body floor of 6 keeps masked output ("sk-abc...")
    # inert so redaction stays idempotent; real keys are far longer.
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"), _masked),
    # GitHub classic PAT: ghp_ + long alphanumeric body.
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), _masked),
    # GitHub fine-grained PAT.
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_\-]{20,}"), _masked),
    # Slack bot/user/app tokens.
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), _masked),
    # Groq keys. Body floor of 6 keeps masked output inert (idempotency).
    (re.compile(r"\bgsk_[A-Za-z0-9_\-]{6,}"), _masked),
    # Google API keys.
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), _masked),
    # Perplexity keys. Body floor of 6 keeps masked output inert (idempotency).
    (re.compile(r"\bpplx-[A-Za-z0-9_\-]{6,}"), _masked),
    # npm automation tokens. Body floor of 6 keeps masked output inert.
    (re.compile(r"\bnpm_[A-Za-z0-9_\-]{6,}"), _masked),
    # AWS access key ids: AKIA + 16 uppercase-alphanumeric.
    (re.compile(r"\b(A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b"), _masked),
]

# --- structured secret carriers ---------------------------------------------

_ASSIGNMENT_NAME = r"[A-Za-z_][A-Za-z0-9_]*"

_STRUCTURED_PATTERNS: list[tuple[re.Pattern[str], object]] = [
    # ENV-style assignment whose value looks like a credential:
    # long unquoted token on the right side of KEY=.
    (
        re.compile(
            rf"(?i)\b({ _ASSIGNMENT_NAME }(?:API_KEY|APIKEY|SECRET|TOKEN|PASSWORD)"
            rf"|API_KEY|SECRET_KEY|ACCESS_TOKEN|AUTH_TOKEN)"
            rf"(?:\"|')?\s*[:=]\s*[\"']?([^\s\"'{{}}]+)[\"']?"
        ),
        lambda m: f"{m.group(1)}={mask_token(m.group(2))}",
    ),
    # JSON-ish fields named like credentials.
    (
        re.compile(
            r'(?i)("(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|password)'
            r'"\s*:\s*")([^"]+)(")'
        ),
        lambda m: f"{m.group(1)}{mask_token(m.group(2))}{m.group(3)}",
    ),
    # CLI flags: --api-key VALUE / --token=VALUE / -p VALUE.
    (
        re.compile(
            r"(?i)(--(?:api[_-]?key|token|secret|password)(?:\s+|=))"
            r"(?:\"|')?([^\s\"']+)"
        ),
        lambda m: f"{m.group(1)}{mask_token(m.group(2))}",
    ),
    # Bearer credentials in headers.
    (
        re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s,\"']+)", re.IGNORECASE),
        lambda m: f"{m.group(1)}{mask_token(m.group(2))}",
    ),
]

# --- envelope formats --------------------------------------------------------

_PEM_PATTERN = re.compile(
    r"(-----BEGIN [A-Z0-9 ]+PRIVATE KEY-----\n)"  # opening banner
    r"(?:(?!-----END).*\n)+?"  # body lines
    r"(-----END [A-Z0-9 ]+PRIVATE KEY-----)?",
)

_PEM_REPLACEMENT_BODY = "...redacted..."

_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")


def _redact_pem(text: str) -> str:
    """Collapse PEM private-key bodies to a placeholder, keep banners."""

    def _swap(match: re.Match[str]) -> str:
        header = match.group(1)
        footer = match.group(2) or "-----END PRIVATE KEY-----"
        return f"{header}{_PEM_REPLACEMENT_BODY}\n{footer}"

    return _PEM_PATTERN.sub(_swap, text)


def _apply_patterns(text: str, patterns) -> str:
    for compiled, replacer in patterns:
        text = compiled.sub(replacer, text)
    return text


def _scan_chunk(chunk: str) -> str:
    """Full pattern battery over one chunk of text."""
    chunk = _redact_pem(chunk)
    chunk = _JWT_PATTERN.sub(lambda m: mask_token(m.group(0)), chunk)
    chunk = _apply_patterns(chunk, _STRUCTURED_PATTERNS)
    chunk = _apply_patterns(chunk, _PROVIDER_PATTERNS)
    return chunk


def redact_text(text: str) -> str:
    """Remove credential material from free-form text, preserving context.

    Idempotent by construction: every replacement output is itself inert
    (masked forms contain no full token), so a second pass is a no-op.
    """
    if not text:
        return text

    if len(text) <= CHUNK_THRESHOLD:
        return _scan_chunk(text)

    # Chunked mode: fixed-size windows with generous overlap so tokens that
    # straddle a cut are seen whole by at least one pass.
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_THRESHOLD, len(text))
        pieces.append(_scan_chunk(text[start:end]))
        if end == len(text):
            break
        start = end - _CHUNK_OVERLAP

    result = pieces[0]
    for piece in pieces[1:]:
        # Re-stitch across the overlap: find the longest suffix of `result`
        # that is a prefix of `piece` within the overlap window.
        window = min(len(result), len(piece), _CHUNK_OVERLAP * 2)
        stitch = _longest_overlap(result[-window:], piece[:window])
        if stitch > 0:
            result += piece[stitch:]
        else:
            result += piece
    return result


def _longest_overlap(tail: str, head: str) -> int:
    """Length of the longest string that is both a suffix of ``tail`` and a
    prefix of ``head`` (used to re-stitch overlapping chunks)."""
    best = 0
    upper = min(len(tail), len(head))
    for size in range(upper, 0, -1):
        if tail.endswith(head[:size]):
            best = size
            break
    return best

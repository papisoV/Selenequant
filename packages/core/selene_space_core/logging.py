"""Structured logging with secret redaction and request-id correlation."""

from __future__ import annotations

import contextvars
import logging
import re
import sys

import structlog

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "selene_request_id", default=None
)

_MASK = "***"


def _keep_prefix(value: str, keep: int) -> str:
    return value[:keep] + _MASK if len(value) > keep else _MASK


def _redact_value(text: str) -> str:
    """Mask API keys, bearer tokens, JWTs, password= and query-param secrets in *text*."""
    result = text

    # Bearer tokens (before generic key patterns so the scheme stays visible)
    def _bearer(match: re.Match) -> str:
        token = match.group(2)
        return f"{match.group(1)}{_keep_prefix(token, 5)}"

    result = re.sub(r"(Bearer\s+)([A-Za-z0-9\-_.]{8,})", _bearer, result)

    # OpenAI-style keys
    def _openai(match: re.Match) -> str:
        return _keep_prefix(match.group(0), 6)

    result = re.sub(r"\bsk-[A-Za-z0-9\-_]{8,}", _openai, result)

    # Standalone JWTs (three dot-separated base64url segments)
    def _jwt(match: re.Match) -> str:
        return _keep_prefix(match.group(0), 8)

    result = re.sub(
        r"\beyJ[A-Za-z0-9_=-]{4,}\.[A-Za-z0-9_=-]+\.[A-Za-z0-9_=-]+", _jwt, result
    )

    # password=<secret>
    def _password(match: re.Match) -> str:
        return f"{match.group(1)}{_keep_prefix(match.group(2), 3)}"

    result = re.sub(r"(?i)(password[=:]\s*)(\S{4,})", _password, result)

    # Query-param / assignment secrets (api_key=, registrationkey=, token=, secret=,
    # access_key=). A vendor that authenticates via query param leaves the key in
    # the URL; httpx/requests quote the full URL into error messages, so an
    # unhandled provider error would otherwise log the key verbatim. Covers both
    # `key=value` (URL/query) and `"key": "value"` / `key: value` (JSON/dict) forms.
    result = re.sub(
        r"(?i)((?:api[_-]?key|apikey|registration[_-]?key|access[_-]?key|secret|token)\b"
        r"[\"']?\s*[:=]\s*[\"']?)([A-Za-z0-9\-_.]{4,})",
        lambda m: f"{m.group(1)}{_keep_prefix(m.group(2), 3)}",
        result,
    )

    return result


class RedactingFilter(logging.Filter):
    """A ``logging.Filter`` that scrubs secrets from a record before emission.

    The structlog pipeline only covers logs that pass through structlog. The app
    configures stdlib ``logging.basicConfig`` + ``RotatingFileHandler``
    (``services/local-api/app/main.py``), so vendor errors logged via
    ``logging.getLogger(...)`` bypassed the structlog redactor entirely. Attach
    this filter to a handler (or the root logger) to put the redactor on that
    path. Both ``record.msg`` and ``record.args`` are scrubbed, since the common
    ``logger.error("... %s", exc)`` form hides the secret in the args tuple.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_value(str(record.msg))
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(self._scrub_arg(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: self._scrub_arg(v) for k, v in record.args.items()}
        return True

    @staticmethod
    def _scrub_arg(value: object) -> object:
        """Scrub one log argument.

        An exception passed as an arg (the common
        ``logger.error("... %s", exc)`` form) is NOT a str, so a str-only
        scrub leaves it untouched — and ``str(exc)`` happens at emit time,
        after this filter. Coerce non-str, non-numeric values to a scrubbed
        string so the secret in the exception message cannot survive.
        """
        if isinstance(value, str):
            return _redact_value(value)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return _redact_value(str(value))


class RedactingFormatter(logging.Formatter):
    """A ``logging.Formatter`` that scrubs secrets from the rendered output.

    ``RedactingFilter`` scrubs ``msg``/``args``, but ``logger.error(...,
    exc_info=True)`` renders the traceback *at format time* — after the filter —
    so an exception carrying a URL-with-key leaks through the traceback text.
    326+ ``exc_info=True`` call sites exist repo-wide, so this closes that path.
    Scrubbing the final formatted string catches the traceback and anything else
    a formatter adds.
    """

    def format(self, record: logging.LogRecord) -> str:
        return _redact_value(super().format(record))


def bind_request_id(request_id: str | None) -> None:
    """Bind a request id to the current context for log correlation."""
    _request_id_var.set(request_id)


def get_request_id() -> str | None:
    """Return the request id bound to the current context (None if unset)."""
    return _request_id_var.get()


def _inject_request_id(_, __, event_dict):
    rid = get_request_id()
    if rid is not None:
        event_dict["request_id"] = rid
    return event_dict


def _redact_event(_, __, event_dict):
    if "event" in event_dict:
        event_dict["event"] = _redact_value(str(event_dict["event"]))
    return event_dict


_configured = False


def setup_logging(log_level: str = "INFO", json_format: bool = True) -> None:
    """Configure structlog + stdlib logging. Safe to call repeatedly."""
    global _configured

    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=level, force=True
    )

    renderer = (
        structlog.processors.JSONRenderer()
        if json_format
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_event,
            _inject_request_id,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    _configured = True

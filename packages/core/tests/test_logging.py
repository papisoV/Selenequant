"""Logging locks for selene_space_core.logging — secret redaction patterns,
request-id context var, and logger setup in both formats."""

from selene_space_core.logging import (
    _redact_value,
    bind_request_id,
    get_request_id,
    setup_logging,
)


class TestSecretRedaction:
    def test_openai_style_key_gets_masked(self):
        scrubbed = _redact_value("key is sk-abcdef12345678")
        assert "sk-abc***" in scrubbed

    def test_bearer_token_keeps_short_prefix(self):
        scrubbed = _redact_value("Authorization: Bearer eyJabc123456789xyzabc")
        checks = (
            "***" in scrubbed,
            "Bearer eyJab" in scrubbed,
        )  # prefix kept, rest masked
        assert all(checks)

    def test_standalone_jwt_gets_masked(self):
        # Standalone JWT (not preceded by token=)
        scrubbed = _redact_value(
            "jwt: eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
        )
        assert "eyJhbGci***" in scrubbed

    def test_benign_text_passes_through_verbatim(self):
        plain = "normal log message about BTC/USDT"
        assert _redact_value(plain) == plain

    def test_password_assignment_gets_masked(self):
        scrubbed = _redact_value("password=MySecret123456")
        assert "MyS***" in scrubbed


class TestQueryParamSecretRedaction:
    """A vendor that authenticates via query param puts the key in the URL, and
    httpx quotes the full URL into its error message — so an unhandled provider
    error logs the key. The redactor must catch the query-param form too."""

    def test_api_key_query_param_gets_masked(self):
        url = (
            "Client error '400 Bad Request' for url "
            "'https://api.stlouisfed.org/fred/series/observations?"
            "series_id=GDP&api_key=SECRETVALUE123'"
        )
        scrubbed = _redact_value(url)
        assert "SECRETVALUE123" not in scrubbed
        assert "api_key=" in scrubbed and "***" in scrubbed
        # the non-secret parts survive so the error stays diagnosable
        assert "series_id=GDP" in scrubbed

    def test_registrationkey_query_param_gets_masked(self):
        # BLS uses `registrationkey` as its query-param name.
        scrubbed = _redact_value("https://api.bls.gov/x?registrationkey=abc123def456")
        assert "abc123def456" not in scrubbed

    def test_token_and_secret_query_params_get_masked(self):
        assert "deadbeef99" not in _redact_value("/x?token=deadbeef99")
        assert "hunter2hunter2" not in _redact_value("/x?secret=hunter2hunter2")

    def test_json_style_key_assignment_gets_masked(self):
        # "api_key": "..." and api_key=... both appear in real logs
        assert "abcdef123456" not in _redact_value('"api_key": "abcdef123456"')
        assert "abcdef123456" not in _redact_value("api_key='abcdef123456'")

    def test_short_benign_key_word_not_over_masked(self):
        # a bare harmless word "key" without a value should pass through
        assert _redact_value("press any key to continue") == "press any key to continue"


class TestStdlibRedactionFilter:
    """The app configures logging via stdlib `logging.basicConfig` +
    RotatingFileHandler (services/local-api/app/main.py), which does NOT run the
    structlog redaction processor. A stdlib logging.Filter is what puts the
    redactor on the real path."""

    def test_filter_scrubs_record_message(self):
        import logging as _logging

        from selene_space_core.logging import RedactingFilter

        rec = _logging.LogRecord(
            name="t",
            level=_logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="failed: https://x/y?api_key=SEKRET123",
            args=(),
            exc_info=None,
        )
        RedactingFilter().filter(rec)
        assert "SEKRET123" not in rec.getMessage()

    def test_filter_scrubs_args(self):
        import logging as _logging

        from selene_space_core.logging import RedactingFilter

        rec = _logging.LogRecord(
            name="t",
            level=_logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="FRED fetch %s failed: %s",
            args=("GDP", "url?api_key=SEKRET123"),
            exc_info=None,
        )
        RedactingFilter().filter(rec)
        assert "SEKRET123" not in rec.getMessage()

    def test_filter_scrubs_exception_arg(self):
        """The real call form is `logger.error("... %s", exc)` — the secret sits in
        the exception's message and `str(exc)` runs at emit time, after the
        filter. A str-only scrub misses it, so this pins the fix."""
        import logging as _logging

        from selene_space_core.logging import RedactingFilter

        exc = RuntimeError("Client error '400' for url 'https://x/y?api_key=SEKRET123'")
        rec = _logging.LogRecord(
            name="t",
            level=_logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="FRED fetch %s failed: %s",
            args=("GDP", exc),
            exc_info=None,
        )
        RedactingFilter().filter(rec)
        assert "SEKRET123" not in rec.getMessage()

    def test_filter_leaves_benign_record_untouched(self):
        import logging as _logging

        from selene_space_core.logging import RedactingFilter

        rec = _logging.LogRecord(
            name="t",
            level=_logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="loaded %d bars for %s",
            args=(120, "BTC/USDT"),
            exc_info=None,
        )
        RedactingFilter().filter(rec)
        assert rec.getMessage() == "loaded 120 bars for BTC/USDT"

    def test_formatter_scrubs_traceback_text(self):
        """`logger.error(..., exc_info=True)` renders the traceback at format
        time, bypassing the record.msg/args scrub. The formatter must scrub the
        final string too or the exception's message (with a URL key) leaks."""
        import io
        import logging as _logging

        from selene_space_core.logging import RedactingFormatter

        logger = _logging.getLogger("slop.test.tb")
        logger.setLevel(_logging.ERROR)
        logger.propagate = False
        buf = io.StringIO()
        handler = _logging.StreamHandler(buf)
        handler.setFormatter(RedactingFormatter("%(message)s"))
        logger.handlers = [handler]
        try:
            raise RuntimeError("url '?api_key=SEKRET123'")
        except RuntimeError:
            logger.error("fetch failed", exc_info=True)
        assert "SEKRET123" not in buf.getvalue()

    def test_child_logger_records_are_scrubbed(self):
        """A record from a child logger propagates to the ROOT'S handlers, not
        through the root logger's own filters — so the filter must sit on the
        handler, or `logging.getLogger("app.services.foo")` leaks to console."""
        import io
        import logging as _logging

        from selene_space_core.logging import RedactingFilter, RedactingFormatter

        root = _logging.getLogger()
        saved = root.handlers[:]
        try:
            root.handlers = []
            buf = io.StringIO()
            handler = _logging.StreamHandler(buf)
            handler.setFormatter(RedactingFormatter("%(message)s"))
            handler.addFilter(RedactingFilter())
            root.addHandler(handler)
            child = _logging.getLogger("app.services.probe")
            child.warning("fetch failed: %s", RuntimeError("url '?api_key=SEKRET123'"))
            assert "SEKRET123" not in buf.getvalue()
        finally:
            root.handlers = saved


class TestRequestIdContext:
    def test_bound_id_is_readable(self):
        bind_request_id("rq-42")
        assert get_request_id() == "rq-42"

    def test_cleared_context_reads_none(self):
        from selene_space_core.logging import _request_id_var

        token = _request_id_var.set(None)
        assert get_request_id() is None
        _request_id_var.reset(token)


class TestLoggerSetup:
    def test_json_mode_configures_cleanly(self):
        setup_logging(log_level="DEBUG", json_format=True)

    def test_console_mode_configures_cleanly(self):
        setup_logging(log_level="INFO", json_format=False)

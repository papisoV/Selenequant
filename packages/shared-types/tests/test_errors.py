"""Behavior locks for selene_space_types.errors — error hierarchy,
HTTP status mapping table, to_dict payload shape, and typed subclasses."""

import pytest
from selene_space_types.errors import (
    ERROR_CODE_HTTP_STATUS,
    ErrorCode,
    ExchangeError,
    InternalError,
    NotFoundError,
    SeleneSpaceError,
    RateLimitedError,
    RiskDeniedError,
    ValidationError,
)


class TestHttpMappingTable:
    def test_table_covers_every_code(self):
        """Every ErrorCode must have an HTTP status code mapping."""
        for code in ErrorCode:
            assert code in ERROR_CODE_HTTP_STATUS, f"{code} missing HTTP mapping"

    def test_expected_status_per_code(self):
        expected = {
            ErrorCode.VALIDATION_ERROR: 400,
            ErrorCode.NOT_FOUND: 404,
            ErrorCode.RISK_DENIED: 403,
            ErrorCode.RATE_LIMITED: 429,
            ErrorCode.EXCHANGE_ERROR: 502,
            ErrorCode.INTERNAL_ERROR: 500,
        }
        for code, status in expected.items():
            assert ERROR_CODE_HTTP_STATUS[code] == status, f"{code} -> {status}"


class TestBaseErrorContract:
    def test_minimal_construction_defaults(self):
        err = SeleneSpaceError(ErrorCode.INTERNAL_ERROR, "Something broke")
        trio = (err.code, err.message, err.http_status)
        assert trio == (ErrorCode.INTERNAL_ERROR, "Something broke", 500)
        assert err.details is None

    def test_details_attach_and_map_to_400(self):
        err = SeleneSpaceError(
            ErrorCode.VALIDATION_ERROR,
            "Bad input",
            details={"field": "symbol"},
        )
        assert err.details == {"field": "symbol"}
        assert err.http_status == 400

    def test_to_dict_includes_details_when_present(self):
        err = SeleneSpaceError(ErrorCode.NOT_FOUND, "Not found", details={"id": "abc"})
        payload = err.to_dict()
        triple = (payload["code"], payload["message"], payload["details"])
        assert triple == ("NOT_FOUND", "Not found", {"id": "abc"})

    def test_to_dict_omits_details_key_without_them(self):
        payload = SeleneSpaceError(ErrorCode.INTERNAL_ERROR, "Oops").to_dict()
        assert "details" not in payload

    def test_base_error_participates_in_exception_hierarchy(self):
        assert issubclass(SeleneSpaceError, Exception)


SUBCLASS_MATRIX = [
    (ValidationError, "Invalid symbol", {"field": "symbol"}, ErrorCode.VALIDATION_ERROR, 400),
    (NotFoundError, "Strategy not found", None, ErrorCode.NOT_FOUND, 404),
    (ExchangeError, "OKX gateway timeout", None, ErrorCode.EXCHANGE_ERROR, 502),
    (RiskDeniedError, "Position too large", None, ErrorCode.RISK_DENIED, 403),
    (RateLimitedError, "Too many requests", None, ErrorCode.RATE_LIMITED, 429),
    (InternalError, "Unexpected", None, ErrorCode.INTERNAL_ERROR, 500),
]


class TestTypedSubclasses:
    @pytest.mark.parametrize("cls,msg,details,code,status", SUBCLASS_MATRIX)
    def test_subclass_binds_correct_code_and_status(self, cls, msg, details, code, status):
        if details is None:
            err = cls(msg)
        else:
            err = cls(msg, details)
        pair = (err.code, err.http_status)
        assert pair == (code, status)

    def test_validation_error_still_is_a_selene_error(self):
        assert issubclass(ValidationError, SeleneSpaceError)

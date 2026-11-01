"""Wire-format locks for selene_space_types.common — envelope models:
pagination defaults, error info optionality, response meta, APIResponse shape."""

from selene_space_types.common import APIResponse, ErrorInfo, Pagination, ResponseMeta


def _recycle(model_cls, instance):
    return model_cls.model_validate_json(instance.model_dump_json())


class TestPaginationEnvelope:
    def test_constructor_defaults(self):
        page = Pagination()
        triple = (page.offset, page.limit, page.total)
        assert triple == (0, 50, 0)

    def test_survives_json_cycle(self):
        page = Pagination(offset=25, limit=10, total=240)
        assert _recycle(Pagination, page) == page

    def test_builds_from_plain_dict(self):
        page = Pagination.model_validate({"offset": 8, "limit": 30, "total": 90})
        assert page.offset == 8


class TestErrorInfoEnvelope:
    def test_survives_json_cycle_with_details(self):
        info = ErrorInfo(
            code="VALIDATION_ERROR",
            message="Invalid symbol",
            details={"field": "symbol"},
        )
        assert _recycle(ErrorInfo, info) == info

    def test_details_field_is_optional(self):
        bare = ErrorInfo(code="NOT_FOUND", message="Not found")
        assert bare.details is None


class TestResponseMetaEnvelope:
    def test_survives_json_cycle_with_pagination(self):
        meta = ResponseMeta(request_id="rq-77", pagination=Pagination(total=10))
        assert _recycle(ResponseMeta, meta) == meta

    def test_every_field_defaults_to_none(self):
        meta = ResponseMeta()
        assert meta.request_id is None and meta.pagination is None


class TestAPIResponseEnvelope:
    def test_success_envelope_carries_payload(self):
        ok = APIResponse[dict](
            data={"price": 3122.5},
            meta=ResponseMeta(request_id="rq-77"),
            error=None,
        )
        rebuilt = APIResponse[dict].model_validate_json(ok.model_dump_json())
        assert rebuilt.data == {"price": 3122.5}
        assert rebuilt.error is None

    def test_error_envelope_carries_error_info(self):
        bad = APIResponse[dict](
            data=None,
            meta=ResponseMeta(request_id="rq-99"),
            error=ErrorInfo(code="INTERNAL", message="Something went wrong"),
        )
        rebuilt = APIResponse[dict].model_validate_json(bad.model_dump_json())
        assert rebuilt.data is None
        assert rebuilt.error is not None
        assert rebuilt.error.code == "INTERNAL"

    def test_envelope_has_exactly_three_fields(self):
        """APIResponse must have data/meta/error fields per S1-A01 spec."""
        assert set(APIResponse.model_fields.keys()) == {"data", "meta", "error"}

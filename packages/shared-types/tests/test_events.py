"""Wire-format locks for selene_space_types.events — DiagnosticEvent and
HookEvent serialization, spec-required header fields, and level enum."""

from selene_space_types.events import DiagnosticEvent, DiagnosticLevel, HookEvent


def _recycle(model_cls, instance):
    return model_cls.model_validate_json(instance.model_dump_json())


def _diag(**overrides) -> DiagnosticEvent:
    base = dict(
        sequence_id=5,
        timestamp=1720003333444,
        category="market",
        level=DiagnosticLevel.INFO,
        message="WS stream attached",
        data={"exchange": "okx"},
    )
    return DiagnosticEvent(**{**base, **overrides})


def _hook(**overrides) -> HookEvent:
    base = dict(
        sequence_id=9,
        timestamp=1720003333555,
        hook_name="on_order_placed",
        payload={"order_id": "ord-X9", "symbol": "BTC/USDT"},
    )
    return HookEvent(**{**base, **overrides})


class TestDiagnosticEventWire:
    def test_survives_json_cycle_with_data(self):
        event = _diag()
        assert _recycle(DiagnosticEvent, event) == event

    def test_header_fields_required_by_spec(self):
        """Spec: events must have sequence_id + timestamp."""
        assert {"sequence_id", "timestamp"} <= set(DiagnosticEvent.model_fields.keys())

    def test_data_payload_is_optional(self):
        quiet = _diag(
            sequence_id=0,
            category="system",
            level=DiagnosticLevel.DEBUG,
            message="boot",
            data=None,
        )
        assert quiet.data is None

    def test_diagnostic_level_covers_full_ladder(self):
        ladder = {"debug", "info", "warning", "error", "critical"}
        assert {lvl.value for lvl in DiagnosticLevel} == ladder


class TestHookEventWire:
    def test_survives_json_cycle_with_payload(self):
        event = _hook()
        assert _recycle(HookEvent, event) == event

    def test_header_fields_required_by_spec(self):
        """Spec: events must have sequence_id + timestamp."""
        assert {"sequence_id", "timestamp"} <= set(HookEvent.model_fields.keys())

    def test_payload_defaults_to_empty_dict(self):
        fresh = HookEvent(sequence_id=0, timestamp=1720003333666, hook_name="on_startup")
        assert fresh.payload == {}

"""Smart-model routing: keyword → slot resolution.

Regression lock for the bug where the resolved override was computed and
then silently dropped downstream (``runtime._llm._config`` never existed).
These tests pin the *input* half of the chain: which slot a message maps
to, and the two gates (``smart_mode`` off / no ``smart_models`` dict).
"""

from __future__ import annotations

from typing import Any

from app.api.v1.agent import _resolve_model_override

_SMART = {
    "strategy": "strat-model",
    "analysis": "analysis-model",
    "quick": "quick-model",
}


class _StubSettings:
    """Minimal stand-in for SettingsService — only _load_non_sensitive is used."""

    def __init__(self, llm_section: dict[str, Any]) -> None:
        self._llm = llm_section
        self.calls: list[str | None] = []

    def _load_non_sensitive(self, *, user_id: str | None = None) -> dict[str, Any]:
        self.calls.append(user_id)
        return {"llm": self._llm}


def _svc(*, enabled: bool = True, models: dict[str, str] | None = _SMART) -> _StubSettings:
    section: dict[str, Any] = {"smart_mode": enabled}
    if models is not None:
        section["smart_models"] = models
    return _StubSettings(section)


def test_strategy_keywords_route_to_strategy_slot() -> None:
    assert _resolve_model_override("backtest this 策略", _svc()) == "strat-model"
    assert _resolve_model_override("draft a plan", _svc()) == "strat-model"


def test_analysis_keywords_route_to_analysis_slot() -> None:
    assert _resolve_model_override("analyze BTC 行情", _svc()) == "analysis-model"
    assert _resolve_model_override("what is the trend?", _svc()) == "analysis-model"


def test_fallback_routes_to_quick_slot() -> None:
    assert _resolve_model_override("hello there", _svc()) == "quick-model"


def test_disabled_smart_mode_returns_none() -> None:
    assert _resolve_model_override("backtest 策略", _svc(enabled=False)) is None


def test_missing_smart_models_returns_none() -> None:
    assert _resolve_model_override("backtest 策略", _svc(models=None)) is None


def test_no_settings_service_returns_none() -> None:
    assert _resolve_model_override("backtest 策略", None) is None


def test_user_id_forwarded_to_settings_load() -> None:
    svc = _svc()
    _resolve_model_override("hello", svc, user_id="u-42")
    assert svc.calls == ["u-42"]


def test_missing_slot_key_returns_none_not_keyerror() -> None:
    svc = _svc(models={"strategy": "s"})  # no analysis / quick
    assert _resolve_model_override("hello", svc) is None

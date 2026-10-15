"""Contract test: supports_mark_price flag must match place_order signature.

Guards against flag drift — if a future refactor adds/removes mark_price
or signal_timestamp_ms parameter from an execution engine's place_order,
this test fails in CI rather than silently routing to the wrong branch
at runtime.
"""
import inspect
import pytest

from selene_space_exchange.execution.live_engine import LiveExecutionEngine
from selene_space_exchange.execution.shadow_engine import ShadowExecutionEngine
from selene_space_paper.paper_execution import PaperExecutionEngine

ENGINES = [LiveExecutionEngine, ShadowExecutionEngine, PaperExecutionEngine]
MARK_PARAMS = {"mark_price", "signal_timestamp_ms"}


@pytest.mark.parametrize("engine_cls", ENGINES)
def test_flag_signature_consistency(engine_cls):
    """flag 与 signature 必须一致:
    - flag=True  ⇒ signature 必须有 mark_price/signal_timestamp_ms
    - flag=False ⇒ signature 必须没有这 2 参
    """
    flag = getattr(engine_cls, "supports_mark_price", False)
    sig_params = set(inspect.signature(engine_cls.place_order).parameters.keys())

    if flag:
        missing = MARK_PARAMS - sig_params
        assert not missing, (
            f"{engine_cls.__name__}.supports_mark_price=True but "
            f"place_order signature missing {missing}"
        )
    else:
        present = MARK_PARAMS & sig_params
        assert not present, (
            f"{engine_cls.__name__}.supports_mark_price=False but "
            f"place_order signature has {present}"
        )

"""QuantDinger script-run-time adapter for selene.

Compile an exported QuantDinger strategy source into a :class:`QDStrategyRuntime`
that satisfies the selene ``StrategyRunner`` contract( on_kline/reset ), then
feed it to :class:`BacktestEngine`:

    from selene_space_strategy.qd import compile_qd_strategy, QDStrategyRuntime

    compiled = compile_qd_strategy(source, source_name="src48")
    runtime = QDStrategyRuntime(compiled, symbol="XLM/USDT", interval="1h")
    result = engine.run(runtime, data)

Important: any order_target_percent / get_position / get_history the strategy
calls are bound back onto its module globals per bar by the runtime; nothing
in the source is rewritten. Cleanroom re-implementation of the QuantDinger
script semantics (Apache-2.0), no code copied.
"""

from selene_space_strategy.qd.contract import (
    QDCompiledStrategy,
    compile_qd_strategy,
    parse_params,
)
from selene_space_strategy.qd.context import (
    QDContext,
    QDPosition,
    normalize_instrument,
)
from selene_space_strategy.qd.describe import (
    QDSourceInfo,
    describe_qd_source,
    parse_venue,
)
from selene_space_strategy.qd.runtime import QDStrategyRuntime
from selene_space_strategy.qd.sandbox import SecurityError, compile_strategy_source

__all__ = [
    "QDCompiledStrategy",
    "QDContext",
    "QDPosition",
    "QDSourceInfo",
    "QDStrategyRuntime",
    "SecurityError",
    "compile_qd_strategy",
    "compile_strategy_source",
    "describe_qd_source",
    "normalize_instrument",
    "parse_params",
    "parse_venue",
]

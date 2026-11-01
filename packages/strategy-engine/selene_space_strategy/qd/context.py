"""QuantDinger API surface bound into a strategy's namespace each bar.

A fresh :class:`QDContext` is handed to ``handle_data`` for every bar; its
``set_universe / subscribe / set_warmup / set_metadata / allow_leverage``
methods accumulate into a shared :class:`QDManifest` describing how the
strategy wants to consume data and deploy capital. ``context.params.get``
reads the parsed ``# @param`` defaults.

Runtime-side API names the strategy calls as globals (``get_history``,
``get_position``, ``order_target_percent``) live in ``runtime.py``; they are
injected per call. Instrument strings use the QuantDinger form
(``Crypto:XLM/USDT@okx:swap``), which :class:`QDSymbolMapping` normalizes to
the selene unified symbol (``XLM/USDT``) before touching history/portfolio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    import pandas as pd


@dataclass
class QDManifest:
    """Compile-time declarations collected from ``initialize(context)``."""

    universe: list[str] = field(default_factory=list)
    frequencies: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    warmup: int = 0
    direction_mode: str = "both"
    max_leverage: float = 1.0


class QDParams:
    """Dict-like access to the strategy's ``# @param`` defaults."""

    def __init__(self, defaults: dict[str, float | int] | None = None) -> None:
        self._values = dict(defaults or {})

    def get(self, name: str, default: float = 0.0) -> float:
        return self._values.get(name, default)


@dataclass
class QDPosition:
    """Snapshot of one side's holding; strategies read ``.amount``."""

    amount: float = 0.0
    #: Average entry price of the holding. Selene's Portfolio does not track
    #: cost basis, so the runtime fills this from its own entry-price record;
    #: ``0.0`` means flat (sources gate on ``avg_cost > 0``).
    avg_cost: float = 0.0


class _DataDuck:
    """The ``data`` argument for ``handle_data(context, data)``.

    Reads delegate to runtime callables (pandas-heavy; kept out of this
    module): ``history`` resamples the buffered driving bars to the requested
    frequency with QD's closed-bar visibility cutoff, ``current`` is the last
    visible bar's field. Strategies that never touch it (src48 and most
    plain exports) are unaffected.
    """

    def __init__(
        self,
        symbol: str,
        *,
        history_fn: Callable[..., pd.DataFrame] | None = None,
        current_fn: Callable[..., float] | None = None,
    ) -> None:
        self.symbol = symbol
        self._history_fn = history_fn
        self._current_fn = current_fn

    def history(
        self,
        symbols: object,
        *,
        count: int,
        fields: object = None,
        frequency: object = None,
    ) -> pd.DataFrame:
        if self._history_fn is None:
            raise RuntimeError("data.history not wired (context built without runtime)")
        return self._history_fn(
            symbols, count=count, fields=fields, frequency=frequency
        )

    def current(
        self,
        symbol: object,
        field: str = "close",
        default: float = 0.0,
        *,
        frequency: object = None,
    ) -> float:
        if self._current_fn is None:
            raise RuntimeError("data.current not wired (context built without runtime)")
        return self._current_fn(
            symbol, field=field, default=default, frequency=frequency
        )


def _ignore_declaration(*_args: object, **_kwargs: object) -> None:
    """Swallow an unmodeled QuantDinger declaration call."""
    return None


class QDContext:
    """Per-bar context handed to ``handle_data``."""

    def __init__(
        self,
        manifest: QDManifest,
        params: QDParams,
        *,
        data_symbol: str,
        current_ts: int | None = None,
        history_fn: Callable[..., pd.DataFrame] | None = None,
        current_fn: Callable[..., float] | None = None,
    ) -> None:
        self._manifest = manifest
        self._params = params
        self._data = _DataDuck(
            data_symbol, history_fn=history_fn, current_fn=current_fn
        )
        self._current_ts = current_ts

    def __getattr__(self, name: str) -> Callable[..., None]:
        """Tolerate declaration methods this adapter does not model.

        Exported sources are written against the wider QuantDinger context
        API, which declares more than selene consumes -- ``set_benchmark``
        and friends appear in ``initialize`` and would otherwise abort the
        strategy. Unknown declaration-shaped names (``set_*`` / ``allow_*``)
        resolve to a no-op; every other missing attribute, dunder probes
        from ``copy``/``repr`` included, still raises.
        """
        if not name.startswith("__") and (
            name.startswith("set_") or name.startswith("allow_")
        ):
            return _ignore_declaration
        raise AttributeError(name)

    # -- declarations (compile/live time) ----------------------------------

    def set_universe(self, symbols: list[str]) -> None:
        self._manifest.universe = list(symbols)

    def subscribe(
        self,
        frequency: str | None = None,
        fields: list[str] | None = None,
        **_: object,
    ) -> None:
        if frequency and frequency not in self._manifest.frequencies:
            self._manifest.frequencies.append(frequency)
        if fields:
            self._manifest.fields = list(
                dict.fromkeys([*self._manifest.fields, *fields])
            )

    def set_warmup(self, bars: int) -> None:
        self._manifest.warmup = int(bars)

    def set_metadata(self, *, direction_mode: str | None = None, **_: object) -> None:
        if direction_mode:
            self._manifest.direction_mode = direction_mode

    def allow_leverage(self, max_leverage: float) -> None:
        self._manifest.max_leverage = float(max_leverage)

    # -- read surfaces ------------------------------------------------------

    @property
    def params(self) -> QDParams:
        return self._params

    @property
    def data(self) -> _DataDuck:
        return self._data

    @property
    def manifest(self) -> QDManifest:
        return self._manifest

    @property
    def current_dt(self) -> pd.Timestamp | None:
        """Current bar's open time (naive UTC), the QD ``current_dt`` convention.

        In QD's backtest loop ``current_dt`` is the driving bar's timestamp,
        not wall-clock. ``None`` outside a bar (describe-time ``initialize``
        probe) -- real sources only read it in ``handle_data``.
        """
        if self._current_ts is None:
            return None
        import pandas as pd

        return pd.Timestamp(self._current_ts, unit="ms")


def normalize_instrument(instrument: str) -> str:
    """QuantDinger instrument id -> selene unified symbol.

    ``Crypto:XLM/USDT@okx:swap`` -> ``XLM/USDT`` (strip the prefix up to the
    first ``:``, then everything from the first ``@``).
    """
    base = instrument
    if ":" in base:
        base = base.split(":", 1)[1]
    if "@" in base:
        base = base.split("@", 1)[0]
    return base


class QDSymbolMapping:
    """Normalize QuantDinger instrument ids to selene unified symbols.

    ``Crypto:XLM/USDT@okx:swap`` -> ``XLM/USDT`` (strip prefix up to the
    first ``:``, then everything from the first ``@``).
    """

    def __init__(self, selene_symbol: str) -> None:
        self._selene = selene_symbol
        self._aliases: dict[str, str] = {}

    def register_alias(self, strategy_symbol: str) -> None:
        self._aliases[strategy_symbol] = self._selene

    def resolve(self, symbol: str | None) -> str:
        if not symbol:
            return self._selene
        if symbol in self._aliases:
            return self._aliases[symbol]
        return normalize_instrument(symbol) or self._selene

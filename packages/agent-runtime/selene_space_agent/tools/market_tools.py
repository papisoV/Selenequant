"""Market data tools — ticker, kline, and orderbook queries.

Each tool wraps a duck-typed market service (``get_ticker`` / ``get_kline``
/ ``get_orderbook`` keyed by symbol) and formats the result as LLM-readable
text.  Recoverable data-missing paths emit a NO_DATA_AVAILABLE sentinel
(A2, via data_sentinel.no_data_output) instead of a bare string, and every
successful payload carries a "data as-of" notice (D4) so the LLM can tell
how stale its snapshot is before acting on it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from selene_space_types.risk import RiskLevel

from selene_space_agent.tools.base import BaseTool, ToolResult
from selene_space_core.kline_paths import default_klines_db_path


def _fmt(value: float) -> str:
    return f"{value:,.2f}"


def _require_symbol(args: dict[str, Any]) -> str | None:
    symbol = args.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        return None
    return symbol


def _no_data(reason: str, source: str) -> ToolResult:
    """Recoverable miss path: sentinel text via the output channel (A2).

    error stays empty so react.py routes the sentinel text (not just an
    error string) into the LLM context.
    """
    from selene_space_agent.tools.data_sentinel import no_data_output

    return ToolResult(output=no_data_output(reason, source=source), error="")


_UTC8 = timezone(timedelta(hours=8))


def _as_of_line(ts_ms: Any) -> str:
    """D4 look-ahead guard: human-readable data-as-of notice (UTC+8)."""
    try:
        ts = int(ts_ms)
        if ts <= 0:
            raise ValueError
        dt = datetime.fromtimestamp(ts / 1000, tz=_UTC8)
        stamp = dt.strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        stamp = "未知（数据源未带时间戳）"
    return f"数据截至: {stamp} (UTC+8) — 决策前核对此时间与当前时间的间隔"


class _MarketToolBase(BaseTool):
    """Shared plumbing for read-only market data tools."""

    def __init__(self, market_service: Any) -> None:
        self._market_service = market_service

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def execute(
        self, args: dict[str, Any]
    ) -> ToolResult:  # pragma: no cover - abstract
        raise NotImplementedError


class MarketTickerTool(_MarketToolBase):
    def __init__(self, market_service: Any) -> None:
        super().__init__(market_service)

    @property
    def name(self) -> str:
        return "market_ticker"

    @property
    def description(self) -> str:
        return "Get the latest 24h ticker stats for a trading pair."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Trading pair, e.g. BTC/USDT",
                },
            },
            "required": ["symbol"],
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        symbol = _require_symbol(args)
        if symbol is None:
            return ToolResult(output="", error="Missing required parameter: symbol")

        ticker = self._market_service.get_ticker(symbol)
        if ticker is None:
            return _no_data(f"No ticker data available for {symbol}", "ticker")

        lines = [
            f"Ticker for {ticker.symbol}:",
            f"  Last Price: {_fmt(ticker.last_price)}",
            f"  Bid: {_fmt(ticker.bid)} | Ask: {_fmt(ticker.ask)}",
            f"  24h Volume: {ticker.volume_24h:,.2f}",
            f"  Change: {ticker.change_24h_pct:+.2f}%",
            _as_of_line(getattr(ticker, "timestamp", None)),
        ]
        return ToolResult(output="\n".join(lines))


class MarketKlineTool(_MarketToolBase):
    def __init__(self, market_service: Any) -> None:
        super().__init__(market_service)

    @property
    def name(self) -> str:
        return "market_kline"

    @property
    def description(self) -> str:
        return "Get the latest candlestick bar for a trading pair."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Trading pair, e.g. BTC/USDT",
                },
            },
            "required": ["symbol"],
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        symbol = _require_symbol(args)
        if symbol is None:
            return ToolResult(output="", error="Missing required parameter: symbol")

        kline = self._market_service.get_kline(symbol)
        if kline is None:
            return _no_data(f"No kline data available for {symbol}", "kline")

        state = "closed" if kline.closed else "live"
        lines = [
            f"Kline for {kline.symbol} ({kline.interval}, {state}):",
            f"  Open: {_fmt(kline.open)}",
            f"  High: {_fmt(kline.high)}",
            f"  Low: {_fmt(kline.low)}",
            f"  Close: {_fmt(kline.close)}",
            f"  Volume: {kline.volume:,.2f}",
            _as_of_line(getattr(kline, "timestamp", None)),
        ]
        return ToolResult(output="\n".join(lines))


class MarketOrderbookTool(_MarketToolBase):
    def __init__(self, market_service: Any) -> None:
        super().__init__(market_service)

    @property
    def name(self) -> str:
        return "market_orderbook"

    @property
    def description(self) -> str:
        return "Get current order book depth for a trading pair."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Trading pair, e.g. BTC/USDT",
                },
                "depth": {
                    "type": "integer",
                    "description": "Number of price levels per side (default 5)",
                },
            },
            "required": ["symbol"],
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        symbol = _require_symbol(args)
        if symbol is None:
            return ToolResult(output="", error="Missing required parameter: symbol")

        depth_raw = args.get("depth", 5)
        try:
            depth = max(1, int(depth_raw))
        except (TypeError, ValueError):
            depth = 5

        book = self._market_service.get_orderbook(symbol)
        if book is None:
            return _no_data(f"No orderbook data available for {symbol}", "orderbook")

        lines = [f"Order book for {book.symbol}:"]

        asks = list(reversed(list(book.asks)[:depth]))
        lines.append("Asks:")
        for level in asks:
            lines.append(f"  {_fmt(level.price)} x {level.quantity:,.4f}")

        bids = list(book.bids)[:depth]
        lines.append("Bids:")
        for level in bids:
            lines.append(f"  {_fmt(level.price)} x {level.quantity:,.4f}")

        if book.asks and book.bids:
            spread = book.asks[0].price - book.bids[0].price
            lines.append(f"Spread: {_fmt(spread)}")

        lines.append(_as_of_line(getattr(book, "timestamp", None)))
        return ToolResult(output="\n".join(lines))


class MarketSnapshotTool(BaseTool):
    """Defensive indicator snapshot over the local klines.db (A1).

    Read-only, SAFE. Every indicator degrades to ``N/A (ExcType)`` on
    failure so one broken metric never kills the whole snapshot; an empty
    partition emits the A2 sentinel instead of zeros the LLM could
    misread as real market data.
    """

    _INDICATOR_COUNT = 12

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or str(default_klines_db_path())

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    @property
    def name(self) -> str:
        return "market_snapshot"

    @property
    def description(self) -> str:
        return (
            "Defensive indicator snapshot (trend/momentum/volatility) computed "
            "from the local klines.db for one symbol."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Trading pair, e.g. BTC/USDT",
                },
                "exchange": {
                    "type": "string",
                    "description": "Exchange partition (default binance)",
                },
                "market_type": {
                    "type": "string",
                    "description": "Market type partition (default spot)",
                },
                "interval": {
                    "type": "string",
                    "description": "Kline interval (default 1h)",
                },
            },
            "required": ["symbol"],
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        symbol = _require_symbol(args)
        if symbol is None:
            return ToolResult(output="", error="Missing required parameter: symbol")

        exchange = args.get("exchange") or "binance"
        market_type = args.get("market_type") or "spot"
        interval = args.get("interval") or "1h"

        try:
            from selene_space_market.kline_db import KlineDB
        except ImportError as exc:
            return _no_data(f"kline storage unavailable: {exc}", "snapshot")

        try:
            db = KlineDB(self._db_path)
            frame = db.get_klines(
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                interval=interval,
            )
        except Exception as exc:
            return _no_data(f"klines.db query failed: {exc}", "snapshot")

        if frame is None or frame.empty:
            return _no_data(
                f"No klines in local db for {symbol} "
                f"({exchange}/{market_type}/{interval})",
                "snapshot",
            )

        closes = frame["close"].astype(float)
        latest_ts = int(frame["timestamp"].iloc[-1])

        lines = [
            f"Market snapshot for {symbol} ({exchange}/{market_type}/{interval}):",
            f"  Bars loaded: {len(frame)}",
            f"  Last close: {_fmt(float(closes.iloc[-1]))}",
        ]
        lines.extend(self._indicators(closes))
        lines.append(_as_of_line(latest_ts))
        return ToolResult(output="\n".join(lines))

    def _indicators(self, closes: Any) -> list[str]:
        """~12 defensive metrics; each wrapped so one failure → N/A."""
        out: list[str] = []

        def _add(label: str, fn: Any) -> None:
            try:
                value = fn()
                out.append(f"  {label}: {value}")
            except Exception as exc:  # noqa: BLE001 — defensive by design
                out.append(f"  {label}: N/A ({type(exc).__name__})")

        s = closes

        def _sma(n: int) -> str:
            return _fmt(float(s.tail(n).mean()))

        def _ema(n: int) -> str:
            return _fmt(float(s.ewm(span=n, adjust=False).mean().iloc[-1]))

        def _rsi(n: int = 14) -> str:
            delta = s.diff().dropna()
            if delta.empty:
                raise ValueError("insufficient bars")
            up = delta.clip(lower=0).tail(n).mean()
            down = (-delta.clip(upper=0)).tail(n).mean()
            if down == 0:
                return "100.00" if up > 0 else "50.00"
            rs = up / down
            return f"{100 - 100 / (1 + rs):.2f}"

        def _std(n: int) -> str:
            return _fmt(float(s.tail(n).std()))

        def _ret(n: int) -> str:
            if len(s) <= n:
                raise ValueError("insufficient bars")
            return f"{(float(s.iloc[-1]) / float(s.iloc[-1 - n]) - 1) * 100:+.2f}%"

        def _hi_lo(n: int) -> str:
            window = s.tail(n)
            return f"{_fmt(float(window.max()))} / {_fmt(float(window.min()))}"

        _add("SMA20", lambda: _sma(20))
        _add("SMA50", lambda: _sma(50))
        _add("EMA12", lambda: _ema(12))
        _add("EMA26", lambda: _ema(26))
        _add("RSI14", _rsi)
        _add("STD20", lambda: _std(20))
        _add("Return1d", lambda: _ret(24))
        _add("Return7d", lambda: _ret(168))
        _add("Return30d", lambda: _ret(720))
        _add("Hi/Lo 24 bars", lambda: _hi_lo(24))
        _add("Hi/Lo 168 bars", lambda: _hi_lo(168))
        _add("Bars span", lambda: f"{len(s)} bars")
        return out

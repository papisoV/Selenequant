"""Multi-source A-share daily kline fetching with priority failover.

Fetchers are tried in ascending priority order (smaller number = tried
first). A failure on one source falls through to the next; when every
source fails the last error is raised (or a last-good snapshot within TTL
is served, marked ``source="last-good:<name>"``). Individual fetcher
priority can be overridden per-source via ``SELENE_<NAME>_PRIORITY``
environment variables, read at registration time.

Circuit breaking: consecutive ``RateLimitError`` failures on a source
(at or above ``SELENE_FETCHER_BREAKER_THRESHOLD``, default 3) open a
cooldown window of ``SELENE_FETCHER_BREAKER_COOLDOWN_SEC`` seconds
(default 300) during which that source is skipped entirely. A successful
fetch resets the streak. Each registered source is guarded by its own
mutex so concurrent callers serialize per source.

Successful results are cleaned (empty rows dropped, numeric columns cast,
sorted by date ascending) and enriched with ``ma5``/``ma10``/``ma20`` and
``volume_ratio`` (latest volume vs. mean volume of the previous five
bars; 1.0 when no prior bar exists).

Fetchers here are synchronous (requests-based) — call sites that are async
should wrap in asyncio.to_thread.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable

import requests

logger = logging.getLogger(__name__)

_DEFAULT_LAST_GOOD_TTL_SEC = 300.0
_DEFAULT_BREAKER_THRESHOLD = 3
_DEFAULT_BREAKER_COOLDOWN_SEC = 300.0
_HTTP_TIMEOUT_SEC = 10

_STANDARD_COLUMNS = ("date", "open", "high", "low", "close", "volume")


class RateLimitError(Exception):
    """Upstream API rate limit hit."""


class DataSourceUnavailableError(Exception):
    """Data source unusable: HTTP error, empty payload, parse failure, or
    network-level failure (timeouts/connection errors normalize here)."""


class BaseFetcher(ABC):
    """Abstract A-share daily fetcher.

    ``priority`` semantics: smaller number is tried first (99 = last
    resort). ``fetch_daily`` returns raw daily rows (date/open/high/low/
    close/volume dicts) — normalization to downstream events stays with
    the caller.
    """

    name: str = "BaseFetcher"
    priority: int = 99

    @abstractmethod
    def fetch_daily(self, symbol: str) -> list[dict]:
        """Fetch daily bars for *symbol*; raise on any failure."""


def _to_a_symbol(symbol: str) -> str:
    """'000001.SZ' → 'sz000001'; '600519.SH' → 'sh600519'."""
    code, _, suffix = symbol.partition(".")
    prefix = {"SZ": "sz", "SH": "sh"}.get(suffix.upper())
    if not prefix or not code.isdigit() or len(code) != 6:
        raise ValueError(f"Invalid A-share symbol: {symbol!r}")
    return f"{prefix}{code}"


def _read_priority_env(name: str, default: int) -> int:
    """``SELENE_<NAME>_PRIORITY`` override, read at fetcher construction.

    Tolerant parsing: invalid values warn and keep the default.
    """
    raw = os.getenv(f"SELENE_{name.upper()}_PRIORITY")
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(
            "SELENE_%s_PRIORITY=%r is not a valid integer; keeping default %d",
            name.upper(),
            raw,
            default,
        )
        return default


def _read_int_env(var: str, default: int) -> int:
    raw = os.getenv(var)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(
            "%s=%r is not a valid integer; keeping default %d", var, raw, default
        )
        return default


def _read_float_env(var: str, default: float) -> float:
    raw = os.getenv(var)
    if raw is None:
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(
            "%s=%r is not a valid number; keeping default %s", var, raw, default
        )
        return default


class _SourceSlot:
    """Registry entry: fetcher + its mutex + circuit-breaker state."""

    __slots__ = ("cooldown_sec", "fetcher", "lock", "open_until", "threshold", "streak")

    def __init__(
        self,
        fetcher: BaseFetcher,
        *,
        threshold: int,
        cooldown_sec: float,
    ) -> None:
        self.fetcher = fetcher
        self.lock = threading.Lock()
        self.threshold = threshold
        self.cooldown_sec = cooldown_sec
        self.streak = 0
        self.open_until: float | None = None

    def is_open(self, now: float) -> bool:
        return self.open_until is not None and now < self.open_until

    def record_rate_limited(self, now: float) -> None:
        self.streak += 1
        if self.streak >= self.threshold:
            self.open_until = now + self.cooldown_sec
            self.streak = 0

    def record_success(self) -> None:
        self.streak = 0
        self.open_until = None


def _clean_rows(rows: list[dict]) -> list[dict]:
    """Drop empty/invalid rows, cast numeric columns, sort by date asc."""
    cleaned: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not all(col in row for col in _STANDARD_COLUMNS):
            continue
        try:
            cleaned.append(
                {
                    "date": str(row["date"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                }
            )
        except (TypeError, ValueError):
            continue
    cleaned.sort(key=lambda r: r["date"])
    return cleaned


def _enrich_indicators(rows: list[dict]) -> list[dict]:
    """Add ma5/ma10/ma20 (rolling means over available bars) and
    volume_ratio (latest volume vs mean of previous five bars' volume;
    1.0 when no prior bar). Expects rows sorted by date ascending."""
    out: list[dict] = [dict(r) for r in rows]
    closes = [r["close"] for r in out]
    volumes = [r["volume"] for r in out]
    for i, row in enumerate(out):
        for window in (5, 10, 20):
            lookback = closes[max(0, i - window + 1) : i + 1]
            row[f"ma{window}"] = sum(lookback) / len(lookback)
        if i == 0:
            row["volume_ratio"] = 1.0
        else:
            prev = volumes[max(0, i - 5) : i]
            row["volume_ratio"] = volumes[i] / (sum(prev) / len(prev))
    return out


class SinaDailyFetcher(BaseFetcher):
    """Sina kline API fetcher (REST)."""

    name = "sina"
    priority = 10

    _KLINE_URL = (
        "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
    )
    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://finance.sina.com.cn",
    }

    def __init__(self) -> None:
        self.priority = _read_priority_env(self.name, self.priority)

    def fetch_daily(self, symbol: str) -> list[dict]:
        sina_sym = _to_a_symbol(symbol)
        try:
            resp = requests.get(
                self._KLINE_URL,
                params={"symbol": sina_sym, "scale": 240, "ma": "no", "datalen": 320},
                headers=self._HEADERS,
                timeout=_HTTP_TIMEOUT_SEC,
            )
        except requests.RequestException as exc:
            raise DataSourceUnavailableError(
                f"sina request failed for {symbol}: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise DataSourceUnavailableError(
                f"sina HTTP {resp.status_code} for {symbol}"
            )
        try:
            rows = json.loads(resp.text.strip() or "null")
        except ValueError as exc:
            raise DataSourceUnavailableError(
                f"sina JSON parse failed for {symbol}"
            ) from exc
        if not rows:
            raise DataSourceUnavailableError(f"sina returned empty data for {symbol}")
        out: list[dict] = []
        try:
            for row in rows:
                out.append(
                    {
                        "date": str(row["day"]),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                    }
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise DataSourceUnavailableError(
                f"sina row parse failed for {symbol}"
            ) from exc
        return out


class TencentDailyFetcher(BaseFetcher):
    """Tencent ifzq fqkline fetcher.

    Column order in qfqday/day rows is [date, open, close, high, low,
    volume] — close before high/low, unlike the usual OHLC order.
    """

    name = "tencent"
    priority = 20

    _KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    _HEADERS = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
    }

    def __init__(self) -> None:
        self.priority = _read_priority_env(self.name, self.priority)

    def fetch_daily(self, symbol: str) -> list[dict]:
        tx_sym = _to_a_symbol(symbol)
        try:
            resp = requests.get(
                self._KLINE_URL,
                params={"param": f"{tx_sym},day,,,320,qfq"},
                headers=self._HEADERS,
                timeout=_HTTP_TIMEOUT_SEC,
            )
        except requests.RequestException as exc:
            raise DataSourceUnavailableError(
                f"tencent request failed for {symbol}: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise DataSourceUnavailableError(
                f"tencent HTTP {resp.status_code} for {symbol}"
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise DataSourceUnavailableError(
                f"tencent JSON parse failed for {symbol}"
            ) from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        item = data.get(tx_sym) if isinstance(data, dict) else None
        rows: list | None = None
        if isinstance(item, dict):
            rows = item.get("qfqday") or item.get("day")
        if not rows:
            raise DataSourceUnavailableError(
                f"tencent returned empty data for {symbol}"
            )
        out: list[dict] = []
        try:
            for row in rows:
                if not isinstance(row, list) or len(row) < 6:
                    continue
                out.append(
                    {
                        "date": str(row[0]),
                        "open": float(row[1]),
                        "close": float(row[2]),
                        "high": float(row[3]),
                        "low": float(row[4]),
                        "volume": float(row[5]),
                    }
                )
        except (TypeError, ValueError) as exc:
            raise DataSourceUnavailableError(
                f"tencent row parse failed for {symbol}"
            ) from exc
        if not out:
            raise DataSourceUnavailableError(f"tencent rows unparseable for {symbol}")
        return out


class FetcherManager:
    """Priority-ordered failover over registered fetchers + last-good cache.

    register() keeps the registry sorted ascending by priority (stable:
    equal priorities preserve registration order). fetch_daily tries
    sources in that order; any failure (typed or network) falls to the
    next source; when all fail, either the last-good snapshot within TTL
    is served (marked source="last-good:<name>") or the last exception
    propagates.
    """

    def __init__(
        self,
        *,
        last_good_ttl_sec: float | None = None,
        breaker_threshold: int | None = None,
        breaker_cooldown_sec: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = (
            last_good_ttl_sec
            if last_good_ttl_sec is not None
            else _read_float_env(
                "SELENE_FETCHER_LAST_GOOD_TTL_SEC", _DEFAULT_LAST_GOOD_TTL_SEC
            )
        )
        self._breaker_threshold = (
            breaker_threshold
            if breaker_threshold is not None
            else _read_int_env(
                "SELENE_FETCHER_BREAKER_THRESHOLD", _DEFAULT_BREAKER_THRESHOLD
            )
        )
        self._breaker_cooldown_sec = (
            breaker_cooldown_sec
            if breaker_cooldown_sec is not None
            else _read_float_env(
                "SELENE_FETCHER_BREAKER_COOLDOWN_SEC", _DEFAULT_BREAKER_COOLDOWN_SEC
            )
        )
        self._clock = clock
        self._slots: list[_SourceSlot] = []
        # key = symbol → (expires_at, source_name, rows)
        self._last_good: dict[str, tuple[float, str, list[dict]]] = {}

    def register(self, fetcher: BaseFetcher) -> None:
        slot = _SourceSlot(
            fetcher,
            threshold=self._breaker_threshold,
            cooldown_sec=self._breaker_cooldown_sec,
        )
        self._slots.append(slot)
        # Stable ascending sort: equal priorities keep registration order.
        self._slots.sort(key=lambda s: s.fetcher.priority)

    @property
    def source_order(self) -> list[str]:
        """Registered source names in try order (diagnostics/tests)."""
        return [s.fetcher.name for s in self._slots]

    def _try_source(self, slot: _SourceSlot, symbol: str) -> list[dict]:
        """Fetch through one source under its mutex; update breaker state."""
        with slot.lock:
            rows = slot.fetcher.fetch_daily(symbol)
            slot.record_success()
            return rows

    def fetch_daily(self, symbol: str) -> tuple[list[dict], str]:
        """Fetch daily rows, trying sources by priority; (rows, source_name).

        On total failure: serve the last-good snapshot if within TTL, else
        re-raise the last failure (DataSourceUnavailableError when no source
        was even attempted, e.g. empty registry). Successful rows are
        cleaned and enriched with ma5/ma10/ma20/volume_ratio.
        """
        now = self._clock()
        last_exc: Exception | None = None
        for slot in self._slots:
            if slot.is_open(now):
                logger.warning(
                    "fetch_daily: source %s in cooldown until %s — skipping for %s",
                    slot.fetcher.name,
                    slot.open_until,
                    symbol,
                )
                continue
            try:
                raw_rows = self._try_source(slot, symbol)
            except (RateLimitError, DataSourceUnavailableError) as exc:
                if isinstance(exc, RateLimitError):
                    slot.record_rate_limited(now)
                logger.warning(
                    "fetch_daily: source %s failed for %s: %s — falling back",
                    slot.fetcher.name,
                    symbol,
                    exc,
                )
                last_exc = exc
            except ValueError as exc:
                # Invalid symbol: deterministic across sources — no fallback.
                raise
            except Exception as exc:  # noqa: BLE001 — network layer normalize
                logger.warning(
                    "fetch_daily: source %s network error for %s: %s — falling back",
                    slot.fetcher.name,
                    symbol,
                    exc,
                )
                last_exc = exc
            else:
                if not raw_rows:
                    last_exc = DataSourceUnavailableError(
                        f"{slot.fetcher.name} returned empty data for {symbol}"
                    )
                    logger.warning(
                        "fetch_daily: source %s failed for %s: %s — falling back",
                        slot.fetcher.name,
                        symbol,
                        last_exc,
                    )
                    continue
                rows = _enrich_indicators(_clean_rows(raw_rows))
                self._last_good[symbol] = (
                    now + self._ttl,
                    slot.fetcher.name,
                    [dict(r) for r in rows],
                )
                return rows, slot.fetcher.name

        cached = self._last_good.get(symbol)
        if cached is not None and cached[0] > now:
            _, cached_source, cached_rows = cached
            logger.warning(
                "fetch_daily: all sources failed for %s — serving last-good "
                "snapshot from %s",
                symbol,
                cached_source,
            )
            return [dict(r) for r in cached_rows], f"last-good:{cached_source}"

        if last_exc is not None:
            raise last_exc
        raise DataSourceUnavailableError(
            f"no fetchers registered; cannot fetch {symbol}"
        )

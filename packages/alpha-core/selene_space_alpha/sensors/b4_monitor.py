"""B4 monitoring quadruple sensor (QD-absorption Phase 2) — alpha-core layer.

v0 scope locked by the consensus plan (qd-absorption-consensus.md, pre-mortem
scenario 3 "scope explosion guard"): exactly three pieces — quadruple registry,
new-bar-detection recompute, TTL cache with stale marking. Push channels are
explicitly OUT of v0 ("超界即砍"); the data-source dimension is hard-wired to
klines.db single source (open-question 2 resolved by Architect ruling).

Concept-level absorption from the QD realtime-monitoring idea (monitoring item
= (source, symbol, interval, factor), recompute only on new bar, TTL cache,
persistent watchlist) — crypto-domain independent implementation, cleanroom.

Registry persistence: JSON via atomic_write (crash-safe, same infra as the
mining engine), stored under the sensor module's own data dir.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from selene_space_core.infra.atomic_write import atomic_write

from ..factors.btc_background import DEFAULT_KLINES_DB_PATH
from ..factors.btc_bg_factors import (
    BTC_BG_DB_PATH_ENV,
    compute_btc_bg_rs_extreme,
    compute_btc_bg_rs_level,
    compute_btc_bg_rs_slope,
)

#: v0 data-source dimension is hard-wired (Architect ruling: klines.db single
#: source; adding sources is a v1+ decision, not an open question).
SOURCE_KLINES_DB = "klines.db"

#: Quadruple registry file lives next to the klines db's sibling data dir.
DEFAULT_REGISTRY_PATH = Path("data") / "b4_monitor_registry.json"

#: Seconds a computed reading stays fresh. Beyond this the reading is served
#: with ``stale: true`` (never silently treated as fresh).
DEFAULT_TTL_SECONDS = 300.0

#: How many bars the recompute window pulls from klines.db.
RECOMPUTE_BARS = 300

_FACTOR_FNS = {
    "BTC_BG_RS_LEVEL": compute_btc_bg_rs_level,
    "BTC_BG_RS_SLOPE": compute_btc_bg_rs_slope,
    "BTC_BG_RS_EXTREME": compute_btc_bg_rs_extreme,
}


@dataclass(frozen=True)
class Quadruple:
    """Monitoring item: (source, symbol, interval, factor)."""

    source: str
    symbol: str
    interval: str
    factor: str

    def key(self) -> str:
        return f"{self.source}|{self.symbol}|{self.interval}|{self.factor}"


@dataclass
class Reading:
    """Latest computed reading for one quadruple."""

    value: float | None
    computed_at: float  # unix seconds
    last_bar_ts: int | None  # ms timestamp of the newest bar covered
    stale: bool


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> list[Quadruple]:
    """Read the persisted quadruple registry (empty list when absent/corrupt)."""
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [
            Quadruple(
                source=item["source"],
                symbol=item["symbol"],
                interval=item["interval"],
                factor=item["factor"],
            )
            for item in payload.get("quadruples", [])
        ]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def save_registry(
    quadruples: list[Quadruple], path: Path = DEFAULT_REGISTRY_PATH
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        str(path),
        json.dumps(
            {"quadruples": [asdict(q) for q in quadruples]},
            ensure_ascii=False,
            indent=2,
        ),
    )


class B4Monitor:
    """Quadruple registry + new-bar recompute + TTL cache (v0, single source)."""

    def __init__(
        self,
        db_path: Path | None = None,
        registry_path: Path = DEFAULT_REGISTRY_PATH,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock=time.time,
    ):
        self._db_path = db_path or DEFAULT_KLINES_DB_PATH
        self._registry_path = registry_path
        self._ttl = float(ttl_seconds)
        self._clock = clock
        # (symbol, interval) -> last-seen bar timestamp (ms)
        self._last_bar_ts: dict[tuple[str, str], int] = {}
        # quadruple key -> Reading
        self._cache: dict[str, Reading] = {}

    # -- registry ------------------------------------------------------------

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def register(self, symbol: str, interval: str, factor: str) -> Quadruple:
        """Add a quadruple to the persistent registry (idempotent)."""
        if factor not in _FACTOR_FNS:
            raise ValueError(f"unknown factor {factor!r}; known: {sorted(_FACTOR_FNS)}")
        q = Quadruple(
            source=SOURCE_KLINES_DB, symbol=symbol, interval=interval, factor=factor
        )
        quads = load_registry(self._registry_path)
        if all(existing.key() != q.key() for existing in quads):
            quads.append(q)
            save_registry(quads, self._registry_path)
        return q

    def unregister(self, symbol: str, interval: str, factor: str) -> bool:
        """Remove a quadruple; returns True when it existed."""
        q = Quadruple(
            source=SOURCE_KLINES_DB, symbol=symbol, interval=interval, factor=factor
        )
        quads = load_registry(self._registry_path)
        kept = [existing for existing in quads if existing.key() != q.key()]
        if len(kept) == len(quads):
            return False
        save_registry(kept, self._registry_path)
        self._cache.pop(q.key(), None)
        return True

    def list_quadruples(self) -> list[Quadruple]:
        return load_registry(self._registry_path)

    def cache_contains(self, symbol: str, interval: str, factor: str) -> bool:
        """Whether a cached reading exists for this quadruple (no computation)."""
        return f"{SOURCE_KLINES_DB}|{symbol}|{interval}|{factor}" in self._cache

    # -- recompute + cache ----------------------------------------------------

    def _load_close(self, symbol: str, interval: str) -> pd.DataFrame:
        """OHLCV frame for (symbol, interval) from klines.db, newest RECOMPUTE_BARS."""
        if not self._db_path.exists():
            raise FileNotFoundError(f"klines db not found: {self._db_path}")
        conn = sqlite3.connect(str(self._db_path))
        try:
            df = pd.read_sql_query(
                "SELECT timestamp, open, high, low, close, volume FROM klines "
                "WHERE symbol = ? AND interval = ? AND exchange = 'okx' "
                "ORDER BY timestamp DESC LIMIT ?",
                conn,
                params=(symbol, interval, RECOMPUTE_BARS),
            )
        finally:
            conn.close()
        if df.empty:
            raise ValueError(f"no klines for {symbol} {interval} in {self._db_path}")
        df = df.sort_values("timestamp").reset_index(drop=True)
        idx = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df.set_index(idx)[["open", "high", "low", "close", "volume"]]

    def _newest_bar_ts(self, symbol: str, interval: str) -> int:
        frame = self._load_close(symbol, interval)
        return int(frame.index[-1].value // 1_000_000)

    def recompute(self, symbol: str, interval: str, factor: str) -> Reading:
        """Force a recompute of one quadruple and refresh the cache entry."""
        fn = _FACTOR_FNS[factor]
        frame = self._load_close(symbol, interval)
        # The bg factors read the db themselves; keep the env override pointed
        # at our db path so tests and endpoint use the same source.
        old = os.environ.get(BTC_BG_DB_PATH_ENV)
        os.environ[BTC_BG_DB_PATH_ENV] = str(self._db_path)
        try:
            series = fn(frame)
        finally:
            if old is None:
                os.environ.pop(BTC_BG_DB_PATH_ENV, None)
            else:
                os.environ[BTC_BG_DB_PATH_ENV] = old
        last_val = series.dropna()
        bar_ts = int(frame.index[-1].value // 1_000_000)
        reading = Reading(
            value=None if last_val.empty else float(last_val.iloc[-1]),
            computed_at=self._clock(),
            last_bar_ts=bar_ts,
            stale=False,
        )
        self._cache[f"{SOURCE_KLINES_DB}|{symbol}|{interval}|{factor}"] = reading
        self._last_bar_ts[(symbol, interval)] = bar_ts
        return reading

    def read(self, symbol: str, interval: str, factor: str) -> tuple[Reading, bool]:
        """Serve a reading; recompute only when a new bar appeared or TTL expired.

        Returns ``(reading, recomputed)``. The returned reading carries
        ``stale=True`` when served from cache past TTL (value is still served —
        the caller decides what to do with stale data).
        """
        q_key = f"{SOURCE_KLINES_DB}|{symbol}|{interval}|{factor}"
        if factor not in _FACTOR_FNS:
            raise ValueError(f"unknown factor {factor!r}; known: {sorted(_FACTOR_FNS)}")
        now = self._clock()
        cached = self._cache.get(q_key)
        recomputed = False
        if cached is not None:
            age = now - cached.computed_at
            try:
                newest: int = self._newest_bar_ts(symbol, interval)
            except (FileNotFoundError, ValueError):
                newest = int(cached.last_bar_ts or 0)  # db unavailable: serve cache
            has_new_bar = newest != cached.last_bar_ts
            if not has_new_bar and age <= self._ttl:
                return cached, False
            # past TTL or new bar → block on recompute (read() always returns
            # a fresh reading; callers wanting no-block use read_stale_aware)
        reading = self.recompute(symbol, interval, factor)
        recomputed = True
        return reading, recomputed

    def read_stale_aware(self, symbol: str, interval: str, factor: str) -> Reading:
        """Read that prefers serving stale cache instantly over blocking recompute.

        New bar or TTL expired → return cached reading with ``stale=True`` and
        let the caller decide when to recompute (endpoint uses 202 task pattern).
        Cache miss → recompute synchronously (first read must have a value).
        """
        q_key = f"{SOURCE_KLINES_DB}|{symbol}|{interval}|{factor}"
        if factor not in _FACTOR_FNS:
            raise ValueError(f"unknown factor {factor!r}; known: {sorted(_FACTOR_FNS)}")
        cached = self._cache.get(q_key)
        if cached is None:
            return self.recompute(symbol, interval, factor)
        try:
            newest: int = self._newest_bar_ts(symbol, interval)
        except (FileNotFoundError, ValueError):
            newest = int(cached.last_bar_ts or 0)
        has_new_bar = newest != cached.last_bar_ts
        expired = (self._clock() - cached.computed_at) > self._ttl
        if has_new_bar or expired:
            return Reading(
                value=cached.value,
                computed_at=cached.computed_at,
                last_bar_ts=cached.last_bar_ts,
                stale=True,
            )
        return cached

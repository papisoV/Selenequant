# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 papisoV
# Source: https://github.com/papisoV/tick-stock-panel
"""Concept (概念) membership for A-shares — one symbol → many concepts.

TSP keeps this in its `ext_gn_ths` extension table, populated from a preset at
`backend/app/services/ext_presets.py:33`. The preset is pulled on demand and
never blocks startup ("拉取失败只记 warning, 不阻断启动"), which is the same
posture this module takes toward the endpoint it reads.

The endpoint is a **single-host public GET maintained by the upstream author**,
not an exchange or a licensed vendor. Measured 2026-10-18: HTTP 200, 1.65 MB,
3.39 s, 5582 symbols / 1199 concepts / 92254 memberships, zero malformed rows.
It has no SLA and no documented license, so it is treated here as a *degradable
external dependency*: results are cached on disk for a day, a failure logs and
returns whatever is on disk (or an empty frame), and nothing ever raises.

Why disk and not memory: membership is a snapshot, not a time series — there is
no "membership at date T". One fetch per day is the whole refresh story, and a
process restart should not spend 3.4 s to rediscover it.

Relationship to the 东财 board list already in `data_fetcher`: that answers "how
did each board move today" from Eastmoney's own board taxonomy. This module
answers "which stocks belong to which concept" from 同花顺's taxonomy. Measured
2026-10-18, the two name spaces overlap on only 171 of 1199 names (东财's
「5G概念」 vs THS's 「5G」), so they are *different concepts*, not the same list
under two vendors. Do not mix them in one ranking.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

CONCEPT_SOURCE_URL = "https://shy313.com/api/plugins/market_flow/exports/ths-concepts"

# Membership is the *current* snapshot replayed over history: an early session
# is attributed with today's constituents. Callers that publish a history must
# surface this, because a concept that did not exist three years ago will
# otherwise appear to have had a flat history rather than no history.
MEMBERSHIP_NOTE = (
    "概念成分为当前快照, 回看历史存在归属漂移(新概念不会出现在旧时段, "
    "成分调整会改归属), 越近越准"
)

CACHE_DIR = Path(__file__).parent.parent / "data_cache" / "concept_map"
CACHE_NAME = "ths-concepts.parquet"

# Membership changes are a daily affair at most; an hourly TTL would only add
# load to a single-host endpoint run by a volunteer.
_DEFAULT_TTL_SEC = 24 * 3600.0
_REQUEST_TIMEOUT = 20.0

COLUMNS: tuple[str, ...] = ("symbol", "name", "concept")


def _cache_path() -> Path:
    return CACHE_DIR / CACHE_NAME


def _ttl_seconds() -> float:
    raw = os.environ.get("SELENE_CONCEPT_MAP_TTL_SEC", "")
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning(
                "concept_map: ignoring non-numeric SELENE_CONCEPT_MAP_TTL_SEC=%r", raw
            )
    return _DEFAULT_TTL_SEC


def _read_cache() -> pd.DataFrame | None:
    """Cached membership, or None when absent/unreadable."""
    path = _cache_path()
    if not path.exists():
        return None
    try:
        frame = pd.read_parquet(path)
    except Exception:
        logger.warning("concept_map: cache unreadable at %s", path, exc_info=True)
        return None
    missing = [c for c in COLUMNS if c not in frame.columns]
    if missing:
        # A cache written by an older shape is a cache miss, not a crash: the
        # caller still gets a working answer after a refetch.
        logger.warning("concept_map: cache missing columns %s; ignoring", missing)
        return None
    return frame


def _cache_age_seconds() -> float | None:
    path = _cache_path()
    if not path.exists():
        return None
    return time.time() - path.stat().st_mtime


def _is_fresh() -> bool:
    age = _cache_age_seconds()
    # Strict ``<`` so that a TTL of 0 means "always refetch" rather than "fresh
    # only in the instant the file was written". A just-written file reports an
    # age at or below zero depending on timestamp granularity, and a caller
    # setting TTL=0 is asking for a bypass, not a coin flip.
    return age is not None and age < _ttl_seconds()


def _explode_payload(payload: Any) -> tuple[pd.DataFrame, int]:
    """``[{symbol, name, concepts: [...]}]`` → long frame ``symbol/name/concept``.

    Returns ``(frame, dropped)`` where ``dropped`` counts rows skipped for a bad
    shape. Blank concepts and blank symbols are skipped too, and counted in the
    same tally: a caller reading the log should see one number for "this payload
    was not what we expected", not have to add up three counters.
    """
    if not isinstance(payload, list):
        logger.warning(
            "concept_map: payload is %s, not a list; treating as empty",
            type(payload).__name__,
        )
        return pd.DataFrame(columns=list(COLUMNS)), 0

    symbols: list[str] = []
    names: list[str] = []
    concepts: list[str] = []
    dropped = 0
    for row in payload:
        if not isinstance(row, dict):
            dropped += 1
            continue
        members = row.get("concepts")
        symbol = row.get("symbol")
        if not isinstance(members, list) or not isinstance(symbol, str) or not symbol:
            dropped += 1
            continue
        symbol = symbol.strip().upper()
        name = str(row.get("name") or "")
        kept = 0
        for concept in members:
            if not isinstance(concept, str):
                dropped += 1
                continue
            concept = concept.strip()
            if not concept:
                continue
            symbols.append(symbol)
            names.append(name)
            concepts.append(concept)
            kept += 1
        if kept == 0:
            dropped += 1

    frame = pd.DataFrame(
        {"symbol": symbols, "name": names, "concept": concepts}, columns=list(COLUMNS)
    )
    return frame, dropped


def _fetch_remote() -> pd.DataFrame:
    """Fetch + parse. Never raises; returns an empty frame on any failure."""
    try:
        import httpx

        response = httpx.get(
            CONCEPT_SOURCE_URL,
            timeout=_REQUEST_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
            trust_env=False,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        logger.warning(
            "concept_map: fetch from %s failed; falling back to cache "
            "(if any) — a missing concept map must not block a page",
            CONCEPT_SOURCE_URL,
            exc_info=True,
        )
        return pd.DataFrame(columns=list(COLUMNS))

    frame, dropped = _explode_payload(payload)
    if dropped:
        logger.warning(
            "concept_map: dropped %d malformed entries while parsing", dropped
        )
    if frame.empty:
        logger.warning("concept_map: endpoint returned no usable memberships")
    return frame


def _write_cache(frame: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path()
    tmp = path.with_suffix(".parquet.tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def fetch_concept_membership(force: bool = False) -> pd.DataFrame:
    """Long-form concept membership: one row per ``(symbol, concept)`` pair.

    Args:
        force: bypass the TTL and refetch. Callers that have just observed a
            stale map (a new concept missing, an old one prominent) use this;
            the endpoint has no version header, so freshness is only ever
            inferred from the cache age.

    Returns:
        ``symbol`` (suffixed, upper-case) / ``name`` / ``concept``. Empty on a
        cold cache *and* a failed fetch — never raises, because the concept
        blocks on the watch page degrade rather than blank the page.
    """
    if not force and _is_fresh():
        cached = _read_cache()
        if cached is not None:
            return cached

    frame = _fetch_remote()
    if not frame.empty:
        try:
            _write_cache(frame)
        except Exception:
            # A read-only deployment is still a working deployment: the map in
            # memory is good for this process even if it cannot be persisted.
            logger.warning("concept_map: could not persist cache", exc_info=True)
        return frame

    cached = _read_cache()
    if cached is not None:
        age = _cache_age_seconds()
        logger.warning(
            "concept_map: using stale cache (age %.1f h)", (age or 0.0) / 3600.0
        )
        return cached
    return pd.DataFrame(columns=list(COLUMNS))

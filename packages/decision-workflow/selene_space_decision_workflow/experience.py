"""Experience sedimentation — REVALIDATE verdict → recallable case (P3).

Turns a mission outcome (e.g. keltner_revalidate.py's outcome dict) into a
frozen case stored under ~/.selenespace/data/experiences/<symbol>_<interval>/
so the next diagnosis on the same symbol+interval can load it as prior
evidence. Zero LLM, zero external corpus — pure project assembly (see
PROVENANCE.md §8).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXPERIENCES_ROOT = Path.home() / ".selenespace" / "data" / "experiences"

_SAFE_SEG = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True)
class ExperienceCase:
    """One REVALIDATE outcome distilled to a recallable precedent."""

    id: str
    symbol: str
    interval: str
    verdict: str
    in_sample: dict[str, Any]
    oos: dict[str, Any]
    method: str
    provenance: str
    created_iso: str
    market_regime: str = ""


def digest_experience(outcome: dict[str, Any], *, created_iso: str) -> ExperienceCase:
    """Map a mission outcome dict to an ExperienceCase (no file IO).

    market_regime comes from the outcome verbatim — the sedimentation layer
    never derives a regime label itself (strategy returns are not a market
    state); callers that know the regime pass it, others leave "".
    """
    declared = outcome.get("declared_intent") or {}
    return ExperienceCase(
        id=f"exp-{_SAFE_SEG.sub('', (declared.get('symbol') or 'unknown'))[:24]}-"
        f"{_SAFE_SEG.sub('', (declared.get('interval') or 'unknown'))[:8]}-"
        f"{_SAFE_SEG.sub('', str(outcome.get('verdict') or 'unknown')).lower()[:16]}",
        symbol=str(declared.get("symbol") or "unknown"),
        interval=str(declared.get("interval") or "unknown"),
        verdict=str(outcome.get("verdict") or "unknown"),
        in_sample=dict(outcome.get("in_sample") or {}),
        oos=dict(outcome.get("oos") or {}),
        method=str(outcome.get("method") or ""),
        provenance="mission outcome (REVALIDATE IS 2/3 + OOS 1/3, declared config)",
        created_iso=created_iso,
        market_regime=str(outcome.get("market_regime") or ""),
    )


def _regime_dir(symbol: str, interval: str, root: Path = EXPERIENCES_ROOT) -> Path:
    """Group cases by declared intent: <root>/<symbol>_<interval>/."""
    seg = f"{_SAFE_SEG.sub('_', symbol)}_{_SAFE_SEG.sub('_', interval)}"
    return root / seg


def save_experience(case: ExperienceCase, *, root: Path = EXPERIENCES_ROOT) -> Path:
    """Persist one case; filename carries verdict + timestamp."""
    directory = _regime_dir(case.symbol, case.interval, root)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = _SAFE_SEG.sub("", case.created_iso.replace(":", "").replace("-", ""))
    path = directory / f"{case.verdict.lower()}-{stamp}.json"
    path.write_text(
        json.dumps(dataclass_dict(case), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_experiences(
    symbol: str,
    interval: str,
    *,
    limit: int = 5,
    root: Path = EXPERIENCES_ROOT,
    market_regime: str | None = None,
) -> list[ExperienceCase]:
    """Most recent N cases for this symbol+interval; missing dir → [].

    ``market_regime=None`` keeps every case (existing callers unchanged).
    A given value keeps exact-regime matches plus legacy cases whose regime
    is "" (pre-regime sedimentation) — filtering never drops precedent.
    """
    directory = _regime_dir(symbol, interval, root)
    if not directory.is_dir():
        return []
    # Deterministic recency: sort by created_iso (mtime ties when several
    # cases are saved within the same second, which made ordering flaky).
    candidates: list[ExperienceCase] = []
    for p in directory.glob("*.json"):
        try:
            case = ExperienceCase(**json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError):
            continue
        if (
            market_regime is not None
            and case.market_regime
            and case.market_regime != market_regime
        ):
            continue
        candidates.append(case)
    candidates.sort(key=lambda c: c.created_iso, reverse=True)
    return candidates[:limit]


def dataclass_dict(case: ExperienceCase) -> dict[str, Any]:
    """Explicit field order for on-disk JSON (stable diffs)."""
    return {
        "id": case.id,
        "symbol": case.symbol,
        "interval": case.interval,
        "verdict": case.verdict,
        "in_sample": case.in_sample,
        "oos": case.oos,
        "method": case.method,
        "provenance": case.provenance,
        "created_iso": case.created_iso,
        "market_regime": case.market_regime,
    }


__all__ = [
    "ExperienceCase",
    "EXPERIENCES_ROOT",
    "digest_experience",
    "load_experiences",
    "save_experience",
]

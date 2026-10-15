"""Serenity fundamental analysis endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/serenity", tags=["serenity"])

# Module-level singleton (initialized by main.py lifespan)
_serenity_workflow: Any = None
_serenity_fetcher: Any = None
_score_cache: dict[str, Any] = {}


def set_serenity_workflow(workflow: Any) -> None:
    global _serenity_workflow
    _serenity_workflow = workflow


def get_serenity_workflow() -> Any:
    return _serenity_workflow


def set_serenity_fetcher(fetcher: Any) -> None:
    global _serenity_fetcher
    _serenity_fetcher = fetcher


def get_serenity_fetcher() -> Any:
    return _serenity_fetcher


# Theme pool scanner singleton (initialized by main.py lifespan)
_pool_scanner: Any = None
# Latest SerenityPoolScan per theme, in-memory only (P3: no schema change).
_pool_cache: dict[str, Any] = {}


def set_serenity_pool_scanner(scanner: Any) -> None:
    global _pool_scanner
    _pool_scanner = scanner


def get_serenity_pool_scanner() -> Any:
    return _pool_scanner


# Live SerenityScoreCache (wired by main.py lifespan) so /pool/scan can push
# a fresh scan into the theme-pool rank gate (B1). None until the strategy
# runner wiring runs; None means the gate is simply not syncable.
_serenity_cache: Any = None


def set_serenity_cache(cache: Any) -> None:
    global _serenity_cache
    _serenity_cache = cache


def get_serenity_cache() -> Any:
    return _serenity_cache


def _sync_pool_filter_to_cache() -> None:
    """Push the freshest pool scans into the live cache's pool rank gate.

    Re-feeds set_pool_filter with the current in-memory scan map so a fresh
    /pool/scan immediately updates the gate. No-op when the gate is off or the
    cache is absent.
    """
    cache = _serenity_cache
    if cache is None or cache._pool_mode == "off":
        return
    cache.set_pool_filter(
        dict(_pool_cache),
        mode=cache._pool_mode,
        top_n=cache._pool_top_n,
    )


def _py_scalar(value: Any) -> Any:
    """Convert pandas/numpy scalars from a DataFrame row to native Python."""
    return value.item() if hasattr(value, "item") else value


def _report_from_score_row(row: dict[str, Any]) -> Any:
    """Synthesize a lightweight research report from a persisted score row.

    Used to rehydrate _score_cache from score_db after a restart (P2-3). The
    DB stores scores, not prose — narrative/lists are neutral placeholders.
    """
    from selene_space_alpha.serenity.models import SerenityResearchReport, SerenityScore

    score = SerenityScore(**{k: _py_scalar(v) for k, v in row.items()})
    ns = score.normalized_score
    direction = 1 if ns > 0.6 else -1 if ns < 0.4 else 0
    return SerenityResearchReport(
        symbol=score.symbol,
        tier1=score,
        tier2=None,
        narrative=f"{score.symbol}: {score.tier} fundamental profile (score {ns:.2f})",
        system_change="",
        scarce_constraints=[],
        blind_spots=[],
        disproof_conditions=[],
        signal_direction=direction,
        signal_strength=abs(ns - 0.5) * 2.0,
        elapsed_seconds=0.0,
    )


def bootstrap_score_cache() -> int:
    """Rehydrate _score_cache from the latest persisted scores in score_db.

    /scores only reads this in-memory cache, so after a restart it came back
    empty even though /history (SQLite-backed) still had data. Load the latest
    row per symbol so the two endpoints stay consistent (P2-3).
    """
    global _score_cache
    from app.core.dependencies import get_serenity_score_db

    score_db = get_serenity_score_db()
    if score_db is None:
        return 0
    df = score_db.get_all_latest()
    loaded = 0
    for _, row in df.iterrows():
        report = _report_from_score_row(row.to_dict())
        _score_cache[report.symbol] = report
        loaded += 1
    if loaded:
        logger.info("Bootstrapped %d Serenity scores from score_db", loaded)
    return loaded


def bootstrap_pool_cache() -> int:
    """Rehydrate _pool_cache from the latest persisted scans in pool_db.

    /pool/{theme} only reads this in-memory cache, so after a restart it came
    back "no scan yet" even though the scans were persisted. Load the latest
    scan per theme so the endpoint survives restart (F1).
    """
    global _pool_cache
    from app.core.dependencies import get_serenity_pool_db

    pool_db = get_serenity_pool_db()
    if pool_db is None:
        return 0
    scans = pool_db.get_all_latest()
    for scan in scans:
        _pool_cache[scan.theme] = scan
    if scans:
        logger.info("Bootstrapped %d Serenity pool scans from pool_db", len(scans))
    return len(scans)


def _persist_score(report: Any) -> None:
    """Persist SerenityScore to SQLite after a scan."""
    try:
        from app.core.dependencies import get_serenity_score_db
        score_db = get_serenity_score_db()
        if score_db is None:
            return
        score_db.put_score(report.tier1)
        if report.tier2 is not None:
            score_db.put_score(report.tier2)
    except Exception:
        logger.debug("Serenity score persist failed", exc_info=True)


@router.get("/scan/{symbol}")
async def quick_scan(
    request: Request,
    symbol: str,
) -> APIResponse[dict[str, Any]]:
    """Quick-scan a symbol with Serenity methodology (Tier 1)."""
    if _serenity_workflow is None:
        return APIResponse(
            data={"error": "Serenity service not available"},
            meta=build_response_meta(request),
            error=None,
        )

    report = await _serenity_workflow.quick_scan(symbol)
    _score_cache[symbol.upper()] = report
    _persist_score(report)

    return APIResponse(
        data=_report_to_dict(report),
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/deep/{symbol}")
async def deep_verify(
    request: Request,
    symbol: str,
) -> APIResponse[dict[str, Any]]:
    """Deep verification for a symbol (Tier 1 + Tier 2)."""
    if _serenity_workflow is None:
        return APIResponse(
            data={"error": "Serenity service not available"},
            meta=build_response_meta(request),
            error=None,
        )

    report = await _serenity_workflow.scan_and_verify(symbol)
    _score_cache[symbol.upper()] = report
    _persist_score(report)

    return APIResponse(
        data=_report_to_dict(report),
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/score/{symbol}")
async def get_latest_score(
    request: Request,
    symbol: str,
) -> APIResponse[dict[str, Any]]:
    """Get the latest cached Serenity score for a symbol."""
    sym = symbol.upper()
    if sym in _score_cache:
        return APIResponse(
            data=_report_to_dict(_score_cache[sym]),
            meta=build_response_meta(request),
            error=None,
        )

    # No cached score — run a quick scan
    if _serenity_workflow is not None:
        report = await _serenity_workflow.quick_scan(symbol)
        _score_cache[sym] = report
        return APIResponse(
            data=_report_to_dict(report),
            meta=build_response_meta(request),
            error=None,
        )

    return APIResponse(
        data={"symbol": sym, "score": None, "message": "No score available"},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/scores")
async def list_scores(
    request: Request,
) -> APIResponse[dict[str, Any]]:
    """List latest Serenity scores for all tracked symbols."""
    results = {}
    for sym, report in _score_cache.items():
        results[sym] = {
            "score": report.tier1.normalized_score,
            "tier": report.tier1.tier,
            "direction": report.signal_direction,
            "narrative": report.narrative[:100],
        }
    return APIResponse(
        data={"scores": results, "count": len(results)},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/factors")
async def list_serenity_factors(
    request: Request,
) -> APIResponse[dict[str, Any]]:
    """List available Serenity factors and their descriptions."""
    from selene_space_alpha.serenity.signal_bridge import SERENITY_FACTORS

    factors = {name: desc for name, (_, desc) in SERENITY_FACTORS.items()}
    return APIResponse(
        data={"factors": factors, "count": len(factors)},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/history/{symbol}")
async def get_score_history(
    request: Request,
    symbol: str,
    limit: int = Query(100, ge=1, le=1000),
) -> APIResponse[dict[str, Any]]:
    """Get historical Serenity scores for a symbol."""
    from app.core.dependencies import get_serenity_score_db

    score_db = get_serenity_score_db()
    if score_db is None:
        return APIResponse(
            data={"symbol": symbol.upper(), "history": [], "count": 0},
            meta=build_response_meta(request),
            error=None,
        )

    df = score_db.get_history(symbol.upper(), limit=limit)
    history = df.to_dict(orient="records") if not df.empty else []

    return APIResponse(
        data={"symbol": symbol.upper(), "history": history, "count": len(history)},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/pool/scan")
async def pool_scan(
    request: Request,
    theme: str,
    refresh: bool = False,
    top_n: int = Query(7, ge=1, le=50),
) -> APIResponse[dict[str, Any]]:
    """Scan a theme candidate pool (build >=20 universe, rank scarce-layer-first).

    Cached per theme; pass refresh=true to force a rescan. The scan is
    sequential (~30s for 20+ candidates) so results are served from cache on
    the hot path.
    """
    global _pool_cache
    if not refresh and theme in _pool_cache:
        return APIResponse(
            data=_pool_scan_to_dict(_pool_cache[theme]),
            meta=build_response_meta(request),
            error=None,
        )

    if _pool_scanner is None:
        return APIResponse(
            data={"error": "Serenity pool service not available"},
            meta=build_response_meta(request),
            error=None,
        )

    try:
        scan = await _pool_scanner.scan(theme, top_n=top_n)
    except ValueError as e:
        return APIResponse(
            data={"error": str(e)},
            meta=build_response_meta(request),
            error=None,
        )
    _pool_cache[theme] = scan
    _sync_pool_filter_to_cache()
    return APIResponse(
        data=_pool_scan_to_dict(scan),
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/pool/{theme}")
async def pool_latest(
    request: Request,
    theme: str,
) -> APIResponse[dict[str, Any]]:
    """Get the latest cached pool scan for a theme.

    Cache-first; on a miss, fall back to the latest persisted scan in pool_db
    (F1) so the endpoint survives a restart even before the first /pool/scan.
    """
    global _pool_cache
    scan = _pool_cache.get(theme)
    if scan is None:
        from app.core.dependencies import get_serenity_pool_db

        pool_db = get_serenity_pool_db()
        if pool_db is not None:
            try:
                scan = pool_db.get_latest(theme)
            except Exception:
                logger.debug("Serenity pool DB read failed", exc_info=True)
            if scan is not None:
                _pool_cache[theme] = scan
    if scan is None:
        return APIResponse(
            data={"theme": theme, "message": "no scan yet"},
            meta=build_response_meta(request),
            error=None,
        )
    return APIResponse(
        data=_pool_scan_to_dict(scan),
        meta=build_response_meta(request),
        error=None,
    )


def _pool_scan_to_dict(scan: Any) -> dict[str, Any]:
    """Convert SerenityPoolScan to a JSON-serializable dict."""
    return {
        "theme": scan.theme,
        "theme_name": scan.theme_name,
        "scanned_at": scan.scanned_at,
        "universe_size": scan.universe_size,
        "weak_evidence_count": scan.weak_evidence_count,
        "layer_summary": [
            {
                "layer": ls.layer,
                "count": ls.count,
                "top_scarce_symbols": list(ls.top_scarce_symbols),
            }
            for ls in scan.layer_summary
        ],
        "top_picks": list(scan.top_picks),
        "ranked": [
            {
                "symbol": c.symbol,
                "name": c.name,
                "category": c.category,
                "chains": list(c.chains),
                "normalized_score": round(c.normalized_score, 4),
                "tier": c.tier,
                "scarce_layer_score": round(c.scarce_layer_score, 4),
                "evidence_strength": round(c.evidence_strength, 3),
                "evidence_label": c.evidence_label,
                "rationale": c.rationale,
                "top_penalty": c.top_penalty,
                "disproof": list(c.disproof),
            }
            for c in scan.ranked
        ],
    }


def _report_to_dict(report: Any) -> dict[str, Any]:
    """Convert SerenityResearchReport to a JSON-serializable dict."""
    result: dict[str, Any] = {
        "symbol": report.symbol,
        "tier1": _score_to_dict(report.tier1),
        "tier2": _score_to_dict(report.tier2) if report.tier2 else None,
        "narrative": report.narrative,
        "system_change": report.system_change,
        "scarce_constraints": report.scarce_constraints,
        "blind_spots": report.blind_spots,
        "disproof_conditions": report.disproof_conditions,
        "signal_direction": report.signal_direction,
        "signal_strength": report.signal_strength,
        "elapsed_seconds": round(report.elapsed_seconds, 2),
    }
    return result


def _score_to_dict(score: Any) -> dict[str, Any]:
    """Convert SerenityScore to a JSON-serializable dict."""
    return {
        "normalized_score": round(score.normalized_score, 4),
        "tier": score.tier,
        "raw_score": round(score.raw_score, 4),
        "factors": {
            "consensus_scarcity": round(score.consensus_scarcity, 3),
            "liquidity_scarcity": round(score.liquidity_scarcity, 3),
            "trust_scarcity": round(score.trust_scarcity, 3),
            "replication_difficulty": round(score.replication_difficulty, 3),
            "certification_progress": round(score.certification_progress, 3),
            "prepayment_signal": round(score.prepayment_signal, 3),
            "decentralization": round(score.decentralization, 3),
            "evidence_strength": round(score.evidence_strength, 3),
        },
        "penalties": {
            "fork_risk": round(score.penalty_fork_risk, 3),
            "tvl_concentration": round(score.penalty_tvl_concentration, 3),
            "dev_exodus": round(score.penalty_dev_exodus, 3),
            "revenue_decline": round(score.penalty_revenue_decline, 3),
            "oi_divergence": round(score.penalty_oi_divergence, 3),
            "whale_exodus": round(score.penalty_whale_exodus, 3),
            "governance_risk": round(score.penalty_governance_risk, 3),
            "liquidity_trap": round(score.penalty_liquidity_trap, 3),
        },
        "price_change_7d": getattr(score, "price_change_7d", None),
        "volume_change_7d": getattr(score, "volume_change_7d", None),
        "funding_rate": getattr(score, "funding_rate", None),
        "oi_change": getattr(score, "oi_change", None),
        "ls_ratio": getattr(score, "ls_ratio", None),
    }

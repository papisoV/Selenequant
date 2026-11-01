"""Vibe Shadow Account Profile API (Phase 3.6).

FastAPI router exposing the shadow profile extraction, engine generation,
and backtest endpoints. All endpoints are env-gated via VIBE_SHADOW_ENABLED.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Environment gate
# ---------------------------------------------------------------------------


def _shadow_enabled() -> bool:
    """Return True if VIBE_SHADOW_ENABLED is 1/true/yes (case-insensitive)."""
    return os.environ.get("VIBE_SHADOW_ENABLED", "").lower() in ("1", "true", "yes")


def _gate() -> None:
    """Raise HTTP 404 if shadow profile is not enabled."""
    if not _shadow_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


# ---------------------------------------------------------------------------
# POST /shadow-profile/extract
# ---------------------------------------------------------------------------


@router.post("/shadow-profile/extract")
async def shadow_profile_extract(
    file: UploadFile = File(...),
    min_support: int = Form(3),
    max_rules: int = Form(5),
) -> dict:
    """Extract a ShadowProfile from an uploaded broker trade journal.

    Accepts a CSV UploadFile and form params min_support/max_rules.
    Returns JSON with shadow_id, rules, and meta.
    """
    _gate()

    try:
        from subsystems.vibe.bridge.shadow_account.preset import (
            new_vibe_shadow_account,
            vibe_shadow_account_preset,
        )
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "vibe shadow_account dependencies not installed",
                "detail": str(e),
            },
        )

    try:
        # Save uploaded file to a temp file
        suffix = Path(file.filename or "journal.csv").suffix or ".csv"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(await file.read())
            temp_path = tmp.name

        preset_descriptor = vibe_shadow_account_preset()
        account = new_vibe_shadow_account()
        profile = account.extract_profile(
            temp_path,
            min_support=min_support,
            max_rules=max_rules,
        )

        # Persist so shadow_id is retrievable
        saved_path = account.save_profile(profile)

        return {
            "shadow_id": profile.shadow_id,
            "rules": profile.rules if hasattr(profile, "rules") else [],
            "meta": profile.meta if hasattr(profile, "meta") else {},
        }
    except Exception as e:
        logger.warning("Shadow profile extract failed", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": str(e)})


# ---------------------------------------------------------------------------
# POST /shadow-profile/{shadow_id}/render-engine
# ---------------------------------------------------------------------------


@router.post("/shadow-profile/{shadow_id}/render-engine")
async def shadow_profile_render_engine(shadow_id: str) -> dict:
    """Render a standalone Python SignalEngine from a persisted shadow profile."""
    _gate()

    try:
        from subsystems.vibe.bridge.shadow_account.preset import (
            new_vibe_shadow_account,
        )
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "vibe shadow_account dependencies not installed",
                "detail": str(e),
            },
        )

    try:
        account = new_vibe_shadow_account()
        profile = account.load_profile(shadow_id)
        source = account.render_signal_engine(profile)
        return {"source": source}
    except Exception as e:
        logger.warning("Shadow profile render-engine failed", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": str(e)})


# ---------------------------------------------------------------------------
# POST /shadow-profile/{shadow_id}/backtest
# ---------------------------------------------------------------------------


@router.post("/shadow-profile/{shadow_id}/backtest")
async def shadow_profile_backtest(shadow_id: str, body: dict) -> dict:
    """Run a multi-market backtest against a persisted shadow profile.

    Body: {window_start, window_end, markets?, per_market_count?, source?}
    """
    _gate()

    try:
        from subsystems.vibe.bridge.shadow_account.preset import (
            new_vibe_shadow_account,
        )
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "vibe shadow_account dependencies not installed",
                "detail": str(e),
            },
        )

    try:
        account = new_vibe_shadow_account()
        profile = account.load_profile(shadow_id)
        result = account.run_backtest(
            profile,
            window_start=body["window_start"],
            window_end=body["window_end"],
            markets=body.get("markets"),
            per_market_count=body.get("per_market_count", 5),
            source=body.get("source", "auto"),
        )
        # ShadowBacktestResult has __dict__ or dict-like conversion
        if hasattr(result, "_asdict"):
            return result._asdict()
        if hasattr(result, "__dict__"):
            return result.__dict__
        return result
    except KeyError as e:
        raise HTTPException(
            status_code=422,
            detail={"error": f"Missing required field: {e.args[0]}"},
        )
    except Exception as e:
        logger.warning("Shadow profile backtest failed", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": str(e)})

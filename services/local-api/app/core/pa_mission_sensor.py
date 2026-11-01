"""PA heartbeat → research_missions Mission sensor (T4).

``scripts/run_pa_diagnosis.py`` already bridges a manual diagnosis into a
research_missions row (create market subject + ``MissionPersistence`` writing
activity/context). This module is the unattended serialization of the same
pipeline: the heartbeat (§2.3 ``_diagnose_one``) projects each automatic
diagnosis onto the Mission surface the same way, but lighter — the full record
stays in v009 (GET /records/{input_id}), the mission carries the pointer +
the diagnosis log, and it is settled straight to ``needs_input`` so the
existing four-action decision machine applies (approve/more_evidence/
redirect/park via POST /research/missions/{id}/decision).

Zero migration (v015 research_missions + mission_activity tables exist), zero
new endpoints, zero LLM, zero budget spend.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "packages" / "storage"))
from selene_space_storage.repositories.research_missions import (  # noqa: E402
    ResearchMissionRepository,
)

_QUESTION_TEMPLATE = "{symbol} PA 心跳诊断待决策"


def _question(symbol: str) -> str:
    return _QUESTION_TEMPLATE.format(symbol=symbol)


def _repo() -> ResearchMissionRepository:
    from app.core.dependencies import get_db_manager

    return ResearchMissionRepository(get_db_manager())


def build_diagnosis(
    *,
    input_id: str,
    interval: str | None = None,
    market_type: str | None = None,
    trading_stance: str | None = None,
) -> dict[str, Any]:
    """Pointer payload — the full diagnosis record lives in v009 by ``input_id``."""
    return {
        "record_id": input_id,
        "diagnosed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "interval": interval,
        "market_type": market_type,
        "trading_stance": trading_stance,
    }


async def sync_diagnosis_to_mission(
    *,
    symbol: str,
    diagnosis: dict[str, Any],
    repo: ResearchMissionRepository | None = None,
) -> str | None:
    """Project one heartbeat diagnosis onto the Mission surface.

    ``find_open_by_question`` keeps the Investigate uniqueness invariant: an
    already-open mission (same market subject + question, regardless of
    phase) accumulates the diagnosis log in its context and records the touch
    as a ``pa_diagnosis_record`` activity. Otherwise a new mission opens and
    is settled to ``needs_input`` so the four-action machine applies.

    Returns the mission id; ``None`` when the repo path is unavailable (the
    caller treats the projection as best-effort and never fatal).
    """
    if repo is None:
        repo = _repo()
    question = _question(symbol)
    open_mission = await repo.find_open_by_question("market", symbol, question)
    mission_id: str

    if open_mission is not None:
        mission_id = open_mission["id"]
        context = open_mission.get("context") or {}
        diagnoses = list(context.get("diagnoses") or []) + [diagnosis]
        await repo.update_context(
            mission_id,
            {"diagnoses": diagnoses, "latest_diagnosis": diagnosis},
        )
    else:
        mission_id = await repo.create(
            title=f"{symbol} PA 心跳诊断",
            question=question,
            subject_type="market",
            subject_id=symbol,
            created_by="selene",
            context={
                "pipeline": "pa-heartbeat",
                "interval": diagnosis.get("interval"),
                "market_type": diagnosis.get("market_type"),
                "trading_stance": diagnosis.get("trading_stance"),
                "diagnoses": [diagnosis],
                "latest_diagnosis": diagnosis,
            },
        )
        await repo.transition(
            mission_id,
            "needs_input",
            actor="selene",
            note="心跳诊断完成,待负责人四动作决策",
        )

    await repo.append_activity(
        mission_id,
        actor="selene",
        kind="pa_diagnosis_record",
        detail={
            "input_id": diagnosis["record_id"],
            "interval": diagnosis.get("interval"),
            "diagnosed_at": diagnosis.get("diagnosed_at"),
        },
    )
    return mission_id


# How many of a symbol's missions to scan for the one owning a record.
# ``list_missions`` orders by updated_at DESC and appending bumps updated_at,
# so the owner is normally the first row.
# ponytail: fixed window; index the lookup by record_id if one symbol ever
# legitimately exceeds this many missions.
_MISSION_SCAN_LIMIT = 20


def _mission_owning(
    missions: list[dict[str, Any]], record_id: str
) -> dict[str, Any] | None:
    """The newest mission whose diagnosis log references ``record_id``.

    ``latest_diagnosis`` is matched across all missions before the full log,
    so a record still current on one mission is not claimed by an older
    mission that merely carries it as a superseded entry.
    """
    for mission in missions:
        context = mission.get("context") or {}
        if (context.get("latest_diagnosis") or {}).get("record_id") == record_id:
            return mission
    for mission in missions:
        context = mission.get("context") or {}
        if any(
            (d or {}).get("record_id") == record_id
            for d in (context.get("diagnoses") or [])
        ):
            return mission
    return None


async def sync_followup_to_mission(
    *,
    symbol: str,
    record_id: str,
    question: str,
    used_live: bool,
    repo: ResearchMissionRepository | None = None,
) -> str | None:
    """Project one anchored follow-up onto the Mission surface.

    A follow-up interrogates a decision that already exists, so this never
    opens a mission: when no mission carries ``record_id`` — a record from the
    manual ``/analyze-trading`` path has no heartbeat projection — the entry is
    skipped and ``None`` is returned.

    ``actor`` is ``user``, not ``selene``: unlike the heartbeat's automatic
    diagnoses, a follow-up question came from a person.
    """
    if repo is None:
        repo = _repo()
    missions = await repo.list_missions(
        subject_type="market", subject_id=symbol, limit=_MISSION_SCAN_LIMIT
    )
    mission = _mission_owning(missions, record_id)
    if mission is None:
        return None
    mission_id: str = mission["id"]
    await repo.append_activity(
        mission_id,
        actor="user",
        kind="pa_followup",
        detail={
            "record_id": record_id,
            "question": question,
            "used_live": used_live,
        },
    )
    return mission_id

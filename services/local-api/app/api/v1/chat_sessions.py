"""Chat session persistence API.

Provides CRUD endpoints for conversation sessions and messages,
enabling the frontend to persist, list, switch, and resume
AI chat conversations across page refreshes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.dependencies import (
    AuthenticatedUser,
    get_chat_session_repo,
    optional_user,
)

router = APIRouter(prefix="/chat", tags=["chat"])

MAX_SESSIONS_PER_USER = 500

# 单用户（未启用多用户鉴权）时的哨兵 id
_LOCAL_USER = "local"


class CreateSessionBody(BaseModel):
    strategy_id: str | None = None
    title: str = ""


class UpdateSessionBody(BaseModel):
    title: str


class SaveMessagesBody(BaseModel):
    messages: list[dict[str, Any]] = Field(default_factory=list)


def _require_repo(repo: Any = Depends(get_chat_session_repo)) -> Any:
    if repo is None:
        raise HTTPException(503, "Chat persistence not available")
    return repo


def _assert_owns(
    user: AuthenticatedUser, session: dict[str, Any] | None
) -> dict[str, Any]:
    """Return the session if ``user`` may touch it, else raise 404/403.

    Single-user mode (``user.id == "local"``) bypasses the ownership check;
    otherwise the row must carry a matching ``user_id``.  A session that is
    absent or ownerless is not accessible to a named user.
    """
    if session is None:
        raise HTTPException(404, "Session not found")
    if user.id == _LOCAL_USER:
        return session

    owner = session.get("user_id")
    # 允许：会话归当前用户，或归本机哨兵（与旧行为一致）。
    if not owner or (owner != user.id and owner != _LOCAL_USER):
        raise HTTPException(403, "Access denied")
    return session


@router.post("/sessions")
async def create_session(
    body: CreateSessionBody,
    repo: Any = Depends(_require_repo),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    existing = await repo.list_sessions(
        user_id=user.id, limit=1, offset=MAX_SESSIONS_PER_USER
    )
    if existing:
        raise HTTPException(429, f"Session limit reached ({MAX_SESSIONS_PER_USER})")
    created: dict[str, Any] = await repo.create_session(
        strategy_id=body.strategy_id,
        title=body.title,
        user_id=user.id,
    )
    return created


@router.get("/sessions")
async def list_sessions(
    strategy_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    repo: Any = Depends(_require_repo),
    user: AuthenticatedUser = Depends(optional_user),
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = await repo.list_sessions(
        strategy_id=strategy_id,
        limit=limit,
        offset=offset,
        user_id=user.id,
    )
    return rows


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    repo: Any = Depends(_require_repo),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    return _assert_owns(user, await repo.get_session(session_id))


@router.patch("/sessions/{session_id}")
async def update_session(
    session_id: str,
    body: UpdateSessionBody,
    repo: Any = Depends(_require_repo),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, str]:
    _assert_owns(user, await repo.get_session(session_id))
    await repo.update_session_title(session_id, body.title)
    return {"status": "ok"}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    repo: Any = Depends(_require_repo),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, str]:
    _assert_owns(user, await repo.get_session(session_id))
    await repo.delete_session(session_id)
    return {"status": "ok"}


@router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    limit: int = 200,
    offset: int = 0,
    repo: Any = Depends(_require_repo),
    user: AuthenticatedUser = Depends(optional_user),
) -> list[dict[str, Any]]:
    session = await repo.get_session(session_id)
    # 读消息：会话不存在时也允许（返回空列表），仅当存在且不属于当前用户才拦。
    if session is not None:
        _assert_owns(user, session)
    messages: list[dict[str, Any]] = await repo.get_messages(
        session_id, limit=limit, offset=offset
    )
    return messages


@router.put("/sessions/{session_id}/messages")
async def save_messages(
    session_id: str,
    body: SaveMessagesBody,
    repo: Any = Depends(_require_repo),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, str]:
    """Bulk-save messages for a session (replaces existing messages)."""
    existing = await repo.get_session(session_id)
    if existing is None:
        await repo.create_session(session_id=session_id, user_id=user.id)
    else:
        _assert_owns(user, existing)
    await repo.save_messages_bulk(session_id, body.messages)
    return {"status": "ok"}

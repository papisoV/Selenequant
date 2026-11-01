"""Conversation persistence: sessions and their message streams.

Sessions are keyed by client-supplied ids and scoped to a ``user_id``
(the v006 column).  Messages are appended per session in bulk and read
back oldest-first with limit/offset paging.
"""

from __future__ import annotations

import json
import uuid
from typing import Any


class ChatSessionRepository:
    """Facade over the ``chat_sessions`` / ``chat_messages`` tables."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def create_session(
        self,
        session_id: str | None = None,
        *,
        user_id: str = "local",
        strategy_id: str | None = None,
        title: str = "",
    ) -> dict[str, Any]:
        sid = session_id or f"chat-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            """
            INSERT INTO chat_sessions (id, strategy_id, title, user_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET updated_at = datetime('now')
            """,
            (sid, strategy_id, title, user_id),
        )
        session = await self.get_session(sid)
        assert session is not None
        return session

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
        )
        return dict(rows[0]) if rows else None

    async def list_sessions(
        self,
        *,
        user_id: str | None = None,
        strategy_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if strategy_id is not None:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._db.execute(
            "SELECT * FROM chat_sessions"
            + where
            + " ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (*params, int(limit), int(offset)),
        )
        return [dict(r) for r in rows]

    async def update_session_title(self, session_id: str, title: str) -> None:
        await self._db.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = datetime('now') WHERE id = ?",
            (title, session_id),
        )

    async def delete_session(self, session_id: str) -> None:
        await self._db.execute(
            "DELETE FROM chat_messages WHERE session_id = ?", (session_id,)
        )
        await self._db.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))

    async def save_messages_bulk(
        self, session_id: str, messages: list[dict[str, Any]]
    ) -> int:
        saved = 0
        for msg in messages:
            extra = {
                k: v
                for k, v in msg.items()
                if k not in ("id", "role", "content", "created_at")
            }
            await self._db.execute(
                """
                INSERT INTO chat_messages (id, session_id, role, content, extra_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    msg.get("id") or uuid.uuid4().hex,
                    session_id,
                    msg.get("role", "user"),
                    msg.get("content", ""),
                    json.dumps(extra, ensure_ascii=False),
                ),
            )
            saved += 1
        await self._db.execute(
            "UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?",
            (session_id,),
        )
        return saved

    async def get_messages(
        self,
        session_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = await self._db.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at, rowid LIMIT ? OFFSET ?",
            (session_id, int(limit), int(offset)),
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["extra"] = json.loads(item.pop("extra_json"))
            out.append(item)
        return out

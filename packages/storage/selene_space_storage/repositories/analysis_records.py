"""Two-stage market-analysis audit records.

Each record is a free-form dict (klines, prompts, raw LLM output, parsed
stages, token usage) stored wholesale as JSON.  A few columns used for
listing/filtering are lifted out of the payload at write time:
``symbol``/``interval`` from ``input_data``, ``entry_gate`` from
``stage1``, ``decision`` from ``stage2``, and identity fields from
``meta``.

Failure triage: records whose payload carries an ``exception`` key can be
listed separately via :meth:`AnalysisRecordRepository.list_failures`, with
the full payload retained for tuning input.
"""

from __future__ import annotations

import builtins
import json
import uuid
from typing import Any


def _extract_index_columns(record: dict[str, Any]) -> dict[str, Any]:
    input_data = record.get("input_data") or {}
    stage1 = record.get("stage1") or {}
    stage2 = record.get("stage2") or {}
    meta = record.get("meta") or {}
    return {
        "symbol": input_data.get("symbol"),
        "interval": input_data.get("interval"),
        "entry_gate": stage1.get("entry_gate"),
        "decision": stage2.get("decision"),
        "input_id": meta.get("input_id"),
        "session_id": meta.get("session_id"),
    }


class AnalysisRecordRepository:
    """Facade over the ``analysis_records`` table."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def save(self, record: dict[str, Any]) -> str:
        record_id = f"ar-{uuid.uuid4().hex[:12]}"
        cols = _extract_index_columns(record)
        await self._db.execute(
            """
            INSERT INTO analysis_records (
                id, timestamp, input_id, session_id, symbol, interval,
                entry_gate, decision, record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                record.get("meta", {}).get("timestamp") or "",
                cols["input_id"] or "",
                cols["session_id"] or "",
                cols["symbol"],
                cols["interval"],
                cols["entry_gate"],
                cols["decision"],
                json.dumps(record, ensure_ascii=False),
            ),
        )
        return record_id

    async def get(self, record_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT record_json FROM analysis_records WHERE id = ?",
            (record_id,),
        )
        if not rows:
            return None
        payload: dict[str, Any] = json.loads(rows[0]["record_json"])
        return payload

    async def get_by_input_id(self, input_id: str) -> dict[str, Any] | None:
        """Newest record for a caller-supplied ``input_id``.

        The service keys its read-side cache by input_id (not the generated
        ``ar-*`` row id), so a post-restart lookup has no row id to use —
        it resolves the latest row written for that input_id instead.
        """
        rows = await self._db.execute(
            "SELECT record_json FROM analysis_records"
            " WHERE input_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (input_id,),
        )
        if not rows:
            return None
        payload: dict[str, Any] = json.loads(rows[0]["record_json"])
        return payload

    async def append_payload(
        self, input_id: str, key: str, value: dict[str, Any]
    ) -> bool:
        """Attach a top-level key (backtest/outcome) to the latest record.

        Read-modify-write guarded by an optimistic lock: the UPDATE's WHERE
        clause carries the exact ``record_json`` we read back, so a concurrent
        writer that landed first yields rowcount 0 → False, no retry (the
        heartbeat re-attaches idempotently next round). Uses the raw
        connection scope for ``cursor.rowcount`` — ``execute()`` drops the
        affected-row count for UPDATE statements.
        """
        rows = await self._db.execute(
            "SELECT id, record_json FROM analysis_records"
            " WHERE input_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (input_id,),
        )
        if not rows:
            return False
        row_id: str = rows[0]["id"]
        original_json: str = rows[0]["record_json"]
        payload: dict[str, Any] = json.loads(original_json)
        payload[key] = value
        updated_json = json.dumps(payload, ensure_ascii=False)
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "UPDATE analysis_records SET record_json = ?"
                " WHERE id = ? AND record_json = ?",
                (updated_json, row_id, original_json),
            )
            try:
                rowcount = int(cursor.rowcount)
            finally:
                await cursor.close()
        return rowcount > 0

    async def append_followup(
        self, input_id: str, entry: dict[str, Any], *, attempts: int = 3
    ) -> list[dict[str, Any]] | None:
        """Append one entry to the record's ``followups`` list.

        Not expressible as :meth:`append_payload`: that method has set
        semantics (``payload[key] = value``), so passing the new list there
        would replace the follower history instead of extending it.

        The optimistic lock can lose to a concurrent writer, so the
        read-modify-write is retried a bounded number of times. Failing
        closed with no retry is right for the heartbeat's idempotent
        re-attachment (next round rewrites it) but wrong here — the caller
        asked a question and would watch it silently vanish.

        Returns the new list on success, ``None`` when no record exists for
        ``input_id`` or every attempt lost its race.
        """
        for _ in range(attempts):
            rows = await self._db.execute(
                "SELECT id, record_json FROM analysis_records"
                " WHERE input_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (input_id,),
            )
            if not rows:
                return None
            row_id: str = rows[0]["id"]
            original_json: str = rows[0]["record_json"]
            payload: dict[str, Any] = json.loads(original_json)
            existing = payload.get("followups")
            followups: list[dict[str, Any]] = (
                list(existing) if isinstance(existing, list) else []
            )
            followups.append(entry)
            payload["followups"] = followups
            updated_json = json.dumps(payload, ensure_ascii=False)
            async with self._db.connection() as conn:
                cursor = await conn.execute(
                    "UPDATE analysis_records SET record_json = ?"
                    " WHERE id = ? AND record_json = ?",
                    (updated_json, row_id, original_json),
                )
                try:
                    rowcount = int(cursor.rowcount)
                finally:
                    await cursor.close()
            if rowcount > 0:
                return followups
        return None

    async def list(
        self,
        symbol: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        paging = (
            f" LIMIT {int(limit)} OFFSET {int(offset or 0)}"
            if limit is not None
            else ""
        )
        if limit is None and offset:
            paging = f" LIMIT -1 OFFSET {int(offset)}"
        rows = await self._db.execute(
            "SELECT * FROM analysis_records"
            + where
            + " ORDER BY created_at DESC, id"
            + paging,
            tuple(params),
        )
        return [dict(r) for r in rows]

    async def list_records(
        self,
        symbol: str | None = None,
        limit: int | None = 50,
        offset: int = 0,
    ) -> builtins.list[dict[str, Any]]:
        """Full deserialized payloads (newest first) for replay / priors."""
        clauses: list[str] = []
        params: list[Any] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        paging = (
            f" LIMIT {int(limit)} OFFSET {int(offset)}"
            if limit is not None and (limit or offset)
            else (f" LIMIT -1 OFFSET {int(offset)}" if offset else "")
        )
        rows = await self._db.execute(
            "SELECT id, record_json FROM analysis_records"
            + where
            + " ORDER BY created_at DESC, id"
            + paging,
            tuple(params),
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["record_json"])
            meta = payload.setdefault("meta", {})
            meta.setdefault("record_id", row["id"])
            out.append(payload)
        return out

    async def count(self) -> int:
        rows = await self._db.execute("SELECT COUNT(*) AS n FROM analysis_records")
        return int(rows[0]["n"]) if rows else 0

    async def list_failures(
        self,
        limit: int | None = None,
        offset: int | None = None,
    ) -> builtins.list[dict[str, Any]]:
        paging = (
            f" LIMIT {int(limit)} OFFSET {int(offset or 0)}"
            if limit is not None
            else ""
        )
        if limit is None and offset:
            paging = f" LIMIT -1 OFFSET {int(offset)}"
        rows = await self._db.execute(
            """
            SELECT * FROM analysis_records
            WHERE record_json LIKE '%"exception"%'
            ORDER BY created_at DESC, id
            """
            + paging,
            (),
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["record_json"])
            exc = payload.get("exception")
            if not exc:
                continue
            out.append(
                {
                    "id": row["id"],
                    "input_id": row["input_id"],
                    "session_id": row["session_id"],
                    "symbol": row["symbol"],
                    "exception_type": exc.get("type"),
                    "exception_message": exc.get("message"),
                    "record": payload,
                }
            )
        return out

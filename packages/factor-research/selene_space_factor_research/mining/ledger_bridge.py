"""Mining → research-ledger bridge(公式注册事实的写入/读取面)。

把 T5 pipeline 的 confirm 域接进研究图谱账本:每条公式注册 = 一个
episode(subject=("formula", "formula:{canonical_hash}"))+ 一条 RECORDED
evidence(注册域 payload)。

torch-free 声明:本模块不 import torch;对 ``selene_space_storage`` 的
import 全部惰性(函数体内),保证 storage 不在 sys.path 时 ``import
ledger_bridge`` 本身不炸(iso 模式),缺失延迟到调用时暴露。

语义两条(计划 v3 改1 + Architect 裁定):
- **append-only 重测链**:同 hash 重测仍 append 新 episode 挂同 subject,
  derived_from 子→父自动成链(storage ``link_derived_from``,rowid 严格
  早于,首跑无边)。重测事实属注册域——verdict 从 VALID 变 SUSPICIOUS
  本身就是 IC 衰减证据,不得幂等压平。
- **取最新**:load 按 occurred_at DESC, rowid DESC 取每个 subject 的最新
  **可解析**条目(脏数据不是证据,挑选时跳过;同 subject 更早的有效条目
  仍可确立身份)。digest 过滤在取最新**之后**套用:最新注册属另一 vocab
  (W7 双键不匹配)⇒ 身份歧义 ⇒ 不拦,旧 vocab 历史不复活拦截。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SUBJECT_TYPE = "formula"

DEFAULT_LEDGER_DB = Path.home() / ".selenespace" / "data" / "selenespace.db"


def _subject_id(canonical_hash: str) -> str:
    return f"formula:{canonical_hash}"


def _hash_from_subject(subject_id: str) -> str:
    if not isinstance(subject_id, str) or not subject_id.startswith("formula:"):
        return ""
    return subject_id[len("formula:") :]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _entry_from_row(row: Any) -> dict[str, Any]:
    recorded = _safe_json(row["recorded_json"]) or {}
    return {
        "episode_id": str(row["id"]),
        "canonical_hash": _hash_from_subject(row["subject_id"]),
        "vocab_digest": recorded.get("vocab_digest"),
        "formula_tokens": recorded.get("formula_tokens"),
        "occurred_at": row["occurred_at"],
        "title": row["title"],
    }


async def _register_async(
    db_path: str | Path,
    *,
    canonical_hash: str,
    vocab_digest: str | None,
    formula_tokens: list[int] | None,
    readable: str,
    source: str,
    verdict: str,
    metrics: dict[str, Any] | None,
    occurred_at: str | None,
) -> dict[str, Any]:
    from selene_space_storage import ALL_MIGRATIONS, MigrationRunner
    from selene_space_storage.repositories.research_graph import (
        EpisodeRepository,
        EvidenceRepository,
        link_derived_from,
    )
    from selene_space_storage.sqlite import AsyncSQLiteManager

    if not canonical_hash:
        raise ValueError("canonical_hash must be non-empty")
    at = occurred_at or _now_iso()
    recorded: dict[str, Any] = {
        "vocab_digest": vocab_digest,
        "formula_tokens": list(formula_tokens) if formula_tokens else None,
        "source": source,
        "verdict": verdict,
        # lifecycle 初始子键:落账不做认识论判断,状态流转经
        # EpisodeRepository.record_lifecycle_transition 受控窄门(先例
        # factor_snapshot.py / run_probe.py —— 无此子键的 episode 一律
        # 不可流转)。
        "lifecycle": {"status": "DISCOVERED", "transitions": []},
    }
    payload: dict[str, Any] = {
        "canonical_hash": canonical_hash,
        "vocab_digest": vocab_digest,
        "formula_tokens": recorded["formula_tokens"],
        "source": source,
        "verdict": verdict,
        "readable": readable,
    }
    if metrics is not None:
        payload["metrics"] = metrics
    title = f"Formula registration: {readable or canonical_hash}"

    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(str(db_path), migration_runner=runner) as manager:
        ep_id = await EpisodeRepository(manager).create(
            SUBJECT_TYPE,
            _subject_id(canonical_hash),
            title,
            at,
            at,
            recorded=recorded,
            occurred_at=at,
        )
        await EvidenceRepository(manager).create(
            ep_id,
            "RECORDED",
            payload=payload,
            source_ref=source or "selene_space_factor_research.mining.ledger_bridge",
        )
        # 同 hash 重测链:子→父,首跑无边(link_derived_from 语义)。
        await link_derived_from(
            manager,
            child_episode_id=ep_id,
            subject_type=SUBJECT_TYPE,
            subject_id=_subject_id(canonical_hash),
        )
        known = await _load_known_async(manager, None)
        latest = known.get(canonical_hash)
    return {"new_episode_id": ep_id, "latest": latest}


def register_formula_episode(
    db_path: str | Path,
    *,
    canonical_hash: str,
    vocab_digest: str | None = None,
    formula_tokens: list[int] | None = None,
    readable: str = "",
    source: str = "",
    verdict: str = "VALID",
    metrics: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """Append 一条公式注册 episode(重测 = 新 episode,同 subject 成链)。

    返回 ``{"new_episode_id", "latest"}``;``latest`` 是该 subject 按 load
    语义(occurred_at DESC, rowid DESC,脏数据跳过)的最新条目 —— 重测后
    它指向新 episode。CLI 单进程一次调用,不做请求级幂等(v3 计划焦点题)。
    """
    return asyncio.run(
        _register_async(
            db_path,
            canonical_hash=canonical_hash,
            vocab_digest=vocab_digest,
            formula_tokens=formula_tokens,
            readable=readable,
            source=source,
            verdict=verdict,
            metrics=metrics,
            occurred_at=occurred_at,
        )
    )


async def _load_known_async(
    manager: Any, vocab_digest: str | None
) -> dict[str, dict[str, Any]]:
    rows = await manager.execute(
        "SELECT id, subject_id, title, recorded_json, occurred_at "
        "FROM research_episodes WHERE subject_type = ? "
        "ORDER BY occurred_at DESC, rowid DESC",
        (SUBJECT_TYPE,),
    )
    known: dict[str, dict[str, Any]] = {}
    for row in rows:
        # 脏数据(不可解析 recorded)不是证据:挑选最新时跳过,同 subject
        # 更早的有效条目仍可确立身份;空 hash 后缀同判脏。
        if _safe_json(row["recorded_json"]) is None:
            continue
        h = _hash_from_subject(row["subject_id"])
        if not h or h in known:
            continue
        known[h] = _entry_from_row(row)
    # digest 过滤在取最新之后:最新注册属另一 vocab ⇒ 身份歧义 ⇒ 不拦。
    if vocab_digest is not None:
        return {h: e for h, e in known.items() if e.get("vocab_digest") == vocab_digest}
    return known


def load_known_formulas(
    db_path: str | Path, *, vocab_digest: str | None = None
) -> dict[str, dict[str, Any]]:
    """读账本里已知公式的最新注册条目。

    返回 ``{canonical_hash: {episode_id, canonical_hash, vocab_digest,
    formula_tokens, occurred_at, title}}``。库文件不存在(从未落过账)⇒
    返回 ``{}`` 且**不建库**(读路径零副作用)。``vocab_digest`` 给定时,
    最新条目 digest 不符的公式被排除(不拦)。
    """
    if not Path(db_path).exists():
        return {}

    async def run() -> dict[str, dict[str, Any]]:
        from selene_space_storage import ALL_MIGRATIONS, MigrationRunner
        from selene_space_storage.sqlite import AsyncSQLiteManager

        runner = MigrationRunner(ALL_MIGRATIONS)
        async with AsyncSQLiteManager(str(db_path), migration_runner=runner) as manager:
            return await _load_known_async(manager, vocab_digest)

    return asyncio.run(run())

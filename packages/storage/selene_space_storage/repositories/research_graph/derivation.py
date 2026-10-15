"""因子级生命周期推导(消费口)—— 从 derived_from 链聚合证据,只出行动建议。

objects.py:35 明写的刻意留口:episode 的 lifecycle 是 per-episode 观察轴,
**因子级状态由消费方从链推导,storage 不做结转**(RETIRED 结转 = 复活)。
本模块就是那个消费方,边界三条:

- **纯读**:零写路径。建议 ≠ 流转 —— 流转走既有受控窄门
  (EpisodeRepository.record_lifecycle_transition,抽屉"推进/退役"按钮),
  本模块只把依据亮到人眼,转不转人拍板。
- **不造第三个状态机**:输出的是"行动建议"(retire/inspect),不是状态
  标签 —— 状态标签会假装成一台状态机,而这里只有规则表。两套既有状态词
  (episode 观察轴 DISCOVERED…RETIRED / 因子 IC 状态机 ACTIVE…RETIRED)
  均不在此映射,verdict→FactorState 是编造认识论。
- **诚实灰色地带**:链中曾 RETIRED 但存在更新 episode(重测链天然如此)
  不出建议,只标 ``retired_in_history`` —— storage 不结转,推导也不假装
  能裁决复活。

规则表(全部显式,每条建议带 basis_episode_id + reason 可审计):
- 最新 episode 已 RETIRED → 无建议(终态,现状即答案)
- 最新 verdict = INVALID   → retire(证伪证据)
- 最新 verdict = SUSPICIOUS → inspect(IC 衰减证据,保守:复查非直接退役)
- 链中存在 RETIRED 但非最新 → 不出建议,标 retired_in_history
"""

from __future__ import annotations

from typing import Any

from selene_space_storage.repositories.research_graph._shared import _load_json


def _lifecycle_of(episode: dict[str, Any]) -> dict[str, Any] | None:
    """episode 的 lifecycle 子键;无子键(历史落账/无关实体)→ None。"""
    recorded = episode.get("recorded")
    if not isinstance(recorded, dict):
        return None
    lc = recorded.get("lifecycle")
    return lc if isinstance(lc, dict) else None


def _verdict_of(episode: dict[str, Any]) -> str | None:
    """formula 注册 episode 的 verdict;其余实体 → None。

    verdict 藏在 recorded 顶层(ledger_bridge._register_async 写入)。
    """
    recorded = episode.get("recorded")
    if not isinstance(recorded, dict):
        return None
    v = recorded.get("verdict")
    return v if isinstance(v, str) else None


async def derive_subject_lifecycle(
    db: Any,
    *,
    subject_type: str,
    subject_id: str,
    limit: int = 50,
) -> dict[str, Any]:
    """按 subject 聚合链上证据,产出行动建议(纯读,不流转)。

    返回::

        {
          "subject_type", "subject_id",
          "chain": [{episode_id, occurred_at, status, verdict}],   # 新→旧
          "latest": {episode_id, status, verdict} | None,
          "retired_in_history": bool,
          "suggestions": [{action, basis_episode_id, reason}],
        }

    链 = 同 subject 的 episode 序列(derived_from 边连接的正是相邻同
    subject episode,故按 subject 查即链,无需走边)。async 与 repo 同型:
    AsyncSQLiteManager.execute 是协程接口。
    """
    rows = await db.execute(
        "SELECT id, occurred_at, recorded_json FROM research_episodes "
        "WHERE subject_type = ? AND subject_id = ? "
        "ORDER BY occurred_at DESC, rowid DESC LIMIT ?",
        (subject_type, subject_id, max(1, min(limit, 200))),
    )
    chain: list[dict[str, Any]] = []
    for row in rows:
        episode = dict(row)
        # 原始行带的是 recorded_json 字符串;repo.get 的 _load_json 形态在这里
        # 就地解析(脏行 json 失败 → recorded={} → status/verdict 均为 None,
        # 与"无 lifecycle 子键"同路径跳过,不炸)。
        try:
            episode["recorded"] = _load_json(episode.pop("recorded_json")) or {}
        except (TypeError, ValueError):
            episode["recorded"] = {}
        lc = _lifecycle_of(episode)
        chain.append(
            {
                "episode_id": str(episode["id"]),
                "occurred_at": episode["occurred_at"],
                "status": lc.get("status") if lc else None,
                "verdict": _verdict_of(episode),
            }
        )

    latest = chain[0] if chain else None
    retired_in_history = any(c["status"] == "RETIRED" for c in chain[1:])

    suggestions: list[dict[str, Any]] = []
    if latest is not None:
        if latest["status"] == "RETIRED":
            pass  # 终态:现状即答案,无建议
        elif latest["verdict"] == "INVALID":
            suggestions.append(
                {
                    "action": "retire",
                    "basis_episode_id": latest["episode_id"],
                    "reason": "最新注册 verdict=INVALID(证伪证据)",
                }
            )
        elif latest["verdict"] == "SUSPICIOUS":
            suggestions.append(
                {
                    "action": "inspect",
                    "basis_episode_id": latest["episode_id"],
                    "reason": "最新重测 verdict=SUSPICIOUS(IC 衰减证据,建议复查)",
                }
            )

    return {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "chain": chain,
        "latest": latest,
        "retired_in_history": retired_in_history,
        "suggestions": suggestions,
    }

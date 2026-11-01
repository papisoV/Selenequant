"""因子评估快照(factor snapshot)—— IC 面板落库为可复算 RECORDED 快照。

吸收 external-research-project Snapshot+Tearsheet 诚实纪律:payload 只存「对齐原始值」
(merge 前的 factor 值 × fut_ret_1 面板),衍生序列(rolling IC/IR/autocorr…)
一律由消费方从快照重算,绝不落库衍生值。无快照 = 拒绝渲染,不模拟。

快照契约(本模块是契约的唯一执行点):
- payload 首键 ``snapshot_schema`` 做类型标记,消费方(compare_modes/recall)
  可机械识别"这是快照数据不是观察"并跳过。
- **快照 episode 永不加第二条 evidence;新快照 = 新 episode。** Episode/
  Evidence 的观察语义是"当时 vs 现在的观察演化"(objects.py compare_modes),
  快照塞进去后 rows[0](快照 dict)vs rows[-1](轻量 dict)会永真 drifted。
- NaN 必须先转 None 再序列化:storage 的 ``_dump_json`` 是裸 ``json.dumps``
  (allow_nan=True 默认),会写出非法 JSON 字面量 ``NaN``。
- rows 是 merge 前的原始面板(full_dates 过滤后、含末根 NaN 行),NaN↔null
  往返保留;compute 内部 inner merge 的行集才与实时路径一致。契约前提:
  factor/ret 两侧 date×symbol 网格经 _build_cross_section_panel 的
  full_dates 过滤后严格一致(serialize 会校验,不一致即拒)。
- horizon 不进 snapshot_key:ICPanel 的 horizon 仅作用于 factor rank 的
  shift(ic_panel.py _lag_rank),不消费 fut_ret_1 以外的收益数据,任意
  horizon 都能从同一份快照精确重算。schema v2 已落 target 契约
  (ForwardReturnTarget,payload.target 元数据;rows 不扩列 —— 扩列留给
  真需求,见计划裁决 C)。
- 加载排序 ``occurred_at DESC, rowid DESC``:``_now()`` 是秒精度,new_id 是
  uuid4 恒非单调 —— 同秒双 save 只有 rowid(插入序)能分先后,与
  EvidenceRepository.list_for_episode(objects.py)的 rowid 收敛同款修法。
- 5MB payload 闸 fail-fast:快照是原始值不是档案库,超闸说明参数失真
  (limit 过大),拒绝落库而非静默压缩。
- episode recorded 带 ``lifecycle`` 初始子键(DISCOVERED, transitions=[]):
  落账不做认识论判断,状态流转经 EpisodeRepository 受控窄门;这是
  per-episode 观察周期状态,非因子级状态(新快照 = 新 DISCOVERED episode,
  因子级状态由消费方从 derived_from 链推导)。
"""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from selene_space_storage.repositories.research_graph import (
    EpisodeRepository,
    EvidenceRepository,
    ResearchEdgeRepository,
    link_derived_from,
)
from selene_space_storage.repositories.research_graph._shared import _load_json

SNAPSHOT_SCHEMA = 2
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024


@dataclasses.dataclass(frozen=True)
class ForwardReturnTarget:
    """评估定义(target)契约 —— IC 是"对着什么收益算的"的血缘元数据。

    default 与 fut_ret_1 唯一产出点 market_analysis._build_cross_section_panel
    的真实数学严格对齐(close.shift(-1)/close - 1.0,simple return)。
    key 纳入 target hash 留给 v3;当下唯一写入方就是 default。
    """

    entry_price: str = "current_close"
    exit_price: str = "close"
    horizon_bars: int = 1
    return_type: str = "simple"

    def definition(self) -> str:
        if self == DEFAULT_TARGET:
            return "close[t+1]/close[t] - 1"
        return (
            f"{self.exit_price}[t+{self.horizon_bars}]/{self.entry_price}[t] "
            f"({self.return_type})"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "horizon_bars": self.horizon_bars,
            "return_type": self.return_type,
            "definition": self.definition(),
        }


DEFAULT_TARGET = ForwardReturnTarget()


def resolve_target(payload: dict[str, Any]) -> ForwardReturnTarget:
    """reader 按 schema 分支:v1 显式 default(simple),v2 读显式字段。"""
    target = payload.get("target")
    if payload.get("snapshot_schema", 1) >= 2 and isinstance(target, dict):
        return ForwardReturnTarget(
            entry_price=target["entry_price"],
            exit_price=target["exit_price"],
            horizon_bars=int(target["horizon_bars"]),
            return_type=target["return_type"],
        )
    return DEFAULT_TARGET


# 与 recall.py 的 _ANCHOR_KEYS 对齐(fail-fast 判据的数据源);run_probe.py
# 同款复制 —— 私有常量不跨模块 import,改值时两处同步。
ANCHOR_KEYS = ("strategy_ids", "intervals", "regimes")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def snapshot_key(
    *,
    exchange: str,
    market_type: str,
    symbols: list[str],
    interval: str,
    factor: str,
    period: int,
    limit: int,
) -> str:
    """参数集 → subject_id。symbols 排序规范化(乱序同 key);horizon 不进 key。"""
    canon = ",".join(sorted({s.strip() for s in symbols if s.strip()}))
    return (
        f"ic-panel:{exchange}:{market_type}:{canon}:"
        f"{interval}:{factor}:{period}:{limit}"
    )


def _pyfloat(v: Any) -> float | None:
    f = float(v)
    return None if math.isnan(f) else f


def serialize_panel(factor_df: pd.DataFrame, ret_df: pd.DataFrame) -> list[list[Any]]:
    """两个 merge 前面板 → rows:[[iso_date, symbol, factor, fut_ret_1], ...]。

    行序按 (trade_date, symbol) 排序后落库;NaN→null。网格不一致直接拒
    (契约前提被破坏时静默丢行比重算错位更糟)。
    """
    f = factor_df.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
    r = ret_df.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
    if len(f) != len(r) or not (
        f["trade_date"].tolist() == r["trade_date"].tolist()
        and f["symbol"].tolist() == r["symbol"].tolist()
    ):
        raise ValueError("factor/ret panel grids diverge; snapshot would be lossy")
    rows: list[list[Any]] = []
    for i in range(len(f)):
        rows.append(
            [
                pd.Timestamp(f.at[i, "trade_date"]).isoformat(),
                str(f.at[i, "symbol"]),
                _pyfloat(f.at[i, "factor"]),
                _pyfloat(r.at[i, "fut_ret_1"]),
            ]
        )
    return rows


def deserialize_panel(rows: list[list[Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """rows → (factor_df, ret_df),列名与 ICPanel 默认列零适配(null→NaN)。"""
    if not rows:
        raise ValueError("empty snapshot rows")
    dates: list[pd.Timestamp] = []
    symbols: list[str] = []
    factors: list[float] = []
    rets: list[float] = []
    for row in rows:
        iso, sym, factor, fut = row
        dates.append(pd.Timestamp(iso))
        symbols.append(str(sym))
        factors.append(float("nan") if factor is None else float(factor))
        rets.append(float("nan") if fut is None else float(fut))
    factor_df = pd.DataFrame(
        {"trade_date": dates, "symbol": symbols, "factor": factors}
    )
    ret_df = pd.DataFrame({"trade_date": dates, "symbol": symbols, "fut_ret_1": rets})
    return factor_df, ret_df


async def check_question_anchor(manager: Any, question_id: str) -> dict[str, Any]:
    """fail-fast:问题不存在(KeyError)或 anchor 与 ANCHOR_KEYS 无非空交集
    (ValueError)即拒 —— anchor 无可匹配键 = 证据永不召回,写进去是静默
    废数据(与 scripts/run_probe.py 同判据;那边抛 SystemExit 是 CLI 语义,
    服务层抛可映射 4xx 的异常)。"""
    rows = await manager.execute(
        "SELECT anchor_json FROM research_questions WHERE id = ?", (question_id,)
    )
    if not rows:
        raise KeyError(f"question not found: {question_id}")
    try:
        anchor = json.loads(rows[0]["anchor_json"] or "{}") or {}
    except (TypeError, ValueError):
        anchor = {}
    overlap = [k for k in ANCHOR_KEYS if anchor.get(k)]
    if not overlap:
        raise ValueError(
            f"question {question_id} anchor has no usable keys; "
            f"need >=1 non-empty of {ANCHOR_KEYS}, got {anchor!r}"
        )
    return anchor


async def save_snapshot(
    manager: Any,
    *,
    exchange: str,
    market_type: str,
    symbols: list[str],
    interval: str,
    factor: str,
    period: int,
    limit: int,
    factor_df: pd.DataFrame,
    ret_df: pd.DataFrame,
    question_id: str | None = None,
    target: ForwardReturnTarget | None = None,
) -> dict[str, Any]:
    """面板 → Episode("market", 参数集) + Evidence(RECORDED, 原始值 payload)。

    可选 question_id 走 addresses 边(先 anchor fail-fast)。target 必须是
    default(非 default fail-fast —— 快照消费方尚只会重算 simple fut_ret_1,
    收其他 target 落库 = 消费方拿错的收益重算)。返回
    {episode_id, evidence_id, key, occurred_at, n_rows}。
    """
    eff_target = target if target is not None else DEFAULT_TARGET
    if eff_target != DEFAULT_TARGET:
        raise NotImplementedError(
            "non-default ForwardReturnTarget not supported until v3 "
            f"(got {eff_target!r}); snapshot consumers only recompute "
            "simple fut_ret_1"
        )
    key = snapshot_key(
        exchange=exchange,
        market_type=market_type,
        symbols=symbols,
        interval=interval,
        factor=factor,
        period=period,
        limit=limit,
    )
    rows = serialize_panel(factor_df, ret_df)
    now = _now()
    boundary_start = rows[0][0]
    boundary_end = rows[-1][0]
    payload: dict[str, Any] = {
        "snapshot_schema": SNAPSHOT_SCHEMA,
        "params": {
            "exchange": exchange,
            "market_type": market_type,
            "symbols": sorted({s.strip() for s in symbols if s.strip()}),
            "interval": interval,
            "factor": factor,
            "period": period,
            "limit": limit,
        },
        "rows": rows,
        "boundary": {"start": boundary_start, "end": boundary_end},
        "target": eff_target.as_dict(),
    }
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"snapshot payload exceeds {MAX_PAYLOAD_BYTES} bytes "
            f"(n_rows={len(rows)}); reduce limit, do not archive raw values"
        )
    if question_id is not None:
        await check_question_anchor(manager, question_id)
    ep_id = await EpisodeRepository(manager).create(
        "market",
        key,
        f"IC snapshot {key} @ {now}",
        boundary_start,
        boundary_end,
        recorded={
            "kind": "factor_snapshot",
            "snapshot_schema": SNAPSHOT_SCHEMA,
            "n_rows": len(rows),
            "lifecycle": {"status": "DISCOVERED", "transitions": []},
        },
        occurred_at=now,
    )
    ev_id = await EvidenceRepository(manager).create(
        ep_id,
        "RECORDED",
        payload=payload,
        source_ref="app/services/factor_snapshot.py",
    )
    if question_id is not None:
        await ResearchEdgeRepository(manager).add_fact_edge(
            ("evidence", ev_id), ("question", question_id), "addresses"
        )
    # 同参数集(snapshot_key)重跑链:新快照从上一份派生(子→父,首跑无边)
    await link_derived_from(
        manager,
        child_episode_id=ep_id,
        subject_type="market",
        subject_id=key,
    )
    return {
        "episode_id": ep_id,
        "evidence_id": ev_id,
        "key": key,
        "occurred_at": now,
        "n_rows": len(rows),
    }


async def load_latest_snapshot(manager: Any, *, key: str) -> dict[str, Any] | None:
    """同 key 最新快照(occurred_at DESC, rowid DESC)→ {payload, ...} | None。

    rowid 二级键:occurred_at 秒精度 + uuid4 id 恒非单调,同秒双 save 只有
    插入序能分先后(objects.py list_for_episode 的同款病历与修法)。
    """
    rows = await manager.execute(
        "SELECT e.id AS episode_id, e.occurred_at, e.boundary_start, e.boundary_end, "
        "v.id AS evidence_id, v.observed_at AS evidence_observed_at, v.payload_json "
        "FROM research_episodes e "
        "JOIN research_evidence v ON v.episode_id = e.id "
        "WHERE e.subject_type = 'market' AND e.subject_id = ? AND v.mode = 'RECORDED' "
        "ORDER BY e.occurred_at DESC, v.rowid DESC LIMIT 1",
        (key,),
    )
    if not rows:
        return None
    row = dict(rows[0])
    payload = _load_json(row.pop("payload_json")) or {}
    return {
        "episode_id": row["episode_id"],
        "evidence_id": row["evidence_id"],
        "key": key,
        "occurred_at": row["occurred_at"],
        "evidence_observed_at": row["evidence_observed_at"],
        "boundary_start": row["boundary_start"],
        "boundary_end": row["boundary_end"],
        "payload": payload,
    }


async def load_lineage(
    manager: Any, episode_id: str, max_depth: int = 10
) -> list[dict[str, Any]]:
    """沿 derived_from 边(子→父)收集快照祖先链,最近祖先在前。

    空链(首跑无边)返回 []。max_depth 防环兜底(rowid 前置条件已保证
    只指向过去,这里是消费方防线)。
    """
    ancestors: list[dict[str, Any]] = []
    current = episode_id
    for _ in range(max_depth):
        rows = await manager.execute(
            "SELECT e.id, e.occurred_at FROM research_edges r "
            "JOIN research_episodes e ON e.id = r.to_id "
            "WHERE r.from_type = 'episode' AND r.from_id = ? "
            "AND r.to_type = 'episode' AND r.kind = 'derived_from' "
            "ORDER BY r.rowid DESC LIMIT 1",
            (current,),
        )
        if not rows:
            break
        row = dict(rows[0])
        ancestors.append({"episode_id": row["id"], "occurred_at": row["occurred_at"]})
        current = row["id"]
    return ancestors

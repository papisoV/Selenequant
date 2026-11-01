/** B4 聚合端点 GET /api/v1/research/today 返回 shape(与 research_missions.py today_projection 对齐) */

import type { MissionPhase, MissionSummary } from "./missions-types";

export interface LastHandoff {
  mission_id: string;
  /** 交接发生在哪个 Mission 上 — 没有它,"你做了什么"缺主语 */
  mission_title: string;
  occurred_at: string;
  kind: string;
  detail: Record<string, unknown> | null;
}

/** GET /api/v1/research/today — 顶条七环的跨表计数(列表可截断,环上的数不能) */
export interface TodayCounts {
  /** `research_questions` 里 status=open 的全量条数 —— 不是 mission 计数 */
  open_questions: number;
}

/**
 * NEEDS YOU 卡片 — 同一「标的 + 问句」的多条重复开案已由后端折叠成一张卡。
 * 重复开案来自不同入口各开一次案(心跳 / CLI),问句相同、标题不同;
 * 逐条拍板等于把同一件事裁决 N 次。
 */
export interface NeedsYouCardItem extends MissionSummary {
  /** 被折叠进来的重复条数(0 = 只有它自己) */
  duplicates?: number;
  /** 组内其余 mission id — 一次处理整组用 */
  duplicate_ids?: string[];
}

export interface TodayResponse {
  last_handoff: LastHandoff | null;
  needs_you: NeedsYouCardItem[];
  /** 全部 needs_input 行数(折叠前);缺省时回退 needs_you 长度 */
  needs_you_total?: number;
  working: MissionSummary[];
  world_changed: MissionSummary[];
  /** 老后端无此字段时前端回退到 mission 计数 */
  counts?: TodayCounts;
}

/** GET /api/v1/research/agent-activity — Selene 此刻在干什么的聚合读面 */
export interface AgentActivityRunning {
  question_id: string;
  question_title: string;
  question_status: string | null;
  started_at: string;
}

export interface AgentActivityEventDetail {
  perspective?: string;
  status?: string;
  cited_count?: number;
  excerpt?: string;
  truncated?: boolean;
  // 非视角事件(created/closed/reopened/finished)原样透传
  [key: string]: unknown;
}

/**
 * 一行活动。两路来源合并而成:
 * - `source: "question"` — 调查会事件(`research_question_events`),带 `question_id`
 * - `source: "mission"`  — mission 活动(`mission_activity`),带 `mission_id`
 * 另一侧的 id 为 `null`。`question_title` 在两路里都是"这条活动属于谁"的标题。
 */
export interface AgentActivityEvent {
  source: "question" | "mission";
  /** 两路来源各自的自增 id,跨来源不唯一 — React key 必须配 `source` 用 */
  id: number;
  question_id: string | null;
  mission_id: string | null;
  question_title: string | null;
  occurred_at: string;
  actor: string;
  kind: string;
  detail: AgentActivityEventDetail;
}

export interface AgentActivityHeartbeat {
  enabled: boolean;
  busy?: boolean;
  attempts_today?: number;
  last_round?: {
    at?: number;
    outcomes?: { ok?: number; skipped?: number; failed?: number };
  } | null;
  next_fire_at?: number | null;
}

export interface AgentActivityResponse {
  running: AgentActivityRunning[];
  recent_events: AgentActivityEvent[];
  heartbeat: AgentActivityHeartbeat;
}

export type { MissionPhase };

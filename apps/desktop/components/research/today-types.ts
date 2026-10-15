/** B4 聚合端点 GET /api/v1/research/today 返回 shape(与 research_missions.py today_projection 对齐) */

import type { MissionPhase, MissionSummary } from "./missions-types";

export interface LastHandoff {
  mission_id: string;
  occurred_at: string;
  kind: string;
  detail: Record<string, unknown> | null;
}

export interface TodayResponse {
  last_handoff: LastHandoff | null;
  needs_you: MissionSummary[];
  working: MissionSummary[];
  world_changed: MissionSummary[];
}

export type { MissionPhase };

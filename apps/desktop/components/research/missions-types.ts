/** 5 端点返回 shape(与 services/local-api research_missions.py + v015 schema 对齐) */

export type MissionPhase = "investigation" | "validation" | "needs_input" | "resolved" | "parked";

export interface MissionRow {
  id: string;
  title: string;
  question: string;
  subject_type: "strategy" | "market" | "event" | "system";
  subject_id: string;
  phase: MissionPhase;
  stage: string;
  created_by: string;
  opened_at: string;
  updated_at: string;
  resolved_at: string | null;
  context: Record<string, unknown> | null;
  outcome: Record<string, unknown> | null;
}

export interface ActivityRow {
  id: number;
  mission_id: string;
  occurred_at: string;
  actor: "selene" | "user" | "system";
  kind: string;
  detail: Record<string, unknown> | null;
}

/** GET /missions 列表投影(_mission_projection) — 仅行内字段,无 context/outcome */
export interface MissionSummary {
  id: string;
  title: string;
  question: string;
  phase: MissionPhase;
  subject_type: MissionRow["subject_type"];
  subject_id: string;
  updated_at: string;
}

export interface MissionsListResponse {
  missions: MissionSummary[];
}

export interface MissionDetailResponse {
  mission: MissionRow;
  activity: ActivityRow[];
}

export interface ActivityResponse {
  activity: ActivityRow[];
}

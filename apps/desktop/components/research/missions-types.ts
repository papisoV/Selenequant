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
  /** 同一问句重复开案的条数(不含代表卡自己)。后端按「标的 + 问句」折叠,
   *  不按标题 —— 见 research_missions._fold_same_question。旧后端不返回 → 0 */
  duplicates?: number;
  /** 被折叠掉的 id。只有 id,没有它们的标题,所以展开时只列 id,不编造标题。 */
  duplicate_ids?: string[];
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

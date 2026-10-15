/**
 * Thesis 计分卡类型 + 词表中文映射 — 已接真链路。
 *
 * 接线状态(2026-10-02 核实):后端 GET /research/missions/{id}/thesis
 * (research_missions.py:773)已有真实现(list_pillars → aggregate_status →
 * 信号分布 → 末次 drift_check);前端 missions-page.tsx:25 走 useApi 真链路。
 * 本文件的 mock 仅测试引用。"数据冻结不接线"注释已过时,勿据此跳过优化。
 *
 * 认识论纪律(plan 定稿):signal 十档是分析结论的封闭词表,禁合成评分
 * ("summing invents precision");aggregate 是一个词+理由,无数字分值。
 */

export interface MissionPillar {
  id: string;
  claim: string;
  priority: "core" | "supporting";
  baseline?: string | null;
  expectedPath?: string | null;
  confirmThreshold?: string | null;
  /** 预警阈值(后端 warning_threshold):落入确认与证伪之间的灰区。 */
  warningThreshold?: string | null;
  breakThreshold?: string | null;
  thresholdProvenance: "inherited" | "draft" | "approved";
  status:
    | "strengthening"
    | "intact"
    | "watch"
    | "impaired"
    | "broken"
    | "changed"
    | "untested"
    | "retired";
  signal: Signal;
  statusNote?: string | null;
  supersededBy?: string | null;
  nextProofPoint?: string | null;
  nextProofSource?: string | null;
}

export type Signal =
  | "strongly_confirming"
  | "confirming"
  | "mildly_confirming"
  | "neutral"
  | "mildly_weakening"
  | "weakening"
  | "strongly_weakening"
  | "mixed"
  | "invalidating"
  | "untested";

export interface MissionEvidenceRow {
  id: string;
  pillarId: string;
  occurredAt: string;
  sourceType: string;
  sourceRef?: string | null;
  fact: string;
  interpretation: string;
  signal: Signal;
  followUp?: string | null;
}

export interface DriftCheckEntry {
  occurredAt: string;
  checkedBy: string;
  patterns: Record<
    | "reason_changed"
    | "catalyst_laundering"
    | "valuation_substitution"
    | "dismissal_by_horizon"
    | "protection_as_reason",
    { present: boolean; note?: string }
  >;
}

export interface ThesisScorecard {
  missionId: string;
  pillars: MissionPillar[];
  aggregate:
    | "strengthening"
    | "intact"
    | "watch"
    | "impaired"
    | "broken"
    | "changed"
    | "untested";
  aggregateReason: string;
  requiresOverride: boolean;
  evidence: MissionEvidenceRow[];
  lastDriftCheck?: DriftCheckEntry | null;
}

export const SIGNAL_LABELS: Record<Signal, string> = {
  strongly_confirming: "强确认",
  confirming: "确认",
  mildly_confirming: "弱确认",
  neutral: "中性",
  mildly_weakening: "弱削弱",
  weakening: "削弱",
  strongly_weakening: "强削弱",
  mixed: "矛盾",
  invalidating: "证伪",
  untested: "未检验",
};

export const PILLAR_STATUS_LABELS: Record<MissionPillar["status"], string> = {
  strengthening: "增强",
  intact: "完好",
  watch: "观察",
  impaired: "受损",
  broken: "破毁",
  changed: "已变",
  untested: "未检验",
  retired: "退役",
};

export const AGGREGATE_LABELS: Record<ThesisScorecard["aggregate"], string> = {
  strengthening: "增强",
  intact: "完好",
  watch: "观察",
  impaired: "受损",
  broken: "破毁",
  changed: "已变",
  untested: "未检验",
};

export const DRIFT_PATTERN_LABELS: Record<keyof DriftCheckEntry["patterns"], string> = {
  reason_changed: "理由漂移",
  catalyst_laundering: "催化叙事换锚",
  valuation_substitution: "估值逻辑替换",
  dismissal_by_horizon: "以周期长短打发反证",
  protection_as_reason: "风控触发当作持有理由",
};

/* ---- 后端 snake_case → 前端 camelCase 适配层 ---- */

/** GET /thesis pillars 元素的 snake_case 形状(repo dict 直出) */
interface PillarRaw {
  id: string;
  claim: string;
  priority: MissionPillar["priority"];
  baseline: string | null;
  expected_path: string | null;
  confirm_threshold: string | null;
  warning_threshold: string | null;
  break_threshold: string | null;
  threshold_provenance: MissionPillar["thresholdProvenance"];
  status: MissionPillar["status"];
  signal: Signal;
  status_note: string | null;
  superseded_by: string | null;
  next_proof_point: string | null;
  next_proof_source: string | null;
}

/** GET /thesis evidence 元素的 snake_case 形状 */
interface EvidenceRaw {
  id: string;
  pillar_id: string;
  occurred_at: string;
  source_type: string;
  source_ref: string | null;
  fact: string;
  interpretation: string;
  signal: Signal;
  follow_up: string | null;
}

/** GET /thesis 响应的 snake_case 形状(端点 dict 直出) */
export interface ThesisScorecardRaw {
  mission_id: string;
  pillars: PillarRaw[];
  aggregate: ThesisScorecard["aggregate"];
  aggregate_reason: string;
  requires_override: boolean;
  signal_distribution: Record<string, number>;
  evidence_count: number;
  evidence: EvidenceRaw[];
  last_drift_check: {
    occurred_at: string;
    kind: string;
    detail: {
      patterns: DriftCheckEntry["patterns"];
      checked_by: string;
      note?: string;
    };
  } | null;
}

function toPillar(p: PillarRaw): MissionPillar {
  return {
    id: p.id,
    claim: p.claim,
    priority: p.priority,
    baseline: p.baseline,
    expectedPath: p.expected_path,
    confirmThreshold: p.confirm_threshold,
    warningThreshold: p.warning_threshold,
    breakThreshold: p.break_threshold,
    thresholdProvenance: p.threshold_provenance,
    status: p.status,
    signal: p.signal,
    statusNote: p.status_note,
    supersededBy: p.superseded_by,
    nextProofPoint: p.next_proof_point,
    nextProofSource: p.next_proof_source,
  };
}

function toEvidence(e: EvidenceRaw): MissionEvidenceRow {
  return {
    id: e.id,
    pillarId: e.pillar_id,
    occurredAt: e.occurred_at,
    sourceType: e.source_type,
    sourceRef: e.source_ref,
    fact: e.fact,
    interpretation: e.interpretation,
    signal: e.signal,
    followUp: e.follow_up,
  };
}

/** 把 GET /thesis 的 snake_case 响应映射成 ThesisScorecard;无数据返回 null。 */
export function toThesisScorecard(raw: ThesisScorecardRaw | null): ThesisScorecard | null {
  if (!raw || !raw.mission_id) return null;
  return {
    missionId: raw.mission_id,
    pillars: (raw.pillars ?? []).filter((p) => !p.superseded_by && p.status !== "retired").map(toPillar),
    aggregate: raw.aggregate,
    aggregateReason: raw.aggregate_reason,
    requiresOverride: raw.requires_override,
    evidence: (raw.evidence ?? []).map(toEvidence),
    lastDriftCheck: raw.last_drift_check
      ? {
          occurredAt: raw.last_drift_check.occurred_at,
          checkedBy: raw.last_drift_check.detail.checked_by,
          patterns: raw.last_drift_check.detail.patterns,
        }
      : null,
  };
}

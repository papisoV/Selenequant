"use client";

/**
 * 研究图谱客户端 —— W2 Question 面的数据通道。
 *
 * 后端约定(与 research.py 同族):成功路径返回 APIResponse 信封 {data, meta, error};
 * /api/v1/research/ 不在 BARE_ENDPOINT_PATTERNS 里,api()/apiPost() 走 fetchEnvelope
 * 的无条件解包分支 —— 所以后端必须包信封,否则这里全部静默返回 null
 * (回归护栏见 research-graph.test.ts 的反向断言)。
 */

import { api, apiPost } from "@/lib/api-client";

export const LEDGER_KINDS = ["revision", "hypothesis", "evidence"] as const;

export interface Question {
  id: string;
  title: string;
  body: string;
  // 终态值直接落在 status(parked/superseded/falsified/sufficiently_answered),
  // 后端没有独立 terminal_status 字段
  status: string;
  anchor: Record<string, unknown>;
  revision_count: number;
  // 后端字段是 opened_at,不是 created_at
  opened_at: string;
  closed_at: string | null;
  updated_at: string;
  // created_by='selene' 且 parent_question_id 非空 = 调查会提议、待拍板的子问题
  created_by: string;
  parent_question_id: string | null;
  // v025 主动复访:下次该回看此问题的时点(ISO)。可选 = 旧后端(未跑 v025)不返回
  next_revisit_at?: string | null;
}

export interface Revision {
  id: string;
  question_id: string;
  seq: number;
  content: string;
  rationale: string[];
  cited: string[];
  actor: string;
  created_at: string;
  // v025 horizon 二分:structural=长期有效的判断 / tactical=短期观察性结论。
  // 可选 = 旧后端不返回
  horizon?: "structural" | "tactical";
  // v030:主持人输出里可机器判定的字段。键缺失 = 模型没写这一段
  // (不是写了空)—— 缺口另有 structured_missing 事件可查。
  structured?: {
    // 数字(0-1)或模型原话(如"无法给")
    confidence?: number | string;
    falsified_when?: string;
    applies_when?: string;
  };
}

export interface QuestionEvent {
  id: string;
  question_id: string;
  kind: string;
  detail: Record<string, unknown> | null;
  occurred_at: string;
}

export interface GraphEdge {
  id: string;
  from_type: string;
  from_id: string;
  to_type: string;
  to_id: string;
  kind: string;
}

export interface QuestionDetail {
  question: Question;
  revisions: Revision[];
  events: QuestionEvent[];
  edges: GraphEdge[];
  // P3:该问题的调查是否正在后台跑(POST 立即 202,进度靠轮询 detail)
  investigate_running: boolean;
  // cited 里 ev- 对象的轻量投影(RECORDED=探针实测 → 探针徽章;
  // episode_id → 点徽章开 episode 抽屉)。键仅含 ev- 前缀
  cited_refs: CitedRef[];
}

export interface CitedRef {
  id: string;
  mode: string | null;
  episode_id: string | null;
}

export interface EpisodeLifecycleTransition {
  from: string;
  to: string;
  at: string;
  actor: string;
  reason?: string;
}

// 账本 episode(research_graph 表,区别于经验回放的 /api/v1/episodes):
// recorded 含 probe/snapshot/lifecycle 子键(探针落账/快照落账/观察周期)
export interface LedgerEpisode {
  id: string;
  subject_type: string;
  subject_id: string;
  title: string;
  recorded: {
    probe?: string;
    kind?: string;
    snapshot_schema?: string;
    lifecycle?: {
      status: string;
      transitions: EpisodeLifecycleTransition[];
    };
    target?: Record<string, unknown>;
    [key: string]: unknown;
  };
  occurred_at: string;
}

export interface LedgerEvidence {
  id: string;
  episode_id: string;
  mode: string;
  payload: Record<string, unknown> | null;
  source_ref: string;
  created_at: string;
}

export interface EpisodeDetailEdge {
  id: string;
  from_type: string;
  from_id: string;
  to_type: string;
  to_id: string;
  kind: string;
}

export interface EpisodeDetail {
  episode: LedgerEpisode;
  evidence: LedgerEvidence[];
  edges?: EpisodeDetailEdge[];
}

export interface RecallCandidate {
  id: string;
  kind: string;
  text: string;
  question_id: string | null;
  score: number;
  matched_on: string[];
  recall_class: string;
  evidence_chain: string[];
  created_at: string;
  // v025:revision 候选才带(hypothesis 无此键);可选 = 旧后端不返回
  horizon?: "structural" | "tactical";
  // 回放模式专属(N1):evidence 候选带数据窗标注;默认路径不返回此键。
  // boundary 缺失时为 null(诚实说"不知道窗"),不伪造。
  data_window?: { start: string | null; end: string | null } | null;
}

export interface RecallResponse {
  candidates: RecallCandidate[];
  learned: number;
  records: number;
}

export interface CitationMigration {
  question_id: string;
  from_revision_id: string | null;
  to_revision_id: string | null;
  added: string[];
  dropped: string[];
  retained: string[];
}

// 调查会(A1):perspectives 是 investigate_perspective 事件的 id(含失败视角,
// 缺席可审计);new_questions 是主持人提议、落库待拍板的子问题
export interface CouncilNewQuestion {
  id: string;
  title: string;
}

export interface CouncilResult {
  perspectives: number[];
  new_questions: CouncilNewQuestion[];
}

// P3:调查完成信号从 investigate_finished 事件取,不再随 POST 响应返回
// council 回放调查:as_of 仅回放轮出现(成败都带,后端 N1 第二刀)
export interface MarketMaterialDetail {
  state:
    | "ok"
    | "no_symbol"
    | "klines_missing"
    | "klines_empty"
    | "error"
    | "replay";
  reason: string;
  symbol?: string | null;
  funding_available?: boolean | null;
}

export interface InvestigateFinishedDetail {
  status: "ok" | "failed";
  revision_id?: string;
  new_questions?: CouncilNewQuestion[];
  citation_migration?: CitationMigration;
  error?: string;
  as_of?: string;
  /** 本轮行情材料注入结果;缺省 = 旧版后端,前端不渲染行情行 */
  market_material?: MarketMaterialDetail;
}

export interface GraphViewNode {
  id: string;
  category: "question" | "revision" | "hypothesis" | "episode" | "mission";
  name: string;
  status?: string;
  phase?: string;
  question_id?: string | null;
  actor?: string;
  subject_type?: string;
  subject_id?: string;
  created_at: string;
}

export interface GraphViewLink {
  id: string;
  source: string;
  target: string;
  kind: string;
}

export interface GraphView {
  nodes: GraphViewNode[];
  links: GraphViewLink[];
}

const BASE = "/api/v1/research/graph";

export function listQuestions(params?: {
  status?: string;
  parent?: string;
  limit?: number;
}) {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.parent) qs.set("parent", params.parent);
  if (params?.limit) qs.set("limit", String(params.limit));
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return api<{ questions: Question[] }>(`${BASE}/questions${suffix}`);
}

export function getQuestion(questionId: string) {
  return api<QuestionDetail>(`${BASE}/questions/${encodeURIComponent(questionId)}`);
}

export function createQuestion(payload: {
  title: string;
  body?: string;
  anchor?: Record<string, unknown>;
}) {
  return apiPost<{ question_id: string }>(`${BASE}/questions`, payload);
}

export function addRevision(
  questionId: string,
  payload: {
    content: string;
    rationale?: string[];
    cited?: string[];
    horizon?: "structural" | "tactical";
  },
) {
  return apiPost<{ revision: Revision; citation_migration: CitationMigration }>(
    `${BASE}/questions/${encodeURIComponent(questionId)}/revisions`,
    payload,
  );
}

// P3:POST 只触发后台调查,立即 202;进度与结果靠轮询 getQuestion 的 events
// council 回放调查(N1 第二刀):payload.as_of 非空 = 以该时刻为认知边界的
// 回放调查;省略(或默认空体)与现状同形。回放轮产出以当前时刻落库,
// rationale 带 as_of_replay: 前缀。
// guidance(人工介入):研究负责人本轮的关注点,注入三视角与主持人 prompt。
export function investigateQuestion(
  questionId: string,
  payload?: { as_of?: string; guidance?: string },
) {
  const body: { as_of?: string; guidance?: string } = {};
  if (payload?.as_of) body.as_of = payload.as_of;
  if (payload?.guidance) body.guidance = payload.guidance;
  return apiPost<{ question_id: string; status: string }>(
    `${BASE}/questions/${encodeURIComponent(questionId)}/investigate`,
    body,
  );
}

/** 人工叫停正在跑的调查会。200=cancelled/finished,202=cancelling(慢取消兜底)。 */
export function cancelInvestigation(questionId: string) {
  return apiPost<{ question_id: string; status: "cancelled" | "finished" | "cancelling" }>(
    `${BASE}/questions/${encodeURIComponent(questionId)}/investigate/cancel`,
    {},
  );
}

// --- question → pillar 桥 ----------------------------------------------------

/** 活跃支柱(非 superseded 且非 retired)—— 与后端 aggregate_status 同源。 */
export interface PromoteActivePillar {
  id: string;
  claim: string | null;
  status: string | null;
  priority: string | null;
}

export interface PromoteDraft {
  mission: {
    title: string;
    question: string;
    subject_type: string | null;
    subject_id: string | null;
  };
  pillar: {
    claim: string;
    priority: string;
    baseline: string | null;
    break_threshold: string | null;
    next_proof_point: string | null;
    next_proof_source: string | null;
    model_line: string;
    provenance: string;
    threshold_provenance: string;
  };
  subject_suggestion: { subject_type: string; subject_id: string } | null;
  subject_reason: string;
  segments_found: string[];
  revision_id: string;
  revision_seq: number;
  warnings: string[];
  existing_mission_id: string | null;
  existing_pillars: PromoteActivePillar[];
}

/** 取草稿(纯读)。override 参数用于用户改主体后刷新 existing_pillars 警告。 */
export function getPromoteDraft(
  questionId: string,
  params?: { seq?: number; subject_type?: string; subject_id?: string },
) {
  const qs = new URLSearchParams();
  if (params?.seq != null) qs.set("seq", String(params.seq));
  if (params?.subject_type) qs.set("subject_type", params.subject_type);
  if (params?.subject_id) qs.set("subject_id", params.subject_id);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return api<PromoteDraft>(
    `${BASE}/questions/${encodeURIComponent(questionId)}/promote-draft${suffix}`,
  );
}

export interface PromoteResult {
  mission_id: string;
  pillar_id: string;
  reused: boolean;
  edge_written: boolean;
  revision_id: string;
}

/** 提交提升(唯一写入口)。claim 硬门与 model_line 强制都在服务端。 */
export function promoteRevision(
  questionId: string,
  payload: {
    mission: {
      title: string;
      question: string;
      subject_type: string;
      subject_id: string;
    };
    pillar: {
      claim: string;
      priority?: string;
      baseline?: string | null;
      break_threshold?: string | null;
      next_proof_point?: string | null;
      next_proof_source?: string | null;
      provenance?: string;
      threshold_provenance?: string;
    };
    revision_seq: number;
  },
) {
  return apiPost<PromoteResult>(
    `${BASE}/questions/${encodeURIComponent(questionId)}/promote`,
    payload,
  );
}

export function closeQuestion(
  questionId: string,
  payload: { terminal_status: string; note?: string },
) {
  return apiPost<{ question: Question }>(
    `${BASE}/questions/${encodeURIComponent(questionId)}/close`,
    payload,
  );
}

export function reopenQuestion(questionId: string, payload: { reason: string }) {
  return apiPost<{ question: Question }>(
    `${BASE}/questions/${encodeURIComponent(questionId)}/reopen`,
    payload,
  );
}

export function succeedQuestion(
  questionId: string,
  payload: { title: string; body?: string },
) {
  return apiPost<{ question_id: string; question: Question | null }>(
    `${BASE}/questions/${encodeURIComponent(questionId)}/succeed`,
    payload,
  );
}

// v025 主动复访:设/清 Question 复访时点。next_revisit_at=null 清除;
// 后端只收 open/parked(终态 400);格式须与后端时钟同族(UTC ISO)——
// due() 是 TEXT 字典序比较
export function setRevisit(
  questionId: string,
  nextRevisitAt: string | null,
) {
  return apiPost<{ question: Question }>(
    `${BASE}/questions/${encodeURIComponent(questionId)}/revisit`,
    { next_revisit_at: nextRevisitAt },
  );
}

// 到期该复访的问题清单(后端 due() 同形):open/parked、next_revisit_at <= now
export interface DueQuestion {
  id: string;
  title: string;
  status: string;
  next_revisit_at: string;
  opened_at: string;
  updated_at: string;
  anchor: Record<string, unknown>;
}

// ``total`` 是不受 limit 影响的到期总数 —— 界面说"N 个到期"要读它,
// 读 ``due.length`` 会在积压超过一页时少报。
export function listDueQuestions(now: string, limit?: number) {
  const qs = new URLSearchParams({ now });
  if (limit) qs.set("limit", String(limit));
  return api<{ due: DueQuestion[]; total?: number }>(
    `${BASE}/revisit/due?${qs.toString()}`,
  );
}

export function recallLedger(payload: {
  question_id: string;
  kinds?: string[];
  limit?: number;
  /** 账本回放(N1):ISO 时刻,只取该时刻账本已知的候选。省略 = 默认视图。 */
  as_of?: string;
}) {
  // 字面路径(不走 BASE):候选来源单一是可 grep 的门 —— 本文件此处是全仓
  // 唯一一处构造 /recall URL 的生产代码,组件必须走 recallLedger。
  return apiPost<RecallResponse>("/api/v1/research/graph/recall", {
    kinds: [...LEDGER_KINDS],
    ...payload,
  });
}

export function getGraphView(limit?: number) {
  const qs = limit ? `?limit=${limit}` : "";
  return api<GraphView>(`${BASE}/view${qs}`);
}

export function getEpisode(episodeId: string) {
  return api<EpisodeDetail>(
    `${BASE}/episodes/${encodeURIComponent(episodeId)}`,
  );
}

// 因子级生命周期推导(读侧消费口):链时间线 + 行动建议(建议 ≠ 流转)
export interface DerivationSuggestion {
  action: "retire" | "inspect";
  basis_episode_id: string;
  reason: string;
}

export interface SubjectDerivation {
  subject_type: string;
  subject_id: string;
  chain: {
    episode_id: string;
    occurred_at: string;
    status: string | null;
    verdict: string | null;
  }[];
  latest: {
    episode_id: string;
    occurred_at: string;
    status: string | null;
    verdict: string | null;
  } | null;
  retired_in_history: boolean;
  suggestions: DerivationSuggestion[];
}

export function getSubjectDerivation(
  subjectType: string,
  subjectId: string,
) {
  return api<SubjectDerivation>(
    `${BASE}/subjects/${encodeURIComponent(subjectType)}/${encodeURIComponent(subjectId)}/derivation`,
  );
}

export function transitionEpisode(
  episodeId: string,
  to_status: string,
  reason?: string,
) {
  return apiPost<{ episode: LedgerEpisode }>(
    `${BASE}/episodes/${encodeURIComponent(episodeId)}/lifecycle`,
    { to_status, ...(reason ? { reason } : {}) },
  );
}

// 账本完整性报告(integrity.py):ok/violations/scope_pressure/counts
export interface GraphViolation {
  rule: string;
  object_id: string;
  detail: string;
}

export interface GraphIntegrityReport {
  ok: boolean;
  violations: GraphViolation[];
  scope_pressure: GraphViolation[];
  counts: Record<string, number>;
}

export function getGraphIntegrity() {
  return api<GraphIntegrityReport>(`${BASE}/integrity`);
}

/** ① 传感器快照 —— GET /graph/sensor。settings.enabled=false 与
 * observations=[] 是两种不同状态(没开 vs 开了没发现),必须都显。 */
export interface SensorSnapshot {
  settings: {
    enabled: boolean;
    interval_minutes: number;
    question_limit: number;
  };
  observations: Array<{
    id: string;
    episode_id: string;
    // 后端直接透传 payload_json 字符串
    payload: string;
    observed_at: string | null;
    created_at: string;
    question_ids: string[];
  }>;
}

export function getSensorSnapshot(limit?: number) {
  const qs = limit ? `?limit=${limit}` : "";
  return api<SensorSnapshot>(`${BASE}/sensor${qs}`);
}

"use client";

import { api, apiPost, ApiError } from "@/lib/api-client";

const BASE = "/api/v1/decision-workflow";

export interface AnalyzeRequest {
  tree_id: string;
  input_data: Record<string, unknown>;
  input_id?: string;
  session_id?: string;
}

// 与后端 models.py Stage1Result/Stage2Result 对齐（diagnosis_or_decision 已解包）
export interface PaTraceNode {
  node_id: string;
  question: string;
  answer: string;
  bar_range: string | null;
  reason: string;
  branch: string;
}

export interface PaStage1Diagnosis {
  state?: string;
  gate_result?: "proceed" | "wait" | "unknown";
  trace?: PaTraceNode[];
  diagnosis_summary?: Record<string, unknown>;
  // LLM 自报的节点改判(后端 models.py NodeOverride;orchestrator 不应用,仅存证展示)
  node_overrides?: PaNodeOverride[];
}

export interface PaNodeOverride {
  node_id: string;
  new_answer: string;
  reason: string;
  bar_range: string | null;
}

export interface PaOrder {
  direction?: string;
  side?: string;
  entry?: number;
  entry_price?: number;
  stop_loss?: number;
  stop_price?: number;
  take_profit?: number;
  target_price?: number;
  [k: string]: unknown;
}

export interface PaStage2Decision {
  decision?: string;
  terminal_outcome?: "trade" | "wait" | "reject" | "proceed";
  order?: PaOrder | null;
  trace?: PaTraceNode[];
  decision_summary?: Record<string, unknown>;
  node_overrides?: PaNodeOverride[];
}

// 与后端 models.py StageRecord 对齐(原始 LLM I/O,audit/replay 用);各 stage 的 diagnosis_or_decision 类型不同,交叉收窄
export interface PaStageRecordBase {
  messages?: Array<Record<string, unknown>>;
  response?: Record<string, unknown>;
  trace?: PaTraceNode[];
  [k: string]: unknown;
}

export interface AnalyzeResponse {
  meta?: {
    timestamp?: string;
    input_id?: string;
    session_id?: string;
    // S6 demo mode: canned walk-through record (no real LLM) — 前端常驻标注
    demo?: boolean;
    [k: string]: unknown;
  };
  input_data?: {
    summary?: {
      symbol?: string;
      interval?: string;
      bars_total?: number;
      // 名不副实:真管线写的是 ms 数值(klines.db timestamp 列),demo/旧记录
      // 可能是 ISO 字符串。消费方需双吃。
      window_start_iso?: number | string;
      window_end_iso?: number | string;
      last_close?: number;
      atr14?: number;
      [k: string]: unknown;
    };
    snapshot?: {
      requested_bars?: number;
      bars_total?: number;
      fetched_at?: string;
    };
    [k: string]: unknown;
  };
  stage1?: PaStageRecordBase & { diagnosis_or_decision?: PaStage1Diagnosis };
  stage2?: (PaStageRecordBase & { diagnosis_or_decision?: PaStage2Decision }) | null;
  strategy_files_used?: string[];
  usage_total?: Record<string, number>;
  // W2 audit keys (T3a). outcome = win/loss 状态机判定,backtest_verification =
  // 回测回挂附件。Pydantic v2 响应此前丢弃这两键 — 现已在 API 层透传。
  outcome?: {
    status?: 'win' | 'loss' | 'expired' | 'timeout_open';
    pnl_r?: number;
    [k: string]: unknown;
  } | null;
  backtest_verification?: Record<string, unknown> | null;
  // Phase D 锚定追问:按追加顺序排列的问答条目。后端 append_followup 追加,
  // 这里读回的是持久化真值(不是前端本地拼的)。
  followups?: PaFollowUp[] | null;
  [k: string]: unknown;
}

// 一条锚定追问记录。at 是 epoch 秒(与后端 heartbeat/experience 一致,非 ISO)。
export interface PaFollowUp {
  question?: string;
  answer?: string;
  at?: number;
  usage?: Record<string, number>;
  // W3 混合追问:true = 服务端读了新鲜 bar(RECONSTRUCTED 现在读数)注入了
  // 该轮 prompt——审计痕迹,标出哪些回答看记录之外的数据。
  used_live?: boolean;
  [k: string]: unknown;
}

export interface TradingAnalyzeRequest {
  symbol?: string; // "BTC/USDT"
  interval?: string; // "1h"
  bars?: number; // 120
  market_type?: string; // "futures"
  trading_stance?: string | null;
  htf_interval?: string | null; // 高周期聚合(1h→4h, 4h→1d; 已属高周期传 null)
  input_id?: string;
  session_id?: string;
  // Phase D 演示一轮:跑真实两阶段管线但打演示标,结果不入审计表
  // (否则事后结果判定会把演示单当成真决策去复盘)。与 SELENE_PA_DEMO_MODE
  // 的 canned 降级是两条不同的路。
  demo?: boolean;
  // 增量:续接同品种/同周期最近一次诊断(先验 Stage1 + 此后到达的 K 线),
  // 并对新 K 线跑确定性连续性守卫。无先验/无新 K 线时后端返 422,不静默退回全量。
  incremental?: boolean;
}

// S7f 到价提醒:levels 缺失价位由调用方过滤,后端按 {id,status} 回执
export interface PriceAlertRequest {
  symbol: string;
  interval: string;
  direction?: string | null;
  input_id?: string | null;
  session_id?: string | null;
  levels: {
    entry?: number;
    stop_loss?: number;
    take_profit?: number;
  };
}

// HTTPException(detail={error, message}) → 可读文案；裸 string detail 直返
function httpErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const d = err.details as { error?: string; message?: string } | undefined;
    if (d?.message) {
      const code = d.error ? ` [${d.error}]` : "";
      return `${d.message}${code}`;
    }
    return err.message;
  }
  return err instanceof Error ? err.message : String(err);
}

export { httpErrorMessage };

export async function analyzeDecision(req: AnalyzeRequest): Promise<AnalyzeResponse> {
  return await apiPost<AnalyzeResponse>(`${BASE}/analyze`, req);
}

export async function getDecisionRecord(recordId: string): Promise<AnalyzeResponse> {
  return await api<AnalyzeResponse>(`${BASE}/records/${encodeURIComponent(recordId)}`);
}

// 最近诊断列表(结算回看)。行是 AnalyzeResponse 的扁平投影——完整记录
// 一条 GET /records/{id} 就在手边,列表只带"我等的那条后来怎么样了"。
export interface RecordListItem {
  record_id: string;
  timestamp: string;
  demo: boolean;
  symbol?: string | null;
  interval?: string | null;
  decision?: string | null;
  direction?: string | null;
  outcome?: AnalyzeResponse["outcome"];
}

export async function listRecords(params?: {
  symbol?: string;
  interval?: string;
  limit?: number;
}): Promise<RecordListItem[]> {
  const qs = new URLSearchParams();
  if (params?.symbol) qs.set("symbol", params.symbol);
  if (params?.interval) qs.set("interval", params.interval);
  if (params?.limit) qs.set("limit", String(params.limit));
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  const res = await api<{ records: RecordListItem[] }>(`${BASE}/records${suffix}`);
  return res.records;
}

export async function analyzeTrading(
  req: TradingAnalyzeRequest,
  signal?: AbortSignal,
): Promise<AnalyzeResponse> {
  return await apiPost<AnalyzeResponse>(`${BASE}/analyze-trading`, req, signal);
}

// 锚定追问:record_id 由服务端做锚,问题对着那条记录的冻结快照作答,
// 问答对追加回同一条记录。返回追加后的完整列表(渲染真值,不本地拼)。
export async function sendRecordFollowUp(
  recordId: string,
  question: string,
): Promise<{ record_id: string; followups: PaFollowUp[] }> {
  return await apiPost<{ record_id: string; followups: PaFollowUp[] }>(
    `${BASE}/records/${encodeURIComponent(recordId)}/followup`,
    { question },
  );
}

export async function createPriceAlert(body: PriceAlertRequest): Promise<{ id: number; status: string }> {
  return await apiPost<{ id: number; status: string }>(`${BASE}/price-alerts`, body);
}

// ---------------------------------------------------------------------------
// 心跳状态(US-1B)— 与后端 HeartbeatStatusResponse 对齐
// ---------------------------------------------------------------------------

// 一轮的产物:outcomes 三态计数 + 跑完时戳(epoch 秒,非 ISO)
export interface HeartbeatRound {
  day?: string;
  symbols?: number;
  outcomes?: { ok?: number; skipped?: number; failed?: number };
  attempts_today?: number;
  at?: number;
  [k: string]: unknown;
}

export interface HeartbeatStatus {
  enabled: boolean;
  last_round?: HeartbeatRound | null;
  attempts_today?: number;
  budget_day?: string | null;
  busy?: boolean;
  // 下一个诊断窗口的 epoch 秒 —— 由 config.hour_utc 算出的整点,不是 interval 推算
  next_fire_at?: number | null;
  config?: {
    hour_utc?: number;
    daily_max?: number;
    interval?: string;
    htf_interval?: string | null;
    [k: string]: unknown;
  } | null;
  [k: string]: unknown;
}

export async function getHeartbeatStatus(): Promise<HeartbeatStatus> {
  return await api<HeartbeatStatus>(`${BASE}/heartbeat/status`);
}

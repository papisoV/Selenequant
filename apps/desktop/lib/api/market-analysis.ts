"use client";

import { ApiError, fetchBare } from "@/lib/api-client";
import { getAccessToken } from "@/lib/auth";

// 后端 /api/v1/market-analysis 返回 BARE dict（不在 BARE_ENDPOINT_PATTERNS 内），
// 必须显式走 fetchBare，不能走会解包 {data} 的 api/apiPost（否则返回 null）。

const BASE = "/api/v1/market-analysis";

/** US-004: 交易倾向 4 档（自研命名，映射后端 Literal）。 */
export type TradeTemperament = "conservative" | "balanced" | "aggressive" | "very_aggressive";

export interface AnalyzeRequest {
  exchange: string;
  market_type: string;
  symbol: string;
  interval: string;
  background?: boolean;
  input_id?: string;
  session_id?: string;
  // US-004/005: 交易倾向入参 + 增量分析（后端 AnalyzeRequest 已支持，透传即可）
  trade_temperament?: TradeTemperament;
  incremental?: boolean;
}

export interface SignalPattern {
  name: string;
  bias: "bullish" | "bearish" | "neutral";
  confidence: number;
  kline_range?: string | null;
}

export interface Evidence {
  kind: string;
  key: string;
  statement: string;
  kline_range?: string | null;
}

export interface Stage1Diagnosis {
  market_regime: "trend_up" | "trend_down" | "range" | "spike";
  entry_gate: "proceed" | "wait" | "unknown";
  signal_patterns: SignalPattern[];
  trend_context: Record<string, unknown>;
  bar_features: Record<string, unknown>;
  evidence: Evidence[];
  diagnosis_summary: Record<string, unknown>;
}

export interface NextBarForecast {
  direction: "up" | "down" | "neutral";
  probability: number;
  note?: string | null;
}

/** G1: 完整交易设置（对应后端 TradeSetup，仅 propose 交易时存在）。 */
export interface TradeSetup {
  direction?: "long" | "short" | null;
  entry_price?: number | null;
  stop_loss_price?: number | null;
  take_profit_price?: number | null;
  take_profit_price_2?: number | null;
  risk_reward_ratio?: number | null;
  estimated_win_rate: number;
  confidence: number;
  trader_equation_passed: boolean;
  /** G4: 扁平决策路径步骤（前端 stepper 渲染）。 */
  decision_path: string[];
}

export interface Stage2Decision {
  decision: "limit" | "breakout" | "market" | "no_trade";
  dispatch_playbooks: string[];
  decision_summary: Record<string, unknown>;
  evidence: Evidence[];
  next_bar_forecast?: NextBarForecast | null;
  trade_setup?: TradeSetup | null;
}

export interface KlineBar {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface AnalysisRecord {
  meta: {
    timestamp?: string;
    record_id?: string;
    input_id?: string;
    session_id?: string;
    prev_record_id?: string; // US-005: 增量分析的上次记录 id
  };
  input_data?: {
    exchange?: string;
    market_type?: string;
    symbol?: string;
    interval?: string;
    klines?: KlineBar[];
  };
  stage1?: Stage1Diagnosis;
  stage2?: Stage2Decision | null;
  dispatch_playbooks?: string[];
  usage_total?: Record<string, number>;
  exception?: { type: string; message: string };
  [k: string]: unknown;
}

export async function analyzeMarket(req: AnalyzeRequest): Promise<AnalysisRecord> {
  return fetchBare<AnalysisRecord>(`${BASE}/analyze`, {
    method: "POST",
    body: JSON.stringify(req),
  });
}

// ---------------------------------------------------------------------------
// US-003 SSE 流式分析：fetch + ReadableStream 解析（POST 不支持 EventSource）
// ---------------------------------------------------------------------------

export type MarketAnalysisEvent =
  | { type: "analyze_start" }
  | { type: "stage1_start"; attempt: number }
  | { type: "stage1_text"; delta: string }
  | { type: "stage1_done"; attempt: number; usage?: Record<string, number> }
  | { type: "stage1_retry"; attempt: number; message?: string }
  | { type: "stage2_skipped"; gate?: string }
  | { type: "stage2_start"; attempt: number }
  | { type: "stage2_text"; delta: string }
  | { type: "stage2_done"; attempt: number; usage?: Record<string, number> }
  | { type: "stage2_retry"; attempt: number; message?: string }
  | { type: "analyze_done"; record: AnalysisRecord }
  | { type: "analyze_fail"; error: { error: string; message?: string; field?: string; layer?: string } };

/** 事件帧解析：按 ``data: {json}\n\n`` 切分（容忍跨 chunk 的帧边界）。 */
function parseSseFrame(frame: string): MarketAnalysisEvent | null {
  const dataLine = frame.split("\n").find((line) => line.startsWith("data: "));
  if (!dataLine) return null;
  try {
    return JSON.parse(dataLine.slice("data: ".length)) as MarketAnalysisEvent;
  } catch {
    return null;
  }
}

/**
 * 流式两阶段分析：POST /analyze/stream，逐帧回调 onEvent。
 * 收到 analyze_done 时 resolve record；收到 analyze_fail / 网络断时抛 ApiError
 * （analyze_fail 先回调再抛，UI 可保留已流式文本）。
 */
export async function streamAnalyzeMarket(
  req: AnalyzeRequest,
  onEvent: (ev: MarketAnalysisEvent) => void,
  signal?: AbortSignal,
): Promise<AnalysisRecord> {
  const token = getAccessToken();
  const res = await fetch(`${BASE}/analyze/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(req),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new ApiError(`HTTP ${res.status} ${res.statusText}`, res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let record: AnalysisRecord | null = null;

  const dispatchFrames = (chunk: string): void => {
    buffer += chunk;
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const ev = parseSseFrame(frame);
      if (!ev) continue;
      onEvent(ev);
      if (ev.type === "analyze_done") record = ev.record;
      if (ev.type === "analyze_fail") {
        throw new ApiError(ev.error.message ?? "分析失败", 500, {
          code: ev.error.error,
          details: ev.error,
        });
      }
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    dispatchFrames(decoder.decode(value, { stream: true }));
  }
  dispatchFrames(decoder.decode()); // flush 残留尾部
  dispatchFrames(""); // 处理 buffer 中未以 \n\n 结尾的最后一帧

  if (!record) {
    throw new ApiError("分析流异常终止（未收到结果）", 500);
  }
  return record;
}

// ---------------------------------------------------------------------------
// US-302 Free Chat 自由追问
// ---------------------------------------------------------------------------

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  ts?: string;
}

export interface ChatReply {
  record_id: string;
  reply: string;
  messages: ChatMessage[];
}

export async function chatMessage(recordId: string, message: string): Promise<ChatReply> {
  return fetchBare<ChatReply>(`${BASE}/chat`, {
    method: "POST",
    body: JSON.stringify({ record_id: recordId, message }),
  });
}

export async function getChatHistory(recordId: string): Promise<{ record_id: string; messages: ChatMessage[] }> {
  return fetchBare<{ record_id: string; messages: ChatMessage[] }>(`${BASE}/chat/${encodeURIComponent(recordId)}`);
}

// ---------------------------------------------------------------------------
// US-304 演示回放 / 导出 / 通知
// ---------------------------------------------------------------------------

export async function listRecentRecords(symbol?: string, limit = 20): Promise<{ records: AnalysisRecord[] }> {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (symbol) qs.set("symbol", symbol);
  return fetchBare<{ records: AnalysisRecord[] }>(`${BASE}/records?${qs.toString()}`);
}

export async function getRecord(recordId: string): Promise<AnalysisRecord> {
  return fetchBare<AnalysisRecord>(`${BASE}/records/${encodeURIComponent(recordId)}`);
}

export async function sendNotify(recordId?: string, message?: string): Promise<Record<string, string>> {
  return fetchBare<Record<string, string>>(`${BASE}/notify`, {
    method: "POST",
    body: JSON.stringify({ record_id: recordId, message }),
  });
}

// CSV 导出：直接以文本拉取 + 浏览器下载（不走 JSON 解析的 fetchBare）
export async function downloadRecordCsv(recordId: string): Promise<void> {
  const token = getAccessToken();
  const res = await fetch(`${BASE}/records/${encodeURIComponent(recordId)}/export.csv`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} ${res.statusText}`);
  }
  const text = await res.text();
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${recordId}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export type EvalDirection = "up" | "down" | "flat";

export interface ForecastEvalRow {
  record_id: string;
  forecast: EvalDirection | null;
  baseline: EvalDirection;
  actual: EvalDirection;
  forecast_hit: boolean | null;
  baseline_hit: boolean;
  probability?: number | null;
  note?: string | null;
}

export interface ForecastEvalReport {
  symbol: string;
  interval: string;
  samples_total: number;
  samples_with_forecast: number;
  hit_rate_forecast: number | null;
  hit_rate_baseline: number | null;
  rows: ForecastEvalRow[];
}

export async function getForecastEval(
  symbol: string,
  interval: string,
  limit = 100,
): Promise<ForecastEvalReport> {
  const qs = new URLSearchParams({ symbol, interval, limit: String(limit) });
  return fetchBare<ForecastEvalReport>(`${BASE}/records/forecast-eval?${qs.toString()}`);
}

// ---------------------------------------------------------------------------
// /records/ic-panel + /records/return-report — 跨截面 IC / 多空回报面板
// 后端返回 BARE dict（series → {dates, values}，NaN → null）。
// ---------------------------------------------------------------------------

export interface SeriesPayload {
  dates: string[];
  values: (number | null)[];
}

export interface HeatmapPayload {
  years: string[];
  months: string[];
  z: (number | null)[][];
}

export interface SectorPayload {
  groups: string[];
  contribs: (number | null)[];
}

export interface SnapshotMeta {
  episode_id: string;
  occurred_at: string;
  /** GET 返回带 boundary;mine 返回带 key。 */
  boundary_start?: string;
  boundary_end?: string;
  key?: string;
  n_rows: number;
  /** 评估定义血缘(schema v2):IC 对着什么收益算的,如 "close[t+1]/close[t] - 1"。 */
  target_definition?: string;
  /** derived_from 祖先链(子→父,最近祖先在前);首跑无边为空数组。 */
  lineage?: { episode_id: string; occurred_at: string }[];
}

/** 快照契约:GET 只读快照;无快照 → status=no_snapshot + 空 series,拒绝模拟。 */
export interface ICPanelReport {
  status?: "ok" | "no_snapshot";
  snapshot?: SnapshotMeta;
  symbols: string[];
  interval: string;
  factor: string;
  period: number;
  rolling_ic: SeriesPayload;
  rolling_ir: SeriesPayload;
  autocorr: SeriesPayload;
  ic_distribution: Record<string, number | null>;
  qq_stats: Record<string, number | null>;
  monthly_heatmap: HeatmapPayload;
  sector_ic: SectorPayload;
}

export interface MineICPanelRequest {
  symbols: string;
  interval?: string;
  exchange?: string;
  market_type?: string;
  factor?: string;
  period?: number;
  limit?: number;
  rolling_window?: number;
  question_id?: string;
}

export interface ReturnReport {
  symbols: string[];
  interval: string;
  factor: string;
  period: number;
  n_bins: number;
  cum_ret_ls: SeriesPayload;
  cum_ret_net_ls: SeriesPayload;
  turnover_ls: SeriesPayload;
  avg_turnover_ls: number | null;
  mean_ret: number | null;
  stats_ls: Record<string, number | null>;
  df_indus: { industries: string[]; returns: (number | null)[] } | null;
}

export interface ICPanelParams {
  symbols?: string;
  interval?: string;
  exchange?: string;
  market_type?: string;
  factor?: string;
  period?: number;
  rolling_window?: number;
  limit?: number;
}

export interface ReturnReportParams {
  symbols?: string;
  interval?: string;
  exchange?: string;
  market_type?: string;
  factor?: string;
  period?: number;
  bins?: number;
  epoch?: number;
  limit?: number;
}

function _qs(params: object): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null) qs.set(k, String(v));
  }
  return qs.toString();
}

export async function getICPanel(params: ICPanelParams = {}): Promise<ICPanelReport> {
  return fetchBare<ICPanelReport>(`${BASE}/records/ic-panel?${_qs(params)}`);
}

/** 实时计算 + 落库快照(唯一写路径)。返回完整报告 + snapshot 元信息。 */
export async function mineICPanel(req: MineICPanelRequest): Promise<ICPanelReport> {
  return fetchBare<ICPanelReport>(`${BASE}/records/ic-panel/mine`, {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export async function getReturnReport(params: ReturnReportParams = {}): Promise<ReturnReport> {
  return fetchBare<ReturnReport>(`${BASE}/records/return-report?${_qs(params)}`);
}

"use client";

import { api, apiPost } from "@/lib/api-client";

const ALPHA_BASE = "/api/v1/alpha";

export interface DeploySavedStrategyParams {
  symbol?: string;
  interval?: string;
  profile?: string;
}

export async function deploySavedStrategy(
  strategyName: string,
  params?: DeploySavedStrategyParams,
): Promise<void> {
  await apiPost(`${ALPHA_BASE}/saved-strategies/${encodeURIComponent(strategyName)}/deploy`, {
    symbol: params?.symbol ?? "BTC/USDT",
    interval: params?.interval ?? "15m",
    profile: params?.profile ?? "default",
  });
}

export interface FormulaDetail {
  formula_id: string;
  formula_tokens: number[];
  formula_readable: string;
  best_score?: number;
  training_history?: Record<string, unknown>;
}

export async function getFormulaDetail(formulaId: string): Promise<FormulaDetail> {
  return await api<FormulaDetail>(`${ALPHA_BASE}/formulas/${encodeURIComponent(formulaId)}`);
}

/** 本地产物公式清单(GET /formulas;formula_id 是 ic-decay/deploy 的寻址键)。 */
export interface MinedFormulaInfo {
  formula_id: string;
  formula_tokens: number[];
  formula_readable: string;
  best_score: number;
  created_at: number;
}

export async function listFormulas(): Promise<MinedFormulaInfo[]> {
  const data = await api<{ formulas: MinedFormulaInfo[] }>(`${ALPHA_BASE}/formulas`);
  return data.formulas ?? [];
}

export interface TrainingHistory {
  step: number[];
  avg_reward: number[];
  best_score: (number | null)[];
  stable_rank: number[];
  /** 与 stable_rank 同分支 append 的 step 值(US-001 契约):zip 即 [(step, rank)];旧 checkpoint 无此键 */
  rank_step?: number[];
  /** 每步 batch rewards std(与 step 同频稠密);旧数据无此键 */
  reward_std?: number[];
}

export interface TrainStatus {
  session_id: string;
  status: "starting" | "running" | "stopping" | "completed" | "failed" | "stopped";
  step: number;
  best_score?: number;
  train_steps?: number;
  data_source?: "real" | "synthetic" | "parquet";
  fallback_reason?: string;
  elapsed_seconds: number;
  history?: TrainingHistory;
  /** 当前最优公式解码(引擎线程随挖矿推进实时更新;尚无最优时为空) */
  formula_readable?: string | null;
}

export async function getTrainStatus(sessionId: string): Promise<TrainStatus> {
  return await api<TrainStatus>(`${ALPHA_BASE}/train/${encodeURIComponent(sessionId)}/status`);
}

export interface TrainLogs {
  session_id: string;
  logs: string[];
}

export async function getTrainLogs(sessionId: string): Promise<TrainLogs> {
  return await api<TrainLogs>(`${ALPHA_BASE}/train/${encodeURIComponent(sessionId)}/logs`);
}

export interface MiningSession {
  session_id: string;
  status: "starting" | "running" | "stopping" | "completed" | "failed" | "stopped";
  step: number;
  best_score?: number;
  train_steps?: number;
  started_at?: number;
}

export async function listMiningSessions(): Promise<MiningSession[]> {
  const data = await api<{ sessions: MiningSession[] }>(`${ALPHA_BASE}/mining`);
  return data.sessions ?? [];
}

/** klines.db 某分区覆盖情况(刀B 存量可见)。 */
export interface KlineCoverage {
  count: number;
  latest_timestamp: number | null;
  latest_iso: string | null;
}

export async function getKlineCoverage(params: {
  exchange: string;
  market_type: string;
  symbol: string;
  interval: string;
}): Promise<KlineCoverage> {
  // GET(读语义):POST 会经 api-client 的 abortAllPendingGets 掐死同页并发的 GET
  // ——挂载期探测曾把验证报告的 GET 一起干掉,404 被掩盖成 abort 错误。
  const qs = new URLSearchParams(params);
  return await api<KlineCoverage>(`${ALPHA_BASE}/kline-coverage?${qs}`);
}

/** klines.db 缺数据时一键补齐(REST 回填,默认 365 天)。 */
export async function syncKlinePartition(params: {
  exchange: string;
  market_type: string;
  symbol: string;
  interval: string;
  days?: number;
}): Promise<{ written: number }> {
  return await apiPost<{ written: number }>(`${ALPHA_BASE}/kline-sync`, params);
}

export interface ResumeResult {
  session_id: string;
  status: string;
  resumed_from_step: number;
}

export async function resumeTraining(sessionId: string): Promise<ResumeResult> {
  return await apiPost<ResumeResult>(`${ALPHA_BASE}/train/${encodeURIComponent(sessionId)}/resume`);
}

/** 批量训练:POST /train/batch 按 (symbol,interval) 笛卡尔积 fan-out,每对一个会话。 */
export interface BatchTrainParams {
  symbols: string[];
  intervals: string[];
  max_concurrency?: number;
  train_steps?: number;
  batch_size?: number;
  use_lord?: boolean;
  exchange?: string;
  market_type?: string;
  /** parquet 文件路径(~/.selenespace/data 或 demo/data 下);服务端 _batch_session_request 自动透传到每会话 */
  data_file?: string;
}

export interface BatchTrainResult {
  batch_id: string;
  session_ids: string[];
  status: string;
  max_concurrency: number;
}

export async function startBatchTraining(params: BatchTrainParams): Promise<BatchTrainResult> {
  return await apiPost<BatchTrainResult>(`${ALPHA_BASE}/train/batch`, {
    train_steps: 100,
    batch_size: 256,
    use_lord: true,
    exchange: "binance",
    market_type: "futures",
    ...params,
  });
}

/** GET /train/batch/{id}/status 聚合行:每会话一行,含所属 symbol/interval。 */
export interface BatchSessionEntry {
  session_id: string;
  status: string;
  step: number;
  train_steps?: number;
  best_score?: number | null;
  symbol?: string;
  interval?: string;
  error?: string;
  /** 当前最优公式解码(挖矿推进中实时更新;尚无最优时缺省) */
  formula_readable?: string;
}

export interface BatchStatus {
  batch_id: string;
  status: string;
  session_count: number;
  max_concurrency?: number;
  started_at?: number;
  sessions: BatchSessionEntry[];
}

export async function getBatchStatus(batchId: string): Promise<BatchStatus> {
  return await api<BatchStatus>(`${ALPHA_BASE}/train/batch/${encodeURIComponent(batchId)}/status`);
}

/** 逐条 T4 独立裁决(L2):POST /formulas/{id}/validate → per-formula verdict。 */
export interface FormulaVerdict {
  formula_id: string;
  verdict: string;
  decision: "accept" | "hold" | "reject" | "defer" | string;
  reason: string;
  mean_ic?: number | null;
  data_source?: string;
  stored_at?: string;
}

export async function validateFormula(formulaId: string): Promise<FormulaVerdict> {
  return await apiPost<FormulaVerdict>(`${ALPHA_BASE}/formulas/${encodeURIComponent(formulaId)}/validate`, {});
}

export async function getFormulaValidation(formulaId: string): Promise<FormulaVerdict> {
  return await api<FormulaVerdict>(`${ALPHA_BASE}/formulas/${encodeURIComponent(formulaId)}/validation`);
}

export interface FormulaExplain {
  session_id: string;
  source: "llm" | "rule";
  explanation: string;
  summary: {
    step_count: number;
    best_score: number | null;
    recent_avg_reward: number;
  };
}

export async function getFormulaExplain(sessionId: string): Promise<FormulaExplain> {
  return await apiPost<FormulaExplain>(`${ALPHA_BASE}/train/${encodeURIComponent(sessionId)}/explain`);
}

/** 已保存策略 from GET /saved-strategies(回测 tab 的目标源之一)。 */
export interface SavedStrategy {
  name: string;
  version: string;
  formula_readable: string;
  formula_tokens: number[];
  profiles: Array<Record<string, unknown>>;
}

export async function listSavedStrategies(): Promise<SavedStrategy[]> {
  const data = await api<{ strategies: SavedStrategy[] }>(`${ALPHA_BASE}/saved-strategies`);
  return data.strategies ?? [];
}

export interface StrategyBacktestParams {
  symbol?: string;
  interval?: string;
  exchange?: string;
  market_type?: string;
  initial_capital?: number;
}

/** Rich 回测:8 指标 + equity_curve + rolling_sharpe + trades(POST /saved-strategies/{name}/backtest)。 */
export async function backtestSavedStrategy(
  strategyName: string,
  params?: StrategyBacktestParams,
): Promise<BacktestDetail> {
  return await apiPost<BacktestDetail>(
    `${ALPHA_BASE}/saved-strategies/${encodeURIComponent(strategyName)}/backtest`,
    {
      symbol: params?.symbol ?? "BTC/USDT",
      interval: params?.interval ?? "15m",
      exchange: params?.exchange ?? "binance",
      market_type: params?.market_type ?? "futures",
      initial_capital: params?.initial_capital ?? 10000,
    },
  );
}

/** Lean 回测(POST /formulas/{id}/backtest,query 分区参数):公式级信号分布,无净值/滚动曲线。 */
export interface MinedFormulaBacktest {
  formula_id: string;
  formula_readable: string;
  latest_score: number;
  /** 信号取值(字符串键,来自 value_counts) → 出现次数;即公式源信号分布。 */
  signal_counts: Record<string, number>;
  total_bars: number;
  source: "real" | "synthetic";
  symbol: string;
  interval: string;
  exchange: string;
  market_type: string;
}

export interface FormulaBacktestParams {
  symbol?: string;
  exchange?: string;
  market_type?: string;
  interval?: string;
}

export async function backtestMinedFormula(
  formulaId: string,
  params?: FormulaBacktestParams,
): Promise<MinedFormulaBacktest> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null) qs.set(k, String(v));
  }
  const query = qs.toString();
  return await apiPost<MinedFormulaBacktest>(
    `${ALPHA_BASE}/formulas/${encodeURIComponent(formulaId)}/backtest${query ? `?${query}` : ""}`,
  );
}

/** Saved-strategy backtest response (deep metrics + equity curve + rolling sharpe). */
export interface BacktestDetail {
  strategy_name: string;
  formula_readable: string;
  symbol: string;
  interval: string;
  total_return: number;
  annual_return: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  max_drawdown: number;
  win_rate: number;
  total_trades: number;
  profit_factor: number;
  equity_curve: number[];
  rolling_sharpe: number[];
  trades: Array<{ pnl: number; stop: boolean; side: string }>;
}

// ---------------------------------------------------------------------------
// P2: artifact management (zip export / import / listing)
// ---------------------------------------------------------------------------

export interface ArtifactInfo {
  name: string;
  size: number;
  modified: number;
  kind: "formula" | "history" | "checkpoint";
}

export async function listArtifacts(): Promise<ArtifactInfo[]> {
  const data = await api<{ artifacts: ArtifactInfo[] }>(`${ALPHA_BASE}/artifacts`);
  return data.artifacts ?? [];
}

export interface ExportResult {
  zip_base64: string | null;
  count: number;
  files: string[];
}

export async function exportArtifacts(): Promise<ExportResult> {
  const data = await apiPost<ExportResult>(`${ALPHA_BASE}/artifacts/export`);
  return data;
}

export interface ImportResult {
  imported: string[];
  skipped: string[];
}

export interface ImportArtifactPayload {
  zip_base64?: string;
  raw_name?: string;
  raw_base64?: string;
}

/** Import a zip bundle (zip_base64) or a single raw file (.pt/.json, raw_*). */
export async function importArtifacts(payload: ImportArtifactPayload): Promise<ImportResult> {
  return await apiPost<ImportResult>(`${ALPHA_BASE}/artifacts/import`, payload);
}

// ---------------------------------------------------------------------------
// T4 验证报告 — 验证 tab 数据通路(GET /validation-report)
// 结构镜像 ValidationReport.to_dict()(validation_protocol.py L378),不做形状裁剪。
// ---------------------------------------------------------------------------

/** 单折 walk-forward 几何 + 指标(FoldMetrics.to_dict())。 */
export interface FoldMetrics {
  fold_index: number;
  train_start: number;
  train_end: number;
  test_start: number;
  test_end: number;
  train_rank_ic: number | null;
  train_ls_sharpe: number | null;
  train_turnover: number | null;
  oos_rank_ic: number | null;
  oos_ls_sharpe: number | null;
  oos_turnover: number | null;
  per_symbol_oos_ic: Record<string, number | null>;
  oos_n_obs: number;
}

/** solo 判定档:够不着样本下限是 INSUFFICIENT_DATA(无证据 ≠ 指控)。 */
export type SoloVerdict = "PASS" | "SUSPICIOUS" | "INVALID" | "INSUFFICIENT_DATA";

/** 逐 symbol 独立绝对诊断(SymbolSolo.to_dict())。 */
export interface SymbolSolo {
  symbol: string;
  ann_ret: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  verdict: SoloVerdict;
  reason: string;
}

/** 去偏 Sharpe / PBO 审计(OverfitReport.to_dict())。 */
export interface OverfitReport {
  verdict: string;
  passed: boolean;
  observed_sharpe_annual: number;
  skew: number;
  kurtosis: number;
  n_obs: number;
  n_trials: number;
  deflated_sharpe_ratio: number;
  deflation_benchmark_sr0_annual: number;
  psr_vs_zero: number;
  haircut_sharpe_annual: number;
  haircut_pct: number;
  minimum_track_record_length: number;
  pbo: number | null;
}

/** 单数据集 walk-forward 结果(DatasetValidation.to_dict())。 */
export interface DatasetValidation {
  dataset_key: string;
  n_dates: number;
  n_symbols: number;
  effective_folds: number;
  train_rank_ic: number | null;
  oos_rank_ic: number | null;
  subinterval_agreement: number | null;
  multi_symbol_agreement: number | null;
  oos_ls_sharpe: number | null;
  oos_turnover: number | null;
  /** 成本倍数 → 年化 LS Sharpe;总为 None 时 = 本次未开启成本压力档 */
  cost_stress: Record<string, number> | null;
  n_oos_obs: number;
  overfit: OverfitReport | null;
  solo: Record<string, SymbolSolo>;
  folds: FoldMetrics[];
}

/** 单公式判决 + 逐折明细 + 池化汇总(FormulaValidation.to_dict())。 */
export interface FormulaValidation {
  readable: string;
  tokens: number[];
  canonical_hash: string | null;
  verdict: string;
  /** T4 deploy 门控判据(与后端 _ACTION_BY_VERDICT 同语义):accept/hold/reject/defer */
  decision: string;
  reason: string;
  mean_ic: number | null;
  rolling_ic: Array<number | null>;
  train_mean_ic: number | null;
  subinterval_agreement: number | null;
  multi_symbol_agreement: number | null;
  dsr: number | null;
  pbo: number | null;
  cost_stress: Record<string, number> | null;
  n_oos_obs: number;
  datasets: Record<string, DatasetValidation>;
}

export interface ValidationReportConfig {
  n_trials?: number;
  fold_config?: { min_train: number; fold_size: number; n_folds: number };
  thresholds?: Record<string, number>;
  periods_per_year?: number;
  datasets?: Record<string, { n_symbols: number; symbols: string[] }>;
}

export interface ValidationReportData {
  generated_at: string | null;
  config: ValidationReportConfig;
  formulas: FormulaValidation[];
}

export interface ValidationReportEnvelope {
  report: ValidationReportData;
  generated_at: string | null;
}

/** 404 = 报告未生成(语义显形,不是空数据装成功)。 */
export async function getValidationReport(): Promise<ValidationReportEnvelope> {
  return await api<ValidationReportEnvelope>(`${ALPHA_BASE}/validation-report`);
}

/** 部署公式;T4 门控拒绝时抛 403,ApiError.message 即裁定原因。 */
export async function deployFormula(formulaId: string): Promise<void> {
  await apiPost(`${ALPHA_BASE}/formulas/${encodeURIComponent(formulaId)}/deploy`);
}

// ---------------------------------------------------------------------------
// IC 衰减 — 验证 tab 图表通路(GET /formulas/{id}/ic-decay)
// ---------------------------------------------------------------------------

export interface IcDecayData {
  formula_id: string;
  formula_readable: string;
  symbol: string;
  interval: string;
  source: "real" | "synthetic";
  max_lag: number;
  /** lag(bar 数,字符串键) → IC 值 */
  ic_by_lag: Record<string, number>;
  /** IC 掉到峰值一半的 lag;永不衰减时 null */
  half_life: number | null;
  peak_ic: number | null;
  peak_lag: number | null;
  turnover: number | null;
  turnover_adjusted_ic: number | null;
  is_decayed: boolean;
}

export interface IcDecayParams {
  symbol?: string;
  exchange?: string;
  market_type?: string;
  interval?: string;
  max_lag?: number;
}

/** 404 = 无此公式;422 = 该分区无可用 kline/评分(语义显形,不假造零值)。 */
export async function getIcDecay(formulaId: string, params?: IcDecayParams): Promise<IcDecayData> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null) qs.set(k, String(v));
  }
  const query = qs.toString();
  return await api<IcDecayData>(
    `${ALPHA_BASE}/formulas/${encodeURIComponent(formulaId)}/ic-decay${query ? `?${query}` : ""}`,
  );
}

// ---------------------------------------------------------------------------
// B4 监控传感器 — 监控 tab 数据通路(POST/GET /monitor/* 六端点族)
// 四元组 = (klines.db, symbol, interval, factor);read 是 stale-aware 快照。
// ---------------------------------------------------------------------------

/** B4 监控四元组(register/list 返回含 source;提交时只需后三者)。 */
export interface MonitorQuad {
  source?: string;
  symbol: string;
  interval: string;
  factor: string;
}

export interface MonitorQuadPayload {
  symbol: string;
  interval: string;
  factor: string;
}

/** 单次读数:value 为 B4 背景因子得分;stale=true 代表缓存过期、后台已安排刷新。 */
export interface MonitorReading {
  value: number | null;
  computed_at: string | null;
  last_bar_ts: number | null;
  stale: boolean;
  /** 本轮是否发起同步重算(冷缓存首读才有)。 */
  recomputed?: boolean;
}

export async function monitorRegister(quad: MonitorQuadPayload): Promise<MonitorQuad> {
  return await apiPost<MonitorQuad>(`${ALPHA_BASE}/monitor/register`, quad);
}

export async function monitorUnregister(quad: MonitorQuadPayload): Promise<void> {
  await apiPost(`${ALPHA_BASE}/monitor/unregister`, quad);
}

export async function monitorList(): Promise<MonitorQuad[]> {
  const data = await api<MonitorQuad[]>(`${ALPHA_BASE}/monitor/list`);
  return Array.isArray(data) ? data : [];
}

/** 同步读数:冷缓存首读会阻塞到计算完;有缓存则 stale-aware 快速返回。 */
export async function monitorRead(quad: MonitorQuadPayload): Promise<MonitorReading> {
  return await apiPost<MonitorReading>(`${ALPHA_BASE}/monitor/read`, quad);
}

export interface MonitorTaskStatus {
  task_id: string;
  status: "pending" | "running" | "completed" | "failed";
  symbol?: string;
  interval?: string;
  factor?: string;
  created_at?: number;
  error?: string;
}

/** 异步重算:立即返回 task_id,后续 GET /monitor/task/{id} 轮询。 */
export async function monitorReadAsync(quad: MonitorQuadPayload): Promise<MonitorTaskStatus> {
  return await apiPost<MonitorTaskStatus>(`${ALPHA_BASE}/monitor/read/async`, quad);
}

export async function monitorTaskStatus(taskId: string): Promise<MonitorTaskStatus> {
  return await api<MonitorTaskStatus>(`${ALPHA_BASE}/monitor/task/${encodeURIComponent(taskId)}`);
}

export interface ParquetFileInfo {
  name: string;
  path: string;
  size_bytes: number;
  modified_at: number;
}

export async function listDataFiles(): Promise<ParquetFileInfo[]> {
  const data = await api<{ files: ParquetFileInfo[] }>(`${ALPHA_BASE}/data-files`);
  return data.files ?? [];
}

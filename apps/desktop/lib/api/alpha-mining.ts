"use client";

// 确定性遗传公式搜索(alpha-mining)产物数据层。
//
// 数据契约:FastAPI local-api 按 research.py tuning-results 同款 house pattern
// 读取 on-disk JSON 产物并通过 APIResponse 信封返回:
//   GET /api/v1/research/alpha-mining/top-formulas → { data: TopFormulasResult }
//   GET /api/v1/research/alpha-mining/validation   → { data: ValidationReport }
// 两个文件由同一 pipeline 先后写出(genetic_search runner → validation_protocol),
// 可能部分缺失;组件对两个端点独立 fetch,任一缺失时降级展示而非整页崩溃。

export const TOP_FORMULAS_ENDPOINT = "/api/v1/research/alpha-mining/top-formulas";
export const VALIDATION_ENDPOINT = "/api/v1/research/alpha-mining/validation";

// ---- top_formulas.json (runner 产物) ----

export interface TopFormula {
  rank: number;
  readable: string;
  tokens: number[];
  canonical_hash: string;
  valid: boolean;
  score: number;
  rank_ic: number;
  ls_net_sharpe: number;
  turnover: number;
  ic_stability: number;
  subperiod_ic_consistency: number;
  oos_rank_ic: number;
  oos_ls_net_sharpe: number;
}

/** runner 记录的实际取样区间。旧产物无此字段(生成于加入时间区间参数之前),
 *  读取方须容忍 undefined —— 此时只有 bars_per_symbol 的"最近 N 根"语义。 */
export interface MiningWindow {
  /** 命令行原始入参(可能为 null = 该侧不限制) */
  requested_start: string | null;
  requested_end: string | null;
  /** 落库的真实首末 bar(UTC ISO) */
  start: string;
  end: string;
  /** 最短标的的日历跨度(天)——G6 要求 ≥365 */
  span_days: number;
  /** true = 按时间窗口取样(忽略了 bars);false = 传统"最近 N 根" */
  selects_window: boolean;
}

export interface TopFormulasResult {
  symbols: string[];
  interval: string;
  bars_per_symbol: number;
  /** 旧产物缺失 */
  market_type?: string;
  /** 旧产物缺失 */
  window?: MiningWindow | null;
  seed: number;
  eval_budget: number;
  evaluations_used: number;
  generations: number;
  stopped_reason: string;
  top_formulas: TopFormula[];
}

// ---- validation_report.json (validation_protocol 产物) ----

export type Verdict = "VALID" | "SUSPICIOUS" | "INVALID" | string;

export interface OverfitAudit {
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

export interface ValidationFold {
  fold_index: number;
  train_start: number;
  train_end: number;
  test_start: number;
  test_end: number;
  train_rank_ic: number;
  train_ls_sharpe: number;
  train_turnover: number;
  oos_rank_ic: number;
  oos_ls_sharpe: number;
  per_symbol_oos_ic: Record<string, number | null>;
  oos_n_obs: number;
}

export interface ValidationDataset {
  dataset_key: string;
  n_dates: number;
  n_symbols: number;
  effective_folds: number;
  train_rank_ic: number;
  oos_rank_ic: number;
  subinterval_agreement: number;
  multi_symbol_agreement: number;
  oos_ls_sharpe: number;
  n_oos_obs: number;
  overfit: OverfitAudit;
  folds: ValidationFold[];
}

export interface ValidationFormula {
  readable: string;
  tokens: number[];
  canonical_hash: string;
  verdict: Verdict;
  decision: string;
  reason: string;
  mean_ic: number;
  rolling_ic: number[];
  train_mean_ic: number;
  subinterval_agreement: number;
  multi_symbol_agreement: number;
  dsr: number;
  pbo: number | null;
  n_oos_obs: number;
  datasets: Record<string, ValidationDataset>;
}

export interface ValidationReport {
  generated_at: string;
  config: {
    n_trials: number;
    fold_config: { min_train: number; fold_size: number; n_folds: number };
    thresholds: {
      oos_ic_min: number;
      subinterval_agreement_min: number;
      multi_symbol_agreement_min: number;
      dsr_threshold: number;
    };
    periods_per_year: number;
    datasets: Record<string, { n_symbols: number; symbols: string[] }>;
    data_source: string;
  };
  formulas: ValidationFormula[];
  summary: Record<string, number>;
}

// ---- 查询辅助 ----

/** 用 canonical_hash 把验证判定表 join 到 top-N 公式上(缺判返回 null) */
export function findValidationByHash(
  report: ValidationReport | null,
  hash: string,
): ValidationFormula | null {
  if (!report) return null;
  return report.formulas.find((f) => f.canonical_hash === hash) ?? null;
}

/** 取验证公式的首个数据集(当前管线单数据集,预留多数据集) */
export function firstValidationDataset(
  formula: ValidationFormula | null,
): ValidationDataset | null {
  if (!formula) return null;
  const keys = Object.keys(formula.datasets);
  if (keys.length === 0) return null;
  return formula.datasets[keys[0]] ?? null;
}

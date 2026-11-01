"use client";

// A股大盘总览 —— GET /api/v1/ashare/overview。
//
// 服务端(build_overview)fail-open:200 + status∈{ok,empty,degraded},永不 5xx。
// **单位契约与 TSP 不同,不要照搬**:本 payload 里所有百分比都是**百分数**
// (1.23 表示 +1.23%),无例外。而异动偏离(packages/ashare/abnormal.py)那侧
// 的 value/threshold/closeness 是**小数** —— 两处混用是本页面最容易犯的错。
//
// 数据诚实条款(前端不得与之矛盾):
//   · 全市场空截面 → radar: [](**不是**全 0 轴)、emotion.score: null、
//     emotion.label: "无数据"。零轴平均会算出一个"42/震荡"的假温度。
//   · quote_source: "snapshot" | "daily" —— 后者是日线回退,必须对读者可见。
//   · status: "degraded" + reason: "partial_coverage" —— 覆盖度不足,仍可读但要标注。
//   · amount.available === false → 成交额不可得,显示「—」而不是 0。

export interface OverviewIndex {
  symbol: string;
  name: string;
  last_price: number | null;
  prev_close: number | null;
  change_amount: number | null;
  /** 百分数(+0.62 表示 +0.62%)。 */
  change_pct: number | null;
  high: number | null;
  low: number | null;
  amount: number | null;
  date: string | null;
  time: string | null;
}

export interface OverviewRadarItem {
  key: string;
  label: string;
  /** 0-100 的截面分。 */
  value: number;
}

export interface OverviewBreadth {
  total: number;
  up: number;
  down: number;
  flat: number;
  up_pct: number;
  down_pct: number;
  avg_pct: number;
  median_pct: number;
  strong_up: number;
  strong_down: number;
}

export interface OverviewDistributionItem {
  label: string;
  count: number;
  pct: number;
}

export interface OverviewTrend {
  above_ma5: number;
  above_ma20: number;
  above_ma60: number;
  above_ma5_pct: number;
  above_ma20_pct: number;
  above_ma60_pct: number;
  new_high: number;
  new_low: number;
}

export interface OverviewActivity {
  avg_vol_ratio: number | null;
  high_vol_count: number;
  high_vol_pct: number;
  vol_ratio_coverage: number;
}

export interface OverviewLimit {
  limit_up: number;
  broken: number;
  limit_down: number;
  max_boards: number;
  seal_rate: number;
  sealed_ready: boolean;
  sealed_reason: string | null;
  fake_up: number;
  fake_down: number;
}

export interface OverviewCoverage {
  quoted: number;
  universe: number;
  /** 0-1 的小数(不是百分数)。 */
  ratio: number | null;
  baseline_symbols: number;
  baseline_as_of: string | null;
  baseline_sessions: number;
  amount_available: boolean;
}

export interface OverviewRankItem {
  name: string;
  avg_pct: number;
  count: number;
  up_count: number;
  down_count: number;
  leader: { symbol: string | null; name: string };
}

export interface OverviewRankBlock {
  leading: OverviewRankItem[];
  lagging: OverviewRankItem[];
  /** 东财板块指数口径(非等权成员均值)—— 与 concept_rps 不可混谈。 */
  basis: string;
}

export interface OverviewStockRow {
  symbol: string;
  name: string;
  close: number | null;
  change_pct: number | null;
  amount: number | null;
  volume_ratio: number | null;
  board: string;
  boards: number;
}

export interface OverviewData {
  status: "ok" | "empty" | "degraded";
  reason: string | null;
  as_of: string | null;
  adjust: string;
  quote_source: "snapshot" | "daily" | "none";
  coverage: OverviewCoverage | null;
  indices: OverviewIndex[];
  indices_reason: string | null;
  breadth: OverviewBreadth;
  amount: { total: number | null; available: boolean };
  boards: unknown[];
  limit: OverviewLimit;
  distribution: OverviewDistributionItem[];
  trend: OverviewTrend;
  activity: OverviewActivity;
  radar: OverviewRadarItem[];
  emotion: { score: number | null; label: string };
  top_gainers: OverviewStockRow[];
  top_losers: OverviewStockRow[];
  amount_leaders: OverviewStockRow[];
  vol_ratio_leaders: OverviewStockRow[];
  concept_rank: OverviewRankBlock;
  industry_rank: OverviewRankBlock;
  snapshot_reason: string | null;
  rank_reason: string | null;
  requested_date: string | null;
  /** recap_summary() 的一行摘要(指数|情绪|涨停|成交额);status=ok/empty 时才有。 */
  summary?: string;
}

/** 百分数字 → 带符号展示(+0.62% / -1.10% / 0.00%)。 */
export function fmtPctNumber(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}%`;
}

/** 元 → 亿/万亿(成交额量级)。不可得返回「—」。 */
export function fmtCny(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e12) return `${(v / 1e12).toFixed(2)} 万亿`;
  if (abs >= 1e8) return `${(v / 1e8).toFixed(0)} 亿`;
  if (abs >= 1e4) return `${(v / 1e4).toFixed(0)} 万`;
  return String(Math.round(v));
}

/** 小数(0-1)→ 百分数展示,如 0.9231 → 92.3%。 */
export function fmtRatioPct(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

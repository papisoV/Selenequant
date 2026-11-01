"use client";

// A股异动偏离 —— GET /api/v1/ashare/deviation 的类型契约。
//
// **单位**:value / threshold / closeness / max_closeness 全是**小数**
// (0.20 = 20%)。与 lib/api/ashare-overview.ts 的"百分数"口径**相反**,
// 两边的格式化函数刻意不共用 —— 混用一次就是把 20% 显示成 0.2%。
//
// 口径来源 packages/ashare/selene_space_ashare/abnormal.py:
//   偏离值 = 个股 N 日累计涨跌 − 对应基准指数同期涨跌;
//   接近度 = |偏离值| ÷ 该方向阈值;
//   ≥1.0 已触发 / ≥0.7 边缘 / 其余观察(服务端已判好,前端不复算)。

export type DeviationStatus = "triggered" | "edge" | "watch";

export interface DeviationWindowInfo {
  /** 小数;正负 = 偏离方向。 */
  value: number;
  /** 小数;按偏离方向取对应侧(10/30 日负向更严)。 */
  threshold: number;
  /** 小数;value 绝对值 ÷ threshold。 */
  closeness: number;
}

export interface DeviationRow {
  symbol: string;
  /** main / kcb / cyb / bse。 */
  board: string;
  st: boolean;
  /** 基准指数代码(如 000002.SH)。 */
  bench: string;
  /** 历史来自哪个 K 线分区。 */
  basis: "spot" | "astock" | string;
  basis_date: string;
  /** true = 今日实时涨跌已叠加;false = 纯日线口径。 */
  intraday: boolean;
  max_closeness: number;
  status: DeviationStatus;
  /** 键为 "3d" / "10d" / "30d";数据不足的窗口缺键(不是 0)。 */
  windows: Record<string, DeviationWindowInfo | undefined>;
}

export interface DeviationData {
  status: "ok" | "empty" | "degraded";
  reason?: string | null;
  /** reason === "no_ashare_favorites" 时服务端给的教学文案。 */
  hint?: string;
  as_of: string;
  live: boolean;
  counts: { triggered: number; edge: number; watch: number };
  /** 已按 max_closeness 降序。 */
  rows: DeviationRow[];
  skipped: { symbol: string; reason: string }[];
}

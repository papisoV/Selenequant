"use client";

import { apiPost } from "@/lib/api-client";

// A股交互式选股 —— POST /api/v1/ashare/screen 的类型契约与客户端。
//
// **单位**:change_pct / turnover / float_mv / total_mv 的口径与
// lib/api/ashare-overview.ts **相同**(涨跌幅是**百分数** 5.5 = +5.5%,
// 金额是**元**);与 ashare-deviation.ts 的**小数**口径相反。本文件的
// 格式化函数只服务本端点,不要和 deviation 的 pct() 混用。
//
// 数据诚实条款(前端不得与之矛盾):
//   · status "empty" = 截面上没有任何行(数据问题);
//     status "ok" + matched === 0 = 今天没有票满足条件(不是数据问题)。
//     两者文案必须分开 —— 混成一句会让"数据没取到"读作"市场没机会"。
//   · quote_source "daily" = 日线回退:成交额是 close×volume 的估算,逐行
//     turnover_est 标出;量比按完整收盘日计算,与盘中口径不同。
//   · coverage.amount_available === false → 成交额条件只在日线回退下可用估算值。
//   · coverage.market_cap_available === false → 市值表缺失,市值条件会排掉全部行。
//     === null → 这次没设市值条件,后端没去取表;页脚不得据此报警。
//   · rows 是**截断后**的(最多 limit 条),matched 是截断前的命中数。
//
// 契约来源:services/local-api/app/api/v1/ashare_screen.py +
// packages/ashare/selene_space_ashare/screener.py。失败一律 200 + status;
// 条件本身不合法才 422(抛 ApiError,status=422)。

export type ScreenStatus = "ok" | "empty" | "degraded";

export type ScreenBoard = "main" | "kcb" | "cyb" | "bse";
export type ScreenMaWindow = "ma5" | "ma20" | "ma60";
export type ScreenSortKey =
  | "change_pct"
  | "boards"
  | "volume_ratio"
  | "turnover"
  | "float_mv"
  | "total_mv"
  | "last";

export interface ScreenRequest {
  /** YYYY-MM-DD;空/缺省 = 最新达到覆盖率的交易日。 */
  date?: string;
  price_min?: number;
  price_max?: number;
  /** 百分数:5 = +5%。 */
  change_pct_min?: number;
  change_pct_max?: number;
  /** 连板数(不是板块 —— 板块见 board_types)。 */
  boards_min?: number;
  boards_max?: number;
  volume_ratio_min?: number;
  volume_ratio_max?: number;
  /** 元。 */
  turnover_min?: number;
  /** 元。 */
  float_mv_min?: number;
  float_mv_max?: number;
  /** 元。 */
  total_mv_min?: number;
  total_mv_max?: number;
  /** main / kcb / cyb / bse;空数组 = 不限。 */
  board_types?: ScreenBoard[];
  exclude_st?: boolean;
  above_ma?: ScreenMaWindow | null;
  new_high?: boolean;
  new_low?: boolean;
  /** true = 只要涨停;false = 只要未涨停;null/缺省 = 不限。 */
  limit_up?: boolean | null;
  sort_by?: ScreenSortKey;
  sort_desc?: boolean;
  limit?: number;
}

export interface ScreenRow {
  symbol: string;
  name: string;
  /** 最新价 / 收盘价。 */
  last: number | null;
  prev_close: number | null;
  high: number | null;
  low: number | null;
  volume: number | null;
  /** 快照口径的真实成交额;日线回退下为 null(用 turnover 代替)。 */
  amount: number | null;
  /** 百分数;无昨收价时 null。 */
  change_pct: number | null;
  /** 连板数;未涨停固定为 0。 */
  boards: number;
  /** 今日成交量 ÷ 前 5 日均量;无基线时 null。盘中口径偏低。 */
  volume_ratio: number | null;
  /** 元;快照真实成交额或 close×volume 估算。取不到时 null(不是 0)。 */
  turnover: number | null;
  /** true = turnover 是 close×volume 的估算值。 */
  turnover_est: boolean;
  /** 元;市值表缺这只票时 null。 */
  float_mv: number | null;
  total_mv: number | null;
  /** 今日收盘/现价是否封在涨停价(交易所精确规则)。 */
  limit_up: boolean;
  limit_down: boolean;
  /** 触板未封(炸板)。 */
  broken: boolean;
  ma5: number | null;
  ma20: number | null;
  ma60: number | null;
  high60: number | null;
  low60: number | null;
}

export interface ScreenCoverage {
  quoted: number;
  universe: number;
  /** 0-1 的小数(不是百分数)。 */
  ratio: number | null;
  baseline_symbols: number;
  baseline_as_of: string | null;
  baseline_sessions: number;
  /** true = 成交额是快照真实值;false = 日线回退,成交额列是估算。 */
  amount_available: boolean;
  /**
   * 三态:`null` = 本次没设市值条件、后端没去取表(常规筛选都是这个);
   * `true` = 取到了;`false` = 取了但拿不到,市值条件会把行全排掉。
   */
  market_cap_available: boolean | null;

}

/** 服务端规范化后的条件回显 —— 渲染条件标签用,不要在本地重新推导。 */
export interface ScreenSpecEcho {
  price: [number | null, number | null];
  change_pct: [number | null, number | null];
  boards: [number | null, number | null];
  volume_ratio: [number | null, number | null];
  turnover_min: number | null;
  float_mv: [number | null, number | null];
  total_mv: [number | null, number | null];
  board_type: string[];
  exclude_st: boolean;
  above_ma: string | null;
  new_high: boolean;
  new_low: boolean;
  limit_up: boolean | null;
  sort_by: string;
  sort_desc: boolean;
  limit: number;
}

export interface ScreenData {
  status: ScreenStatus;
  reason: string | null;
  as_of: string | null;
  adjust: string;
  quote_source: "snapshot" | "daily" | "none";
  coverage: ScreenCoverage | null;
  screen: ScreenSpecEcho;
  /** 截断前的命中数。 */
  matched: number;
  /** 参与筛选的行数(截面大小)。 */
  scanned: number;
  /** true = rows.length < matched。 */
  truncated: boolean;
  /** 服务端诊断文案(如"N 行无市值数据已排除"),原样呈现。 */
  notes: string[];
  rows: ScreenRow[];
}

/**
 * 跑一次筛选。
 *
 * 用 POST:首次请求在后端是冷路径(全市场读 + 基线装配,十余秒),前端
 * apiPost 不加 GET 超时;而且 body 比 query 更适合可选的数值字段。
 * 422 = 条件不合法(如 min > max),调用方应把原话展示给用户。
 */
export async function screenAshare(
  req: ScreenRequest,
  signal?: AbortSignal,
): Promise<ScreenData> {
  return apiPost<ScreenData>("/api/v1/ashare/screen", req, signal);
}

/** 百分数 → 带符号展示(+5.50% / -1.10% / 0.00%)。 */
export function fmtScreenPct(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

/** 元 → 亿/万亿(成交额、市值)。不可得返回「—」,绝不显示 0。 */
export function fmtScreenCny(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e12) return `${(v / 1e12).toFixed(2)} 万亿`;
  if (abs >= 1e8) return `${(v / 1e8).toFixed(1)} 亿`;
  if (abs >= 1e4) return `${(v / 1e4).toFixed(0)} 万`;
  return String(Math.round(v));
}

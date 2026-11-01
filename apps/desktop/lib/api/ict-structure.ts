"use client";

// ICT 结构叠加层数据源：类型定义 + 查询串构造。
//
// 实际拉取走 lib/hooks/use-api 的 fetcher（它负责解 APIResponse envelope
// 并在非 2xx 时抛 ApiError），见 lib/hooks/use-ict-structure.ts。
//
// 时间戳一律 Unix 秒 UTC，与 lightweight-charts 的 UTCTimestamp 对齐。
// bars 默认不回传（图表已有 K 线），只有显式 include_bars 才有。

export type StructureTier = "st" | "it" | "lt";
export type StructureKind = "BOS" | "MSS";

export interface StructureSwing {
  /** 在请求窗口内的 bar 下标（0 = 窗口第一根）。 */
  index: number;
  price: number;
  kind: "high" | "low";
  /** st = 庄家短期 / it = 中期 / lt = 长期（递归标记，非三套 period）。 */
  tier: StructureTier;
  time: number;
}

export interface StructureDisplacement {
  body_ratio: number;
  has_fvg: boolean;
  fvg_range: number;
}

export interface StructureEvent {
  kind: StructureKind;
  /** 1 = 向上破位，-1 = 向下破位。 */
  direction: 1 | -1;
  /** 被破的那条 swing 价位。 */
  level: number;
  broken_bar_index: number;
  swing_bar_index: number;
  broken_time: number;
  swing_time: number;
  /** 位移证据：只附带，从不用于过滤事件。 */
  displacement: StructureDisplacement;
}

export interface IctStructureResponse {
  exchange: string;
  market_type: string;
  symbol: string;
  interval: string;
  break_on: "close" | "wick";
  bar_count: number;
  bars: { t: number; o: number; h: number; l: number; c: number }[];
  swings: StructureSwing[];
  events: StructureEvent[];
  /** Timing layer: which bars sit inside a killzone window. */
  killzone?: KillzoneLayer;
  /** Positioning layer: the dealing range currently in play, or null. */
  dealing_range?: DealingRange | null;
}

export interface KillzoneWindow {
  name: string;
  start_hour: number;
  end_hour: number;
}

/**
 * Per-bar killzone membership. ``v`` is 1 inside an enabled window and 0
 * outside; rows only exist for bars the server could clock, so an empty
 * ``bars`` plus ``clock_source: null`` means "no clock", not "never inside".
 */
export interface KillzoneLayer {
  sessions: string[];
  tz_offset_hours: number;
  windows: KillzoneWindow[];
  clock_source: "index" | "timestamp" | null;
  bars: { t: number; v: 0 | 1 }[];
  inside_count: number;
}

export type DealingZone = "discount" | "equilibrium" | "premium";

/**
 * The last readable premium/discount leg. Only the current range is returned:
 * it is the one a trader draws, and stacking every historical leg onto one
 * price axis turns the chart into a striped mess.
 */
export interface DealingRange {
  bar_index: number;
  time: number;
  low: number;
  high: number;
  equilibrium: number;
  premium_from: number;
  discount_to: number;
  close: number;
  /** Close's position inside the leg, 0 at the low and 1 at the high. */
  position: number | null;
  zone: DealingZone | null;
}

export interface IctStructureQuery {
  exchange?: string;
  marketType?: string;
  symbol: string;
  interval: string;
  limit?: number;
  breakOn?: "close" | "wick";
  includeBars?: boolean;
  /** Comma-separated killzone keys; server default is london,new_york. */
  sessions?: string;
  tzOffsetHours?: number;
  pdPeriod?: number;
  premiumThreshold?: number;
  discountThreshold?: number;
}

export function ictStructurePath(q: IctStructureQuery): string {
  const params = new URLSearchParams({
    exchange: q.exchange ?? "binance",
    market_type: q.marketType ?? "futures",
    symbol: q.symbol,
    interval: q.interval,
    limit: String(q.limit ?? 500),
    break_on: q.breakOn ?? "close",
  });
  if (q.includeBars) params.set("include_bars", "true");
  // Only send the ICT layer knobs when the caller overrides a default —
  // keeping the query string stable keeps SWR's cache key stable too.
  if (q.sessions !== undefined) params.set("sessions", q.sessions);
  if (q.tzOffsetHours !== undefined) params.set("tz_offset_hours", String(q.tzOffsetHours));
  if (q.pdPeriod !== undefined) params.set("pd_period", String(q.pdPeriod));
  if (q.premiumThreshold !== undefined) params.set("premium_threshold", String(q.premiumThreshold));
  if (q.discountThreshold !== undefined) params.set("discount_threshold", String(q.discountThreshold));
  return `/api/v1/chart/ict-structure-overlay?${params.toString()}`;
}

// ICT 结构层渲染映射：响应载荷 → lightweight-charts 的 markers / price lines。
//
// 纯函数，不碰 DOM 也不碰 chart API——kf-chart 的 effect 只负责把结果喂进去。
// 这样图元数量上限、层级取舍、标签文案都能单测，不用真渲染。

import type { SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";
import type {
  DealingRange,
  KillzoneLayer,
  StructureEvent,
  StructureKind,
  StructureSwing,
  StructureTier,
} from "@/lib/api/ict-structure";

/** 默认可见图元上限。超出后按「越新越重要」截断，避免 1500 根窗口把图面糊死。 */
export const DEFAULT_MAX_SWINGS = 60;
export const DEFAULT_MAX_EVENTS = 40;

/** 层级由弱到强。层级越高，标记越大、标签带前缀。 */
const TIER_RANK: Record<StructureTier, number> = { st: 0, it: 1, lt: 2 };

export const SWING_COLORS: Record<"high" | "low", Record<StructureTier, string>> = {
  high: { st: "#8b95a8", it: "#d8a657", lt: "#f3625d" },
  low: { st: "#8b95a8", it: "#d8a657", lt: "#61bd67" },
};

export const EVENT_COLORS = {
  BOS: { 1: "#61bd67", [-1]: "#f3625d" },
  MSS: { 1: "#54b0f3", [-1]: "#c77dff" },
} as const;

export interface OverlayInputs {
  swings: StructureSwing[];
  events: StructureEvent[];
  maxSwings?: number;
  maxEvents?: number;
  /** 未给的档位一律视为显示;只影响渲染,不改变检测。 */
  tiers?: Record<StructureTier, boolean>;
  /** 结构点只留一侧。默认 both。 */
  swingSide?: "both" | "high" | "low";
  /** 破位事件只留某一类。默认两类都留。 */
  eventKinds?: Record<StructureKind, boolean>;
  /** 破位方向过滤(1 = 向上破, -1 = 向下破)。 */
  eventSide?: "both" | "up" | "down";
}

export type { StructureKind };

function tierLabel(tier: StructureTier): string {
  return tier === "lt" ? "LTH" : tier === "it" ? "ITH" : "";
}

/**
 * 结构点 → series markers。
 *
 * 取舍规则：先按显示的档位/方向过滤，再按 bar 序号保留最新的 maxSwings 个，
 * 保留集合内层级越高越优先（LTH > ITH > STH）。这样近端细节不丢，
 * 远端只留大结构，图面密度可控。
 *
 * 过滤在这里做而不是在调用方做：截断发生在过滤**之后**，否则用户关掉 ST
 * 档后仍只看到 40 个里的 12 个 LTH —— 上限被噪声吃掉了。
 */
export function buildSwingMarkers(inputs: OverlayInputs): SeriesMarker<Time>[] {
  const max = inputs.maxSwings ?? DEFAULT_MAX_SWINGS;
  const side = inputs.swingSide ?? "both";
  const tiers = inputs.tiers;

  const swings = inputs.swings.filter(
    (s) =>
      Number.isFinite(s.price) &&
      (side === "both" || s.kind === side) &&
      (tiers ? tiers[s.tier] : true),
  );

  // 近端优先保留；同 bar 时高层级优先。
  const ranked = [...swings].sort((a, b) => {
    if (b.index !== a.index) return b.index - a.index;
    return TIER_RANK[b.tier] - TIER_RANK[a.tier];
  });
  const kept = new Set(ranked.slice(0, max));

  return swings
    .filter((s) => kept.has(s))
    .sort((a, b) => a.index - b.index)
    .map((s) => {
      const label = tierLabel(s.tier);
      return {
        time: s.time as UTCTimestamp,
        position: s.kind === "high" ? "aboveBar" : "belowBar",
        shape: s.kind === "high" ? "arrowDown" : "arrowUp",
        color: SWING_COLORS[s.kind][s.tier],
        size: 1 + TIER_RANK[s.tier],
        text: label || undefined,
      } satisfies SeriesMarker<Time>;
    });
}

export interface StructureLine {
  price: number;
  color: string;
  title: string;
  /** 破位 bar 的时间戳，用作 React key 与排序依据。 */
  time: number;
}

/**
 * BOS/MSS → 水平价位线。
 *
 * 每条线画在被破 swing 的价位上（结构位），不是破位当根的收盘价——
 * 交易者要看的是「哪条线被打穿」，那是结构位。
 *
 * 去重：同一 kind+direction+level 只留最新一次破位，历史重复破位会把线叠成一片。
 * 过滤发生在去重**之后**、截断**之前**：先按价位收敛，再按用户的 kind/方向
 * 偏好筛，最后才取最近的 maxEvents 条——否则关掉 MSS 后 BOS 也会被挤掉。
 */
export function buildStructureLines(inputs: OverlayInputs): StructureLine[] {
  const max = inputs.maxEvents ?? DEFAULT_MAX_EVENTS;
  const kinds = inputs.eventKinds;
  const side = inputs.eventSide ?? "both";
  const seen = new Map<string, StructureEvent>();

  for (const e of inputs.events) {
    if (!Number.isFinite(e.level)) continue;
    // 同一价位同类事件：保留最近一次破位（broken_time 更大）。
    const key = `${e.kind}:${e.direction}:${e.level.toFixed(8)}`;
    const prev = seen.get(key);
    if (!prev || e.broken_time >= prev.broken_time) seen.set(key, e);
  }

  const unique = [...seen.values()]
    .filter((e) => (kinds ? kinds[e.kind] : true))
    .filter((e) => side === "both" || (side === "up" ? e.direction === 1 : e.direction === -1))
    .sort((a, b) => b.broken_time - a.broken_time);
  return unique.slice(0, max).map((e) => ({
    price: e.level,
    color: EVENT_COLORS[e.kind][e.direction],
    title: `${e.kind} ${e.direction === 1 ? "↑" : "↓"}`,
    time: e.broken_time,
  }));
}

// ---------------------------------------------------------------------------
// Killzone + dealing range (timing and positioning, not structure events)
// ---------------------------------------------------------------------------

/** Killzone colours. Inside-bar tint, plus the background band painted behind the candles. */
export const KILLZONE_COLORS = {
  inside: "#5fb6f3",
  outside: "rgba(95, 182, 243, 0.10)",
  band: "rgba(95, 182, 243, 0.13)",
} as const;

/** Dealing-range line colours, by role rather than by zone. */
export const DEALING_RANGE_COLORS = {
  premium: "#f3625d",
  equilibrium: "#8b95a8",
  discount: "#61bd67",
} as const;

export interface KillzoneBarPoint {
  time: UTCTimestamp;
  value: number;
  color: string;
}

/**
 * Killzone membership → histogram points for a dedicated pane.
 *
 * Rendered as a 1/0 step rather than a line so the eye reads it as a state
 * ("in session" / "out of session"), which is what the detector means. Bars
 * whose ``v`` is neither 0 nor 1 are dropped: the backend already strips
 * unclockable bars, so anything else is a shape we do not understand and
 * should not draw as a guess.
 */
export function buildKillzonePoints(layer: KillzoneLayer | null | undefined): KillzoneBarPoint[] {
  if (!layer || layer.clock_source === null) return [];
  const points: KillzoneBarPoint[] = [];
  for (const bar of layer.bars) {
    if (bar.v !== 0 && bar.v !== 1) continue;
    if (!Number.isFinite(bar.t)) continue;
    points.push({
      time: bar.t as UTCTimestamp,
      value: bar.v,
      color: bar.v === 1 ? KILLZONE_COLORS.inside : KILLZONE_COLORS.outside,
    });
  }
  return points;
}

/**
 * Dealings range → the three horizontal levels a trader draws.
 *
 * Only levels whose price is finite and inside the leg are emitted: a
 * degenerate range (low >= high) would place lines in the wrong order and
 * read as a signal that does not exist.
 *
 * ``lines`` lets the caller hide individual levels. It is applied last, so a
 * hidden line can never shift the geometry checks above it.
 */
export function buildDealingRangeLines(
  range: DealingRange | null | undefined,
  lines?: { premium: boolean; equilibrium: boolean; discount: boolean },
): StructureLine[] {
  if (!range) return [];
  const { low, high, premium_from, discount_to, equilibrium, time } = range;
  const values = [low, high, premium_from, discount_to, equilibrium];
  if (!values.every((v) => Number.isFinite(v))) return [];
  if (!(low < high)) return [];

  const show = lines ?? { premium: true, equilibrium: true, discount: true };
  const zoneLabel = range.zone ? ` · ${range.zone}` : "";
  return [
    {
      price: premium_from,
      color: DEALING_RANGE_COLORS.premium,
      title: `溢价${zoneLabel}`,
      time,
      visible: show.premium,
    },
    {
      price: equilibrium,
      color: DEALING_RANGE_COLORS.equilibrium,
      title: "均衡 50%",
      time,
      visible: show.equilibrium,
    },
    {
      price: discount_to,
      color: DEALING_RANGE_COLORS.discount,
      title: `折价${zoneLabel}`,
      time,
      visible: show.discount,
    },
  ]
    .filter((l) => l.visible)
    .map(({ visible: _visible, ...line }) => line);
}

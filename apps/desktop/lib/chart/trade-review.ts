// 回测交易复盘 — 窗口/翻页/降档/标记 的纯函数层(不触图表/网络,jsdom 可测)。
// 消费方: backtest-trade-review.tsx(懒取 K 线 + CandlestickChart 渲染)。

import type { BacktestTrade } from "@/lib/api/backtests";
import type { Kline } from "@/lib/types/backend";

export const INTERVAL_LADDER = ["1d", "4h", "1h", "15m", "5m", "1m"] as const;

export const INTERVAL_MS: Record<string, number> = {
  "1m": 60_000,
  "5m": 300_000,
  "15m": 900_000,
  "1h": 3_600_000,
  "4h": 14_400_000,
  "1d": 86_400_000,
};

export const PAGE_LIMIT = 1500; // 与 KLINE_FETCH_LIMIT 一致
export const MAX_PAGES_FOCUS = 5; // 单笔聚焦最多翻 5 页(7500 根,覆盖 1m×5天)
export const EDGE_BARS = 10; // 窗口前后各多留 10 根,交易不贴边

/** 回测周期 → 复盘候选降档阶梯(自身打头,往下到 1m)。 */
export function reviewLadder(backtestInterval: string): string[] {
  const idx = INTERVAL_LADDER.indexOf(backtestInterval as (typeof INTERVAL_LADDER)[number]);
  const start = idx === -1 ? 2 : idx; // 未知周期默认从 1h 起降
  return INTERVAL_LADDER.slice(start);
}

/** 交易窗口在给定周期下需要几根 K 线(含前后边距)。 */
export function windowBarCount(entryMs: number, exitMs: number, interval: string): number {
  const iv = INTERVAL_MS[interval];
  if (!iv) return Number.POSITIVE_INFINITY;
  return Math.ceil((exitMs - entryMs) / iv) + 1 + EDGE_BARS * 2;
}

/** 窗口单页能取完吗。 */
export function fitsOnePage(entryMs: number, exitMs: number, interval: string): boolean {
  return windowBarCount(entryMs, exitMs, interval) <= PAGE_LIMIT;
}

/**
 * 自动周期降档:从回测周期起逐档下降,取第一档「单页能装下窗口且至少 MIN_BARS 有形可看」的周期。
 * 全降到底仍不满足 → 返回最低档(1m,交给翻页封顶兜底)。
 */
export function pickReviewInterval(
  entryMs: number,
  exitMs: number,
  backtestInterval: string,
  minBars = 10,
): string {
  const ladder = reviewLadder(backtestInterval);
  for (const iv of ladder) {
    const bars = windowBarCount(entryMs, exitMs, iv);
    if (bars <= PAGE_LIMIT && bars >= minBars + EDGE_BARS * 2) return iv;
  }
  return ladder[ladder.length - 1];
}

/** 翻页计划:从 exit 向过去取 n 页(每页 PAGE_LIMIT 根),首圈用 endTime=exitMs。 */
export function paginationPlan(
  entryMs: number,
  exitMs: number,
  interval: string,
  maxPages: number,
): { endTime: number | null; pages: number } {
  const bars = windowBarCount(entryMs, exitMs, interval);
  const pages = Math.min(Math.ceil(bars / PAGE_LIMIT), maxPages);
  return { endTime: exitMs, pages: Math.max(pages, 1) };
}

/** K 线时间戳(Kline.timestamp/open_time, ms)。 */
export function klineTs(k: Kline): number {
  return k.timestamp ?? k.open_time ?? 0;
}

/** 裁剪 K 线到 [entryMs−edge, exitMs+edge](裁剪即聚焦:CandlestickChart fitContent 后窗口即全景)。 */
export function clipToWindow(
  klines: Kline[],
  entryMs: number,
  exitMs: number,
  interval: string,
): Kline[] {
  const iv = INTERVAL_MS[interval] ?? INTERVAL_MS["1h"];
  const lo = entryMs - EDGE_BARS * iv;
  const hi = exitMs + EDGE_BARS * iv;
  return klines.filter((k) => klineTs(k) >= lo && klineTs(k) <= hi);
}

/** CandlestickChart 的 TradeMarker 子集(时间换算 ms→s + tz offset 由组件内部处理,此处给秒)。 */
export interface ReviewMarker {
  time: number;
  position: "aboveBar" | "belowBar";
  color: string;
  shape: "arrowUp" | "arrowDown" | "circle";
  text?: string;
}

// PnL 着色 = **方向轴**(盈亏是有符号数值,随 locale 翻转)。
// 默认常量与 --color-pos/--color-neg token 同值(Tailwind 未挂载时的兜底);
// 运行时由消费方经 palette 参数注入 useChartTheme() 的实时值。
export const WIN_COLOR = "#61bc66"; // = --color-pos (en) / --color-neg (zh)
export const LOSS_COLOR = "#f3615c"; // = --color-neg (en) / --color-pos (zh)

/** 标记调色板(方向轴两色)。省略则用模块常量默认值。 */
export interface MarkerPalette {
  win: string;
  loss: string;
}

/**
 * 单笔复盘标记:entry 用箭头(方向↑/↓),exit 用圆点。
 * 颜色按盈亏(超越 QD:方向着色→盈亏着色)。
 * palette 可选 —— 传入则用实时 token 色(随 locale 翻转),省略则用默认常量。
 */
export function buildFocusMarkers(trade: BacktestTrade, palette?: MarkerPalette): ReviewMarker[] {
  const pnl = Number(trade.pnl ?? 0);
  const win = palette?.win ?? WIN_COLOR;
  const loss = palette?.loss ?? LOSS_COLOR;
  const color = pnl >= 0 ? win : loss;
  const side = (trade.side ?? "").toLowerCase();
  const isLong = side === "buy" || side === "long";
  const entryTs = trade.entry_time;
  const exitTs = trade.exit_time;
  if (entryTs == null) return [];
  const markers: ReviewMarker[] = [
    {
      time: Math.floor(entryTs / 1000),
      position: isLong ? "belowBar" : "aboveBar",
      color,
      shape: isLong ? "arrowUp" : "arrowDown",
      text: isLong ? "买" : "卖",
    },
  ];
  if (exitTs != null) {
    markers.push({
      time: Math.floor(exitTs / 1000),
      position: isLong ? "aboveBar" : "belowBar",
      color,
      shape: "circle",
      text: `${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}`,
    });
  }
  return markers;
}

/**
 * 全景标记:全回测区间所有交易平铺,同 bar 聚合 ×N(对齐 QD)。
 * entry 买/卖箭头 + exit 圆点都上;PnL 色键控连亏/顺风簇。
 */
export function buildOverviewMarkers(
  trades: BacktestTrade[],
  interval: string,
  palette?: MarkerPalette,
): ReviewMarker[] {
  const iv = INTERVAL_MS[interval] ?? INTERVAL_MS["1h"];
  const bucket = new Map<number, ReviewMarker[]>();
  for (const t of trades) {
    for (const m of buildFocusMarkers(t, palette)) {
      const key = Math.floor((m.time * 1000) / iv);
      const arr = bucket.get(key) ?? [];
      arr.push(m);
      bucket.set(key, arr);
    }
  }
  const out: ReviewMarker[] = [];
  for (const [, arr] of bucket) {
    if (arr.length === 1) {
      out.push(arr[0]);
    } else {
      // 同 bar ×N:首条带计数,其余丢弃(保留首条形状/颜色)
      out.push({ ...arr[0], text: `${arr[0].text ?? ""}×${arr.length}` });
    }
  }
  return out.sort((a, b) => a.time - b.time);
}

/** 全景模式取数参数:回测全区间 + 边距,翻页封顶。 */
export function overviewPlan(
  startMs: number,
  endMs: number,
  interval: string,
): { endTime: number; pages: number } {
  const bars = Math.ceil((endMs - startMs) / (INTERVAL_MS[interval] ?? INTERVAL_MS["1h"])) + 1;
  return { endTime: endMs, pages: Math.min(Math.max(Math.ceil(bars / PAGE_LIMIT), 1), 20) };
}

// ---------------------------------------------------------------------------
// 发车区间预估(backtests-page 发车表单用)。
// ⚠️ 后端目前无 bar 数硬守卫(backtests.py 只在策略构造函数跳过 max_bars,
// runtime.py:87 的 500 是 candle buffer 上限非请求校验)——这里的预估是
// 提交前唯一的预警。将来若加后端守卫,阈值应与此处对齐。
// ---------------------------------------------------------------------------

export const BAR_COUNT_WARN = 5_000; // 超过:可能变慢
export const BAR_COUNT_HEAVY = 20_000; // 超过:建议缩短区间或放大周期

/**
 * 发车区间预估根数(自然日直算)。
 *
 * ⚠️ 口径:按自然日直算,未扣休市日。当前 symbol 面(BTC/ETH/SOL/BNB)全 7×24
 * 加密,直算准确;将来接美股/台股需按标的乘交易日系数(约 0.7,否则高估约 30%)。
 *
 * @returns 预估根数;区间非法(倒置/等值/非有限)或周期未知时返回 null(调用方不显示)。
 */
export function estimateBars(startMs: number, endMs: number, interval: string): number | null {
  const iv = INTERVAL_MS[interval];
  if (!iv) return null;
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return null;
  if (endMs <= startMs) return null;
  return Math.ceil((endMs - startMs) / iv) + 1;
}

/** 预估根数 → 提示档位。ok=不提示。 */
export function barEstimateTone(bars: number): "ok" | "warn" | "heavy" {
  if (bars >= BAR_COUNT_HEAVY) return "heavy";
  if (bars >= BAR_COUNT_WARN) return "warn";
  return "ok";
}

"use client";

import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  CrosshairMode,
  LineStyle,
} from "lightweight-charts";
import type {
  IChartApi,
  ISeriesApi,
  IPriceLine,
  ISeriesMarkersPluginApi,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from "lightweight-charts";
import { CloseCountdown, closeAtFrom } from "@/components/chart/close-countdown";
import { DrawingLayer, type DrawingLayerHandle } from "@/components/watch/chart-drawing-layer";
import { selectVisibleGrids, useDrawingsStore } from "@/lib/store/drawings";
import {
  appearanceToChartOptions,
  appearanceToSeriesOptions,
  defaultGridColor,
} from "@/lib/chart/chart-appearance";
import { pricePrecision } from "@/lib/format";
import { KLINE_FETCH_LIMIT } from "@/lib/api/markets";
import { useKlines } from "@/lib/hooks/use-klines";
import { useIctStructure } from "@/lib/hooks/use-ict-structure";
import {
  buildDealingRangeLines,
  buildKillzonePoints,
  buildStructureLines,
  buildSwingMarkers,
} from "@/lib/chart/ict-structure-overlay";
import {
  buildKillzoneBands,
  killzoneBandFill,
  KillzonePrimitive,
} from "@/lib/chart/killzone-primitive";
import {
  DEFAULT_ICT_DISPLAY,
  sessionsParam,
  type IctDisplaySettings,
} from "@/lib/chart/ict-display";
import { useMarketStore } from "@/lib/store/market";
import type { Kline } from "@/lib/types/backend";
import { useContextStore } from "@/lib/store/context";
import { perf } from "@/lib/perf";
import { useUiText } from "@/components/i18n/use-ui-text";

interface KfChartProps {
  showMA?: boolean;
  showVOL?: boolean;
  showMACD?: boolean;
  /** 以下 6 项为指标 picker 新增（0923）：EMA/BOLL 叠主图，RSI/KDJ/ATR 独立窗格。
   *  只在 SWR 快照/收盘时重算（QD 同粒度），不走 WS 逐秒递推。 */
  showEma20?: boolean;
  showEma50?: boolean;
  showBoll?: boolean;
  showRsi?: boolean;
  showKdj?: boolean;
  showAtr?: boolean;
  symbolOverride?: string;
  /** 覆盖周期（pa 页面板内局部 tf，不污染全局 store）。不传用 pageFilter.tf。 */
  tfOverride?: string;
  /** 决策价格线（AI 分析的入场/SL/TP）。依赖变化时先清除旧的再画新的。 */
  decisionOverlays?: DecisionOverlay[];
  /** 实时最新价（来自 WS ticker），用于驱动最后一根 K 线 close 实时跳动。
   *  不传则只靠 useKlines 的 SWR 30s 轮询刷新（刷新才更新）。 */
  tickerLast?: number | null;
  /** 冻结到发起分析时刻：翻 true 瞬间内部快照最后 bar 时间，此后只接受
   *  time <= 快照的数据（历史左追加允许，右侧新 bar 被丢弃），ticker 不再跳动。
   *  图 = AI 所见（W3-A 视觉锚）。 */
  frozen?: boolean;
  /** AI 诊断窗口末（epoch 秒）。图上有更新的 bar 时显示「AI 所见截至 HH:mm · +N 根新K线」
   *  保鲜差距徽章（W3-B）。无新 bar / null 不显示。 */
  staleAsOf?: number | null;
  /** 收盘归零时自动触发分析（opt-in，默认关）。透传给 CloseCountdown。 */
  autoSubmit?: boolean;
  /** 收盘倒计时归零回调（仅 autoSubmit 为真时触发）。 */
  onCloseElapsed?: () => void;
  /** 手画预警线（批3）：已挂的规则，重建回图上。 */
  alertLines?: AlertLine[];
  /** 「预警」模式开关。true 时点击图表 = 在该价位画线（不弹 tooltip）。
   *  非加密市场不支持 —— 轮询 loop 的价格源写死 binance/futures。 */
  drawMode?: boolean;
  /** 用户点了一个价位、确认要在此挂预警。父层负责 POST 与刷新列表。 */
  onDrawLevel?: (price: number) => void;
  /** 删除一条已挂预警（非画线模式下点线即删）。 */
  onDeleteAlert?: (id: number) => void;

  /** ICT 摆动点：swing 分层标记（ST/IT/LT）。四层共用一次请求，全关时不发。 */
  showIctSwings?: boolean;
  /** ICT 破位线：BOS/MSS 水平位。 */
  showIctEvents?: boolean;
  /** ICT 时段带：伦敦/纽约 killzone 主图背景竖带。 */
  showIctKillzone?: boolean;
  /** ICT 溢价/折价：当前交易区间的溢价/均衡/折价三条分位线。 */
  showIctDealingRange?: boolean;
  /**
   * 四层的显示参数（图元上限 / 档位 / 时段 / 分位阈值）。
   * 不传 = 用 DEFAULT_ICT_DISPLAY；参数只影响"画多少、画哪些"，不影响检测口径。
   */
  ictDisplay?: IctDisplaySettings;
  /** @deprecated 用 ictDisplay.events.breakOn；保留仅为旧调用点不爆。 */
  ictBreakOn?: "close" | "wick";
}

export interface DecisionOverlay {
  price: number;
  color: string;
  title: string;
  lineStyle?: LineStyle;
}

/** 手画预警线（批3）。id 是后端规则 id，用于删除；price 是画线价。 */
export interface AlertLine {
  id: number;
  price: number;
}

/** 手上画的这条线在 levels 里的槽位名。后端 _GENERIC_LEVEL_NAMES 认它。 */
export const DRAWN_LEVEL_KEY = "price";

/** 手画线颜色。与 PA 的入场金/止损红/止盈绿都不同色 —— 画线是用户自己
 *  放的，不表示任何交易意图（后端也按无方向的"穿越"判定）。 */
export const ALERT_LINE_COLOR = "#4aa3ff";

/** 从 rule.levels 里取手画线的价位。没有/非有限数 → null（不画、不发）。
 *  后端把 levels 当 dict[str, float|None]，所以两种形状都要扛住。 */
export function drawnLevelPrice(levels: Record<string, unknown> | null | undefined): number | null {
  const v = levels?.[DRAWN_LEVEL_KEY];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** 挑出属于当前图（symbol + interval）的手画线。
 *
 *  symbol 比较用**统一形式**：轮询 loop 直接按 `get_ticker(symbol, ...)` 查
 *  缓存，而缓存写入的是 `to_unified()` 的结果（BTC/USDT），读取侧零归一化。
 *  所以这里必须按同一形式比，否则线画出来了但永远不会触发（静默空转）。 */
export function alertLinesFor(
  alerts: { id: number; symbol?: string | null; interval?: string | null; levels?: Record<string, unknown> | null }[],
  symbol: string,
  interval: string,
): AlertLine[] {
  const want = symbol.trim().toUpperCase();
  return alerts
    .filter((a) => (a.symbol ?? "").trim().toUpperCase() === want && a.interval === interval)
    .map((a) => ({ id: a.id, price: drawnLevelPrice(a.levels) }))
    .filter((l): l is AlertLine => l.price !== null);
}

function ema(values: number[], period: number): number[] {
  if (values.length === 0) return [];
  const k = 2 / (period + 1);
  const out: number[] = [values[0]];
  for (let i = 1; i < values.length; i++) {
    out.push(values[i] * k + out[out.length - 1] * (1 - k));
  }
  return out;
}

// MACD(12,26,9):DIF = EMA12 - EMA26,DEA = DIF 的 EMA9,柱 = 2×(DIF-DEA)
function computeMacd(values: number[]) {
  if (values.length < 26) return { dif: [] as number[], dea: [] as number[], hist: [] as number[] };
  const ema12 = ema(values, 12);
  const ema26 = ema(values, 26);
  const dif = values.map((_, i) => ema12[i] - ema26[i]);
  const dea = ema(dif, 9);
  const hist = dif.map((v, i) => 2 * (v - dea[i]));
  return { dif, dea, hist };
}

// 简单移动平均（真 MA）。此前「MA20」实为 EMA(20)（历史错位），picker 同时
// 列出 MA/EMA 后两者会完全重合，故 MA 回归真 SMA：前 period-1 个点无值。
export function sma(values: number[], period: number): { start: number; values: number[] } {
  if (values.length < period) return { start: values.length, values: [] };
  const out: number[] = [];
  let sum = 0;
  for (let i = period - 1; i < values.length; i++) {
    if (i === period - 1) {
      for (let j = 0; j < period; j++) sum += values[j];
    } else {
      sum += values[i] - values[i - period];
    }
    out.push(sum / period);
  }
  return { start: period - 1, values: out };
}

// BOLL(20,2)：中轨 SMA20，上下轨 ±2σ（总体标准差，与主流行情软件一致）。
export function computeBoll(
  values: number[],
  period = 20,
  mult = 2,
): { start: number; mid: number[]; upper: number[]; lower: number[] } {
  const s = sma(values, period);
  const mid = s.values;
  const upper: number[] = [];
  const lower: number[] = [];
  for (let k = 0; k < mid.length; k++) {
    const i = s.start + k;
    let sq = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const d = values[j] - mid[k];
      sq += d * d;
    }
    const sd = Math.sqrt(sq / period);
    upper.push(mid[k] + mult * sd);
    lower.push(mid[k] - mult * sd);
  }
  return { start: s.start, mid, upper, lower };
}

// RSI(14) Wilder 平滑：首值 = 前 period 个涨跌幅的简单均值，
// 其后 avg = (prev×(period-1) + cur) / period。输出从 index=period 起。
export function computeRsi(values: number[], period = 14): { start: number; values: number[] } {
  const out: number[] = [];
  if (values.length <= period) return { start: values.length, values: out };
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= period; i++) {
    const d = values[i] - values[i - 1];
    if (d >= 0) gain += d;
    else loss -= d;
  }
  gain /= period;
  loss /= period;
  out.push(loss === 0 ? 100 : 100 - 100 / (1 + gain / loss));
  for (let i = period + 1; i < values.length; i++) {
    const d = values[i] - values[i - 1];
    gain = (gain * (period - 1) + Math.max(d, 0)) / period;
    loss = (loss * (period - 1) + Math.max(-d, 0)) / period;
    out.push(loss === 0 ? 100 : 100 - 100 / (1 + gain / loss));
  }
  return { start: period, values: out };
}

// KDJ(9,3,3)：RSV = 9 周期随机值；K/D 递推平滑（K₀=D₀=50），J = 3K − 2D。
export function computeKdj(
  rows: { high: number; low: number; close: number }[],
  period = 9,
  kSm = 3,
  dSm = 3,
): { start: number; k: number[]; d: number[]; j: number[] } {
  const out = { start: period - 1, k: [] as number[], d: [] as number[], j: [] as number[] };
  if (rows.length < period) return out;
  let kPrev = 50;
  let dPrev = 50;
  for (let i = period - 1; i < rows.length; i++) {
    let hh = -Infinity;
    let ll = Infinity;
    for (let j = i - period + 1; j <= i; j++) {
      if (rows[j].high > hh) hh = rows[j].high;
      if (rows[j].low < ll) ll = rows[j].low;
    }
    const rsv = hh === ll ? 50 : ((rows[i].close - ll) / (hh - ll)) * 100;
    kPrev = (kPrev * (kSm - 1) + rsv) / kSm;
    dPrev = (dPrev * (dSm - 1) + kPrev) / dSm;
    out.k.push(kPrev);
    out.d.push(dPrev);
    out.j.push(3 * kPrev - 2 * dPrev);
  }
  return out;
}

// ATR(14) Wilder：TR = max(H−L, |H−PC|, |L−PC|)，首值 = 前 period 个 TR 均值，
// 其后 (prev×(period-1) + TR) / period。输出从 index=period 起。
export function computeAtr(
  rows: { high: number; low: number; close: number }[],
  period = 14,
): { start: number; values: number[] } {
  const out: number[] = [];
  if (rows.length <= period) return { start: rows.length, values: out };
  let trSum = 0;
  for (let i = 1; i <= period; i++) {
    const pc = rows[i - 1].close;
    trSum += Math.max(rows[i].high - rows[i].low, Math.abs(rows[i].high - pc), Math.abs(rows[i].low - pc));
  }
  let atr = trSum / period;
  out.push(atr);
  for (let i = period + 1; i < rows.length; i++) {
    const pc = rows[i - 1].close;
    const tr = Math.max(rows[i].high - rows[i].low, Math.abs(rows[i].high - pc), Math.abs(rows[i].low - pc));
    atr = (atr * (period - 1) + tr) / period;
    out.push(atr);
  }
  return { start: period, values: out };
}

// WS tick 直达路径（kf卡vs QD 2026-09-22）：每秒一次的当前-bar 更新不进
// SWR→toRows→全量指标重算管线，而是从 store 拿 WsKline 走 rAF 合帧
// series.update() 单根直更。指标用递推状态 O(1) 更新末点。
// SWR 30s 权威快照仍照常覆盖（视觉层 update 不碰 rowsRef）。

/** EMA 递推一步：out = k*x + (1-k)*prev。 */
export function emaStep(prev: number, x: number, period: number): number {
  const k = 2 / (period + 1);
  return x * k + prev * (1 - k);
}

/** MACD 递推状态（EMA12/EMA26/DIF/DEA 四个标量随 tick 演进）。 */
export interface MacdState {
  ema12: number;
  ema26: number;
  dea: number;
}

/** 从全量序列播种 MACD 递推状态（末根的 EMA12/EMA26/DEA）。 */
export function seedMacdState(closes: number[]): MacdState | null {
  if (closes.length < 26) return null;
  const e12 = ema(closes, 12);
  const e26 = ema(closes, 26);
  const dif: number[] = [];
  for (let i = 0; i < closes.length; i++) dif.push(e12[i] - e26[i]);
  const dea = ema(dif, 9);
  return { ema12: e12[e12.length - 1], ema26: e26[e26.length - 1], dea: dea[dea.length - 1] };
}

/** 递推一步 MACD：返回 [dif, hist]（ema12/ema26/dea 状态在参数上演进）。 */
export function stepMacd(state: MacdState, close: number): { dif: number; hist: number } {
  state.ema12 = emaStep(state.ema12, close, 12);
  state.ema26 = emaStep(state.ema26, close, 26);
  const dif = state.ema12 - state.ema26;
  state.dea = emaStep(state.dea, dif, 9);
  return { dif, hist: 2 * (dif - state.dea) };
}

/** WS 注入的单点更新是否会改变图上末根 bar（决定是否需要 series.update）。 */
export function tickChangesBar(
  last: { time: number; open: number; high: number; low: number; close: number },
  tick: { ts: number; open: number; high: number; low: number; close: number },
): boolean {
  return (
    tick.ts !== last.time ||
    tick.open !== last.open ||
    tick.high !== last.high ||
    tick.low !== last.low ||
    tick.close !== last.close
  );
}

export interface KfRow {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

/** W3-A 冻结 clamp:只挡右侧新 bar(time > frozenAsOf),更早的行全部放行
 *  (左侧滚动加载历史的追加路径因此不受影响)。frozenAsOf 为 null/undefined
 *  或全部行都更旧时原样返回。 */
export function clampRowsForFreeze(rows: KfRow[], frozenAsOf?: number | null): KfRow[] {
  if (frozenAsOf == null) return rows;
  if (rows.length === 0) return rows;
  const last = rows[rows.length - 1];
  if (last.time <= frozenAsOf) return rows;
  return rows.filter((r) => r.time <= frozenAsOf);
}

/** P1#2 冻结锚对齐:优先用 staleAsOf(AI 窗口末)作锚——图表末 bar 可能比
 *  AI 快照新(未收盘 bar 没进诊断窗口),按图末自锚会展示 AI 没看过的 bar。
 *  staleAsOf 为 null(首次/无记录)回落图末自捕获;两处皆无 → null。 */
export function resolveFrozenAnchor(
  staleAsOf: number | null | undefined,
  rows: KfRow[],
): number | null {
  if (staleAsOf != null) return staleAsOf;
  return rows.length > 0 ? rows[rows.length - 1].time : null;
}

/** W3-B 保鲜差距:AI 所见截至 staleAsOf 之后进了几根新 K 线。staleAsOf 为
 *  null/undefined 或没有更新的 bar → 0(徽章不显示)。 */
export function countStaleBars(rows: KfRow[], staleAsOf?: number | null): number {
  if (staleAsOf == null) return 0;
  let n = 0;
  for (let i = rows.length - 1; i >= 0; i--) {
    if ((rows[i].time as number) > staleAsOf) n++;
    else break;
  }
  return n;
}

/** W3-D 收盘倒计时基准:末根 bar 的开盘时刻换算回 ms(KfRow.time 是 epoch **秒**,
 *  与 klines.db / toRows 的除法配套)+ 周期得收盘时刻。空 rows / 周期未收录 → null。 */
export function closeAtFromRows(rows: KfRow[], interval?: string | null): number | null {
  const last = rows.length ? rows[rows.length - 1] : null;
  return closeAtFrom(last ? last.time * 1000 : null, interval);
}

/** 周期 → 毫秒。与 close-countdown 的 INTERVAL_MS 同源(键集 = 两种 tf 来源的并集),
 *  未收录 → 不猜数字(buildSlots 收到非有限值会原样返回,不留空)。 */
const KF_INTERVAL_MS: Record<string, number> = {
  "1m": 60_000,
  "5m": 300_000,
  "15m": 900_000,
  "30m": 1_800_000,
  "1h": 3_600_000,
  "4h": 14_400_000,
  "1d": 86_400_000,
  "1w": 604_800_000,
};

/** klines → 排序后的 KfRow(渲染管线与保鲜徽章共用同一映射,含 ms→秒)。 */
function toRows(klines: Kline[]): (KfRow & { time: UTCTimestamp })[] {
  return klines
    .map((k) => ({
      time: Math.floor((k.open_time ?? k.timestamp ?? 0) / 1000) as UTCTimestamp,
      open: k.open,
      high: k.high,
      low: k.low,
      close: k.close,
    }))
    .sort((a, b) => (a.time as number) - (b.time as number));
}

/** 渲染槽:真实 bar(row 非空)或空白点(row=null,只有 time)。 */
export interface KfSlot {
  time: UTCTimestamp;
  row: (KfRow & { time: UTCTimestamp }) | null;
}

// 缺口在图上"撑开"而非被压缩:lightweight-charts 按**数据点索引**排布,不是按时间。
// 2026-10-09 实测:给出 00:00,01:00,[跳 5 根],06:00 —— 相邻两点 x 差 6px,
// 跨洞两点 x 差**也是 6px**(洞被压缩)。插 whitespace 点后跨洞 x 差 = 30px(撑开 5 倍)。
// 故真洞只有 materialize 成 whitespace 才可见。
//
// 注意:本函数只造**排布用**的空白槽,不造 OHLCV —— 价格数据仍只有真实的。
// 指标数学(MA/MACD/RSI…)一律跑在真实 rows 上,不受空白槽影响。
//
// intervalSec 单位是**秒**,与 rows[].time(epoch 秒)一致 —— 不是毫秒。
// (曾用毫秒:gap(秒) > intervalMs(毫秒)×1.5 差 1000 倍,洞永不检出。测试抓出。)
// 缺口判定容差:相邻两 bar 间隔超过 1.5 个周期才算缺口(容非整倍周期抖动)。
const GAP_FACTOR = 1.5;
const MAX_FILL = 20000; // 空白槽安全上界(1m × 14 天 ≈ 20k),防病态数据撑爆 DOM

/** 两个相邻 bar 之间缺几根(0 = 不算缺口)。单位:秒。
 *  buildSlots 与 findRowGaps 共用此判定,避免两处阈值/公式漂移。 */
export function gapMissing(prevTime: number, nextTime: number, intervalSec: number): number {
  if (!Number.isFinite(intervalSec) || intervalSec <= 0) return 0;
  const gap = nextTime - prevTime;
  if (gap <= intervalSec * GAP_FACTOR) return 0;
  return Math.max(0, Math.ceil(gap / intervalSec) - 1);
}

export function buildSlots(
  rows: (KfRow & { time: UTCTimestamp })[],
  intervalSec: number,
): KfSlot[] {
  if (rows.length === 0) return [];
  // 周期未知/非法 → 不猜间隔,原样返回(宁可不留空也不造错位的空白)
  if (!Number.isFinite(intervalSec) || intervalSec <= 0) {
    return rows.map((r) => ({ time: r.time, row: r }));
  }
  const out: KfSlot[] = [];
  for (let i = 0; i < rows.length; i++) {
    out.push({ time: rows[i].time, row: rows[i] });
    if (i === rows.length - 1) break;
    const base = rows[i].time as number;
    const n = Math.min(gapMissing(base, rows[i + 1].time as number, intervalSec), MAX_FILL);
    for (let k = 1; k <= n; k++) {
      out.push({ time: (base + k * intervalSec) as UTCTimestamp, row: null });
    }
  }
  return out;
}

/** 一处缺口(epoch 秒)。from = 洞左沿最后一根真 bar,to = 洞右沿下一根真 bar。 */
export interface RowGap {
  from: number;
  to: number;
  missing: number;
}

/** 扫描 rows 汇总所有缺口(供顶部提示条)。 */
export function findRowGaps(
  rows: (KfRow & { time: UTCTimestamp })[],
  intervalSec: number,
): RowGap[] {
  if (!Number.isFinite(intervalSec) || intervalSec <= 0) return [];
  const out: RowGap[] = [];
  for (let i = 0; i < rows.length - 1; i++) {
    const from = rows[i].time as number;
    const to = rows[i + 1].time as number;
    const missing = gapMissing(from, to, intervalSec);
    if (missing > 0) out.push({ from, to, missing });
  }
  return out;
}

/** 渲染路径判定（纯函数,kf 效应守卫的可测核心）。
 *  prev 为上一轮已渲染态;返回:
 *  - "skip" 数据与指标配置均未变（当前 bar 的逐秒实时刷新走 WS tick 直达路径,不经本判定）
 *  - "append" 尾部恰好新增一根（增量 update;指标配置变化时必须走 full 重建,
 *    否则新指标 series 永不创建——toggle bug 结构性根因)
 *  - "full" 其余（首次/换标的换周期/历史追加/多根变化/指标配置变化）
 *
 *  key = 这组行的身份（`${symbol}@${tf}`）。**必须**比,不能只比形状：
 *  交易所对任何标的/周期都返回"最近 N 根、末根对齐当前时刻",所以换标的时
 *  (length, lastTs) 天然相同 —— 实测 BTC 1h 与 ETH 1h 都是 n=1500,
 *  首 1786669200000、末 1792065600000。只比形状会把换标的判成 skip,
 *  setData 永不执行、画布留在旧标的,tooltip 读 rowsMapRef 也还是旧标的
 *  （2026-10-15 实测 ~4s 残留窗口）。换周期同理会撞（两周期都返回全量根数）。
 *  key 省略时退化为旧行为（仅形状比较），仅供不关心身份的调用方使用。 */
export type KfRenderPath = "skip" | "append" | "full";

export function pickRenderPath(
  prev: { length: number; lastTs: number; key?: string },
  rows: KfRow[],
  indsChanged: boolean,
  key?: string,
): KfRenderPath {
  const isFirstLoad = prev.length === 0;
  if (isFirstLoad) return "full";
  // 身份变了 → 无条件全量重建。哪怕形状一模一样(换标的/换周期),也得重画。
  if (key !== undefined && prev.key !== key) return "full";
  const lastTs = rows.length ? (rows[rows.length - 1].time as number) : 0;
  if (prev.length === rows.length && prev.lastTs === lastTs) {
    return indsChanged ? "full" : "skip";
  }
  if (prev.length === rows.length - 1 && prev.lastTs < lastTs) {
    return indsChanged ? "full" : "append";
  }
  return "full";
}

export function KfChart({
  showMA = false,
  showVOL = false,
  showMACD = false,
  showEma20 = false,
  showEma50 = false,
  showBoll = false,
  showRsi = false,
  showKdj = false,
  showAtr = false,
  symbolOverride,
  tfOverride,
  decisionOverlays,
  tickerLast,
  frozen = false,
  staleAsOf,
  autoSubmit,
  onCloseElapsed,
  alertLines,
  drawMode = false,
  onDrawLevel,
  onDeleteAlert,

  showIctSwings = false,
  showIctEvents = false,
  showIctKillzone = false,
  showIctDealingRange = false,
  ictDisplay = DEFAULT_ICT_DISPLAY,
  ictBreakOn,
}: KfChartProps) {
  const tr = useUiText();
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerReady, setContainerReady] = useState(false);
  // 同一份 DOM 节点的 state 镜像:绘图图层要作为 effect 依赖,ref 改了不触发重跑。
  const [containerEl, setContainerEl] = useState<HTMLDivElement | null>(null);
  const [chartReady, setChartReady] = useState(false);
  // 待确认的落点（点图后先画一条虚线，等用户按「挂上」才 POST）。
  const [pendingLevel, setPendingLevel] = useState<number | null>(null);
  // 回调装进 ref：subscribeClick 注册一次就得活到卸载，但回调会随 render 变。
  const drawModeRef = useRef(drawMode);
  const onDrawLevelRef = useRef(onDrawLevel);
  const onDeleteAlertRef = useRef(onDeleteAlert);
  const alertLinesRef = useRef<AlertLine[]>(alertLines ?? []);
  useEffect(() => {
    drawModeRef.current = drawMode;
    onDrawLevelRef.current = onDrawLevel;
    onDeleteAlertRef.current = onDeleteAlert;
    alertLinesRef.current = alertLines ?? [];
  }, [drawMode, onDrawLevel, onDeleteAlert, alertLines]);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const maSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const volSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const macdLineRef = useRef<ISeriesApi<"Line"> | null>(null);
  const macdSignalRef = useRef<ISeriesApi<"Line"> | null>(null);
  const macdHistRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  // picker 新增指标 series refs（EMA/BOLL 主图叠层；RSI/KDJ/ATR 独立窗格）
  const ema20SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const ema50SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const bollMidRef = useRef<ISeriesApi<"Line"> | null>(null);
  const bollUpperRef = useRef<ISeriesApi<"Line"> | null>(null);
  const bollLowerRef = useRef<ISeriesApi<"Line"> | null>(null);
  const rsiSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const kdjKRef = useRef<ISeriesApi<"Line"> | null>(null);
  const kdjDRef = useRef<ISeriesApi<"Line"> | null>(null);
  const kdjJRef = useRef<ISeriesApi<"Line"> | null>(null);
  const atrSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  // ICT 结构层：markers 插件随 K 线 series 创建/销毁；价格线按响应重建。
  const markersApiRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  // ICT 时段门背景带：画在主图 background 层，随 series 挂/摘。
  const killzonePrimitiveRef = useRef<KillzonePrimitive | null>(null);
  // 主图实际排布的时间轴（含补空白槽后的刻度），供背景带换算像素。
  // 与 rowsRef 同生共死，写入口同是下面的 setRows。
  const barTimesRef = useRef<number[]>([]);
  const tipRef = useRef<HTMLDivElement>(null);
  // 绘图图层的"时间轴快照":与蜡烛 series 的槽位一一对应(含 buildSlots 补出的
  // 空白槽)。走 ref 不走 state —— WS 每 ~1s 注入一次,挂 state 会让整棵
  // KfChart 每秒重渲染。写入后调 drawingLayerRef.redraw() 通知图层取新值。
  const slotRef = useRef<{ times: readonly number[]; stepSec: number }>({ times: [], stepSec: NaN });
  const drawingLayerRef = useRef<DrawingLayerHandle>(null);
  const rowsRef = useRef<{ time: number; open: number; high: number; low: number; close: number }[]>([]);
  // crosshair tooltip O(1) 查找：rowsRef 每次整体替换时同步重建（Map 查找替代 find() 扫 1500 根）
  const rowsMapRef = useRef<Map<number, { time: number; open: number; high: number; low: number; close: number }>>(new Map());
  // 当前 rowsRef 里这组行**属于谁**（`${symbol}@${tf}`）。WS tick 直达路径与
  // tickerLast 路径都从 rowsRef 取"末根"来 update 单根 —— 它们只能校验**来消息的**
  // symbol（入口），无法防止把新标的的价写到旧标的的末根上（出口）。两者末根
  // 时间戳天然相同（见 pickRenderPath 注释），tick.ts === last.time 恒成立,
  // 于是产生"旧标的 open/high/low + 新标的 close"的嵌合 K 线（"K线出错"）。
  // 出口比对这个 key,不一致就整条通道不执行。
  const dataKeyRef = useRef<string>("");
  // rowsRef 唯一写入口：同步重建 Map（tooltip O(1) 查找的数据源）
  const setRows = useCallback(
    (
      rows: { time: number; open: number; high: number; low: number; close: number }[],
      key: string,
    ) => {
      rowsRef.current = rows;
      barTimesRef.current = rows.map((r) => r.time);
      // 身份与数据同生共死：写 rows 的同一处写 key，杜绝"行换了、key 忘了换"。
      dataKeyRef.current = key;
      const map = new Map<number, { time: number; open: number; high: number; low: number; close: number }>();
      for (const r of rows) map.set(r.time, r);
      rowsMapRef.current = map;
    },
    [],
  );
  const prevDataRef = useRef<{ length: number; lastTs: number; firstTs: number; key: string }>({ length: 0, lastTs: 0, firstTs: 0, key: "" });
  // 绘图槽位快照(见 slotRef 注释)。必须在 setData/update 之后写:此时图表的
  // 价格轴已经按新数据重算,图层立即重绘才不会出现"锚点晚一帧才对齐"。
  const syncDrawingSlots = useCallback(
    (rows: { time: number; open: number; high: number; low: number; close: number }[], stepSec: number) => {
      const slots = buildSlots(rows as unknown as (KfRow & { time: UTCTimestamp })[], stepSec);
      slotRef.current = {
        times: slots.length > 0 ? slots.map((s) => s.time as number) : [rows[0].time],
        stepSec,
      };
      drawingLayerRef.current?.redraw();
    },
    [],
  );
  // 上次渲染的指标配置。切换 MA/VOL/MACD 时数据不变、但需要重建 series,
  // 情况1 guard 若只比数据会误 return,导致按钮切换不生效。
  const indicatorsRef = useRef({ ma: showMA, vol: showVOL, macd: showMACD, ema20: showEma20, ema50: showEma50, boll: showBoll, rsi: showRsi, kdj: showKdj, atr: showAtr });
  // W3-A 冻结锚点:P1#2 对齐 — frozen 期间锚跟随 staleAsOf(有则 AI 窗口末,
  // 无则图末自捕获),frozen 翻 false 归 null。staleAsOf 在 runAnalysis 起跑时
  // 置 null(paRecord 清空),所以活分析流回落现行为;响应落库后锚自动收紧到
  // AI 窗口末,此后新 bar 不再上图。
  const frozenAsOfRef = useRef<number | null>(null);
  useEffect(() => {
    if (frozen) {
      frozenAsOfRef.current = resolveFrozenAnchor(staleAsOf, rowsRef.current);
    } else {
      frozenAsOfRef.current = null;
    }
  }, [frozen, staleAsOf]);

  // WS 断流指示:marketsStreamState=degraded（超过 5s 无 WS 消息）时图上
  // 显示「行情延迟」徽章。数据源此时由 use-klines 的 10s REST 收紧轮询兜底。
  const wsDegraded = useMarketStore((s) => s.marketsStreamState === "degraded");

  const containerRefCallback = useCallback((el: HTMLDivElement | null) => {
    containerRef.current = el;
    setContainerEl(el);
    setContainerReady(!!el);
  }, []);
  // 绘图图层要拿 chart/series 实例当 effect 依赖。它们是引擎内部对象,
  // 生命周期与容器绑定 —— 用 state 镜像一次,免得图层读 ref 读到 null 还没人通知它。
  const [chartApi, setChartApi] = useState<IChartApi | null>(null);
  const [seriesApi, setSeriesApi] = useState<ISeriesApi<"Candlestick"> | null>(null);

  // ── 手绘对象树 ────────────────────────────────────────────────────────
  // 走全局 store:同标的多格布局下,一条线要同时出现在每一格里。
  const drawingsHydrated = useDrawingsStore((s) => s.hydrated);
  const hydrateDrawings = useDrawingsStore((s) => s.hydrate);
  const allGrids = useDrawingsStore((s) => s.grids);
  const selectedDrawingIds = useDrawingsStore((s) => s.selectedIds);
  const activeDrawingTool = useDrawingsStore((s) => s.activeTool);
  const drawingPrefs = useDrawingsStore((s) => s.prefs);
  /** 图表外观(背景/网格/K 线色)。建图时取快照,变更走 applyOptions。 */
  const chartAppearance = useDrawingsStore((s) => s.appearance);
  const setActiveDrawingTool = useDrawingsStore((s) => s.setActiveTool);
  const createDrawing = useDrawingsStore((s) => s.createFromDrag);
  const moveDrawing = useDrawingsStore((s) => s.move);
  const selectDrawing = useDrawingsStore((s) => s.select);
  const toggleDrawingSelection = useDrawingsStore((s) => s.toggleSelect);
  const selectRangeTo = useDrawingsStore((s) => s.selectRangeTo);
  const removeDrawings = useDrawingsStore((s) => s.removeMany);
  const clearDrawingSelection = useDrawingsStore((s) => s.clearSelection);
  useEffect(() => {
    if (!drawingsHydrated) hydrateDrawings();
  }, [drawingsHydrated, hydrateDrawings]);
  const { pageFilter } = useContextStore();
  const symbol = symbolOverride ?? pageFilter.symbol;
  const tf = tfOverride ?? pageFilter.tf;
  const exchange = pageFilter.exchange === "all" ? "binance" : pageFilter.exchange;

  const tradingSym = /USDT$|USD$|PERP$/.test(symbol) ? symbol : `${symbol}USDT`;
  // 这张图的身份：换任一维（标的/周期）都算换图。渲染管线、tooltip、WS tick
  // 出口全按它判定"手上这组行是不是当前图的"。exchange 也纳入 —— 同标的同周期
  // 换交易所同样是另一组数据。
  const dataKey = `${tradingSym}@${tf}@${exchange}`;

  const { klines, error, isLoading, source, fromCache, loadMore } = useKlines(tradingSym, tf, exchange, "futures");

  // ICT 叠加层数据。四层（摆动点 / 破位 / 时段 / 分位）共用同一个响应，
  // 全关时不发请求（query=null → SWR key=null）；只开一层也拉整份，因为后端
  // 一次算完四层，多拆请求只会让四层落在不同 bar 快照上。
  // limit 必须跟 K 线窗口同源（KLINE_FETCH_LIMIT），不能比它小：叠加层是按 bar
  // 覆盖的，短了远端就整段空白——之前写死 500 而本图拉 1500 根，时段带只剩右侧
  // 三分之一有，左侧全是裸 K 线。
  const anyIctLayer = showIctSwings || showIctEvents || showIctKillzone || showIctDealingRange;
  // 破位口径走显示参数；旧的单值 prop 只作兜底（老调用点没传 ictDisplay）。
  const breakOn = ictBreakOn ?? ictDisplay.events.breakOn;
  // 只有会影响**后端计算**的旋钮进 query：破位口径、时段、时区、分位参数。
  // 图元上限/档位/不透明度是纯前端取舍，留在渲染期，改了不该触发重拉。
  const kz = ictDisplay.killzone;
  const dr = ictDisplay.dealingRange;
  const ictQuery = anyIctLayer
    ? {
        exchange,
        marketType: "futures",
        symbol: tradingSym,
        interval: tf,
        limit: KLINE_FETCH_LIMIT,
        breakOn,
        sessions: sessionsParam(kz.sessions),
        tzOffsetHours: kz.tzOffsetHours,
        pdPeriod: dr.period,
        premiumThreshold: dr.premiumThreshold,
        discountThreshold: dr.discountThreshold,
      }
    : null;
  const { data: ictStructure } = useIctStructure(ictQuery, { refreshInterval: 120_000 });

  // 图元在渲染期算好（纯函数），effect 只负责喂给 chart。两者都受图元上限约束，
  // 所以徽章要报「画出来的数」而不是「响应里的数」——否则会显示 34 却只画了 20 个。
  const swingMarkers = useMemo(
    () =>
      showIctSwings && ictStructure
        ? buildSwingMarkers({
            swings: ictStructure.swings,
            events: ictStructure.events,
            maxSwings: ictDisplay.swings.maxCount,
            tiers: ictDisplay.swings.tiers,
            swingSide: ictDisplay.swings.side,
          })
        : [],
    [ictStructure, showIctSwings, ictDisplay.swings],
  );
  const structureLines = useMemo(
    () =>
      showIctEvents && ictStructure
        ? buildStructureLines({
            swings: ictStructure.swings,
            events: ictStructure.events,
            maxEvents: ictDisplay.events.maxCount,
            eventKinds: ictDisplay.events.kinds,
            eventSide: ictDisplay.events.side,
          })
        : [],
    [ictStructure, showIctEvents, ictDisplay.events],
  );
  // 时段门：每根 bar 是否落在伦敦/纽约 killzone 内。画成主图背景竖带而不是
  // 独立窗格——时段是时间轴上的底色，开窗格只能挤成几十像素一条或抢走主图一半高度。
  const killzonePoints = useMemo(
    () => (showIctKillzone && ictStructure ? buildKillzonePoints(ictStructure.killzone) : []),
    [ictStructure, showIctKillzone],
  );
  const killzoneBands = useMemo(() => buildKillzoneBands(killzonePoints), [killzonePoints]);
  const killzoneFill = useMemo(
    () => killzoneBandFill(ictDisplay.killzone.opacity),
    [ictDisplay.killzone.opacity],
  );
  // 溢价/折价：当前交易区间的三条分位线，与 BOS/MSS 破位线同一渲染路径。
  const dealingRangeLines = useMemo(
    () =>
      showIctDealingRange && ictStructure
        ? buildDealingRangeLines(ictStructure.dealing_range, ictDisplay.dealingRange.lines)
        : [],
    [ictStructure, showIctDealingRange, ictDisplay.dealingRange.lines],
  );
  // 滚动加载更早历史状态：loadMoreRef 供滚动监听取最新回调；loadingMoreRef 防并发；
  // noMoreRef 标记已到最早数据尽头（true 后不再触发翻页请求）
  const loadMoreRef = useRef(loadMore);
  const loadingMoreRef = useRef(false);
  const noMoreRef = useRef(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [noMore, setNoMore] = useState(false);
  useEffect(() => {
    loadMoreRef.current = loadMore;
  }, [loadMore]);
  // WS kline 每 ~1s 乐观注入一次,klines 引用持续变。useDeferredValue 把图表重算
  // 降为低优先级——用户点击/滚动等交互优先,图表在空闲帧追赶,避免高频注入抢占主线程。
  //
  // 身份必须与数据**成对**延迟：只推迟 klines 时,换标的那一帧会出现"新 dataKey +
  // 旧标的的行"共存,那一帧会把旧行连同新 key 一起写进 rowsRef(setRows) —— 而下一帧
  // 真数据到齐时形状又恰好相同(见 pickRenderPath 注释),于是被判 skip,新数据永不
  // 上屏,残留复发。包成对象一起推迟,过渡帧按 key 直接拒绝渲染(见下方 effect 首行)。
  const liveKlines = useMemo(() => ({ key: dataKey, klines }), [dataKey, klines]);
  const deferredLive = useDeferredValue(liveKlines);
  const deferredKlines = deferredLive.klines;

  // P0b cold start instrumentation: mark on symbol change, measure on first kline render
  useEffect(() => {
    perf.mark("kf_symbol_change");
  }, [tradingSym, tf]);

  // 切换品种/周期时重置"已到最早"标记，新窗口可从最左继续滚动加载
  useEffect(() => {
    noMoreRef.current = false;
    setNoMore(false);
  }, [tradingSym, tf]);
  useEffect(() => {
    if (!klines || klines.length === 0) return;
    perf.mark("kf_first_data");
    perf.measure("kf_time_to_data", "kf_symbol_change", "kf_first_data");
  }, [klines]);

  useEffect(() => {
    if (!containerReady || !containerRef.current || chartRef.current) return;
    // 外观从 store 读,但**不进依赖数组**:改一个色不该重建整张图(会丢视区、
    // 重订阅 WS 回调)。建图时取一次快照,后续变更走下面那个 applyOptions effect。
    const look = useDrawingsStore.getState().appearance;
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      layout: {
        background: { color: look.background.kind === "solid" ? look.background.color : "transparent" },
        textColor: "#a0a8b8",
        fontSize: 10,
      },
      grid: {
        vertLines: { color: look.gridColor || defaultGridColor(), visible: look.gridVisible },
        horzLines: { color: look.gridColor || defaultGridColor(), visible: look.gridVisible },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
      },
      timeScale: { timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.08)" },
    });
    const series = chart.addSeries(CandlestickSeries, appearanceToSeriesOptions(look));
    chartRef.current = chart;
    seriesRef.current = series;
    setChartApi(chart);
    setSeriesApi(series);

    setChartReady(true);

    // 批3：点图交互。两条路——
    //  - drawMode 开：落一条待确认虚线，等用户确认再 POST。
    //  - drawMode 关：点已有手画线附近 = 删掉它（线的删除入口，不用额外的列表 UI）。
    // 判定用**像素距离**而非价格百分比：视区缩放后同样 8px 对应的价格差是变的，
    // 按价格比会把"看起来贴着"和"其实很远"判错。
    const HIT_PX = 6;
    const clickHandler = (param: Parameters<Parameters<typeof chart.subscribeClick>[0]>[0]) => {
      const y = param.point?.y;
      if (typeof y !== "number") return;
      const price = series.coordinateToPrice(y);
      if (price === null) return;
      const rounded = Number(price.toFixed(2));
      if (!Number.isFinite(rounded)) return;

      if (drawModeRef.current) {
        setPendingLevel(rounded);
        return;
      }
      const del = onDeleteAlertRef.current;
      if (!del) return;
      for (const line of alertLinesRef.current) {
        const ly = series.priceToCoordinate(line.price);
        if (ly !== null && Math.abs(ly - y) <= HIT_PX) {
          del(line.id);
          return;
        }
      }
    };
    chart.subscribeClick(clickHandler);

    markersApiRef.current = createSeriesMarkers(series, []);

    // P1-1 crosshair tooltip
    const crosshairHandler = (param: Parameters<Parameters<typeof chart.subscribeCrosshairMove>[0]>[0]) => {
      const tip = tipRef.current;
      if (!tip) return;
      if (!param.time || !param.point) {
        tip.style.display = "none";
        return;
      }
      const row = rowsMapRef.current.get(param.time as number);
      if (!row) {
        tip.style.display = "none";
        return;
      }
      const chg = row.close - row.open;
      const chgPct = row.open !== 0 ? (chg / row.open) * 100 : 0;
      const up = chg >= 0;
      const color = up ? "#61bd67" : "#f3625d";
      const fmt = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 2 });
      tip.innerHTML =
        `<div class="kf-tip-time">${new Date(row.time * 1000).toLocaleString("zh-CN", { hour12: false })}</div>`
        + `<div class="kf-tip-row">O <span>${fmt(row.open)}</span></div>`
        + `<div class="kf-tip-row">H <span>${fmt(row.high)}</span></div>`
        + `<div class="kf-tip-row">L <span>${fmt(row.low)}</span></div>`
        + `<div class="kf-tip-row">C <span>${fmt(row.close)}</span></div>`
        + `<div class="kf-tip-chg" style="color:${color}">${up ? "+" : ""}${fmt(chg)} (${up ? "+" : ""}${chgPct.toFixed(2)}%)</div>`;
      tip.style.display = "block";
      const containerW = containerRef.current?.clientWidth ?? 600;
      const tipW = 140;
      const left = param.point.x + tipW + 12 > containerW ? param.point.x - tipW - 8 : param.point.x + 12;
      tip.style.left = `${Math.max(4, left)}px`;
      tip.style.top = `${Math.max(4, (param.point.y ?? 0) - 10)}px`;
    };
    chart.subscribeCrosshairMove(crosshairHandler);

    // 滚动到最左端 → 加载更早历史（end_time 向后翻页）
    chart.timeScale().subscribeVisibleLogicalRangeChange((logicalRange) => {
      if (!logicalRange) return;
      const from = logicalRange.from as number;
      // from<5 表示视区左缘逼近数据最左端；noMore 后不再触发
      if (from < 5 && !loadingMoreRef.current && !noMoreRef.current) {
        const fn = loadMoreRef.current;
        if (fn) {
          loadingMoreRef.current = true;
          setLoadingMore(true);
          fn()
            .then((added) => {
              if (added === 0) {
                noMoreRef.current = true;
                setNoMore(true);
              }
            })
            .finally(() => {
              loadingMoreRef.current = false;
              setLoadingMore(false);
            });
        }
      }
    });

    const onResize = () => {
      if (containerRef.current) {
        chart.applyOptions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        });
      }
    };
    window.addEventListener("resize", onResize);
    const ro = new ResizeObserver(onResize);
    ro.observe(containerRef.current);

    return () => {
      window.removeEventListener("resize", onResize);
      ro.disconnect();
      chart.remove();
      setChartReady(false);
      chartRef.current = null;
      seriesRef.current = null;
      setChartApi(null);
      setSeriesApi(null);

      markersApiRef.current = null;
      maSeriesRef.current = null;
      volSeriesRef.current = null;
      macdLineRef.current = null;
      macdSignalRef.current = null;
      macdHistRef.current = null;
      ema20SeriesRef.current = null;
      ema50SeriesRef.current = null;
      bollMidRef.current = null;
      bollUpperRef.current = null;
      bollLowerRef.current = null;
      rsiSeriesRef.current = null;
      kdjKRef.current = null;
      kdjDRef.current = null;
      kdjJRef.current = null;
      atrSeriesRef.current = null;
    };
  }, [containerReady]);

  // 图表外观变更 → 就地改选项。不重建图:重建会丢掉视区位置和历史滚动状态,
  // 每调一次色都要重新拖回原处,没法"边调边看"。
  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!chart || !series) return;
    chart.applyOptions(appearanceToChartOptions(chartAppearance));
    series.applyOptions(appearanceToSeriesOptions(chartAppearance));
  }, [chartAppearance]);

  useEffect(() => {
    if (!seriesRef.current || !deferredKlines) return;
    // 过渡帧守卫：deferredLive 是身份+数据成对推迟的,若其 key 还停在上一张图,
    // 这一帧的 klines 属于旧标的 —— 绝不能拿它去判定/写 rowsRef（会把旧行盖上
    // 新 key,后续真数据因形状相同被判 skip）。等下一帧 key 对齐即可。
    if (deferredLive.key !== dataKey) return;
    const allRows = toRows(deferredKlines);

    if (allRows.length === 0) return;

    // W3-A 冻结:捕获 effect 在 frozen 翻转时已锚定 rowsRef 尾部;这里兜底处理
    // "冻结瞬间图上无数据" 的边缘(首批到达即锚)。之后所有管线只看 clamp 后的行。
    if (frozen && frozenAsOfRef.current == null) {
      frozenAsOfRef.current = allRows[allRows.length - 1].time as number;
    }
    let rows = allRows;
    if (frozenAsOfRef.current != null) {
      const clamped = clampRowsForFreeze(allRows as unknown as KfRow[], frozenAsOfRef.current);
      if (clamped.length !== allRows.length) {
        rows = clamped as typeof allRows;
      }
    }
    if (rows.length === 0) return;

    const lastTs = rows[rows.length - 1].time as number;
    const firstTs = rows[0].time as number;
    const prev = prevDataRef.current;
    const isFirstLoad = prev.length === 0;
    // 换标的/换周期/换交易所 → 视同首次:视区要重新 fitContent（新标的价区间
    // 与旧标的无关,沿用旧视区会显示出界/空白）。
    const identityChanged = prev.key !== dataKey;

    // 路径判定（纯函数 pickRenderPath）:
    const indsChanged =
      indicatorsRef.current.ma !== showMA ||
      indicatorsRef.current.vol !== showVOL ||
      indicatorsRef.current.macd !== showMACD ||
      indicatorsRef.current.ema20 !== showEma20 ||
      indicatorsRef.current.ema50 !== showEma50 ||
      indicatorsRef.current.boll !== showBoll ||
      indicatorsRef.current.rsi !== showRsi ||
      indicatorsRef.current.kdj !== showKdj ||
      indicatorsRef.current.atr !== showAtr;
    let path = pickRenderPath(prev, rows as unknown as KfRow[], indsChanged, dataKey);
    if (path === "skip") return;
    const stepSec = (KF_INTERVAL_MS[tf] ?? NaN) / 1000;
    // append 的 update() 只追加一根、不做空白槽排布。若新末根与上一根**不相邻**
    // (WS 断线后补来的 bar,中间隔了洞),append 会把洞压缩 —— 强制走 full,让
    // buildSlots 把洞撑开。(相邻追加是常态,仍走 append 保持增量性能。)
    if (path === "append" && rows.length >= 2) {
      const deltaGap =
        (rows[rows.length - 1].time as number) - (rows[rows.length - 2].time as number);
      if (Number.isFinite(stepSec) && deltaGap > stepSec * 2) path = "full";
    }
    indicatorsRef.current = { ma: showMA, vol: showVOL, macd: showMACD, ema20: showEma20, ema50: showEma50, boll: showBoll, rsi: showRsi, kdj: showKdj, atr: showAtr };

    // picker 指标数据同步(0923):对已存在的 picker series 全量 setData。
    // 情况4 建 series 后灌数据;情况2 新 bar 时重跑——顺带把上一根 bar 的收盘
    // 终值补正(当前 bar 内不逐秒重算,上一根的末点要等新 bar 到达才定稿)。
    const syncPickerData = (rs: typeof rows) => {
      const chart = chartRef.current;
      if (!chart) return;
      const closes = rs.map((r) => r.close);
      if (ema20SeriesRef.current) {
        const v = ema(closes, 20);
        ema20SeriesRef.current.setData(rs.map((r, i) => ({ time: r.time, value: v[i] })));
      }
      if (ema50SeriesRef.current) {
        const v = ema(closes, 50);
        ema50SeriesRef.current.setData(rs.map((r, i) => ({ time: r.time, value: v[i] })));
      }
      if (bollMidRef.current && bollUpperRef.current && bollLowerRef.current) {
        const b = computeBoll(closes);
        bollMidRef.current.setData(rs.slice(b.start).map((r, i) => ({ time: r.time, value: b.mid[i] })));
        bollUpperRef.current.setData(rs.slice(b.start).map((r, i) => ({ time: r.time, value: b.upper[i] })));
        bollLowerRef.current.setData(rs.slice(b.start).map((r, i) => ({ time: r.time, value: b.lower[i] })));
      }
      if (rsiSeriesRef.current) {
        const rsi = computeRsi(closes);
        rsiSeriesRef.current.setData(rs.slice(rsi.start).map((r, i) => ({ time: r.time, value: rsi.values[i] })));
      }
      if (kdjKRef.current && kdjDRef.current && kdjJRef.current) {
        const kd = computeKdj(rs);
        kdjKRef.current.setData(rs.slice(kd.start).map((r, i) => ({ time: r.time, value: kd.k[i] })));
        kdjDRef.current.setData(rs.slice(kd.start).map((r, i) => ({ time: r.time, value: kd.d[i] })));
        kdjJRef.current.setData(rs.slice(kd.start).map((r, i) => ({ time: r.time, value: kd.j[i] })));
      }
      if (atrSeriesRef.current) {
        const at = computeAtr(rs);
        atrSeriesRef.current.setData(rs.slice(at.start).map((r, i) => ({ time: r.time, value: at.values[i] })));
      }
    };

    // 情况2：新增一条 K 线（实时推送新K线）— 增量 update
    // indsChanged 时不可走增量（series 未按新配置重建），落到情况4全量重建
    if (path === "append") {
      const newKline = rows[rows.length - 1];
      seriesRef.current.update({
        time: newKline.time,
        open: newKline.open,
        high: newKline.high,
        low: newKline.low,
        close: newKline.close,
      });
      if (volSeriesRef.current) {
        volSeriesRef.current.update({
          time: newKline.time,
          value: Math.abs(newKline.close - newKline.open) * 10,
          color:
            newKline.close >= newKline.open
              ? "rgba(97, 189, 103, 0.6)"
              : "rgba(243, 98, 93, 0.6)",
        });
      }
      if (maSeriesRef.current) {
        // MA 增量更新：真 SMA(20) 末点（历史错位修复：此前实为 EMA）
        const closes = rows.map((r) => r.close);
        const s = sma(closes, 20);
        const lastVal = s.values.length ? s.values[s.values.length - 1] : null;
        if (lastVal != null) {
          maSeriesRef.current.update({
            time: newKline.time,
            value: lastVal,
          });
        }
      }
      if (macdLineRef.current) {
        // MACD 增量更新：重算最后一条 DIF/DEA/柱
        const closes = rows.map((r) => r.close);
        const { dif, dea, hist } = computeMacd(closes);
        const last = dif.length - 1;
        macdLineRef.current.update({ time: newKline.time, value: dif[last] });
        macdSignalRef.current?.update({ time: newKline.time, value: dea[last] });
        macdHistRef.current?.update({
          time: newKline.time,
          value: hist[last],
          color:
            hist[last] >= 0
              ? "rgba(97, 189, 103, 0.5)"
              : "rgba(243, 98, 93, 0.5)",
        });
      }
      syncPickerData(rows);
      setRows(rows as unknown as { time: number; open: number; high: number; low: number; close: number }[], dataKey);
      syncDrawingSlots(rows, stepSec);
      prevDataRef.current = { length: rows.length, lastTs, firstTs, key: dataKey };
      return;
    }

    // full 路径：数据量变化大/首次加载/指标配置变化 — 全量 setData + isHistoryAppend 视区保持
    // （main 侧原"情况3：当前K线价格更新"分支条件与情况1 完全相同，永不可达，已删；
    //   当前 bar tick 更新由 tickerLast 通道处理，不走 K 线重渲染路径。）
    const isHistoryAppend =
      !isFirstLoad &&
      !identityChanged &&
      rows.length > prev.length &&
      firstTs !== prev.firstTs &&
      lastTs === prev.lastTs;

    let savedRange: { from: number; to: number } | null = null;
    if (isHistoryAppend && chartRef.current) {
      try {
        savedRange = chartRef.current.timeScale().getVisibleLogicalRange();
      } catch { /* ignore */ }
    }

    // 蜡烛 series 用"补空白槽"排布:真洞在图上撑开(见 buildSlots 注释)。
    // 其它 series(MA/VOL/MACD/RSI…)仍喂真实 rows —— 时间轴是所有 series 的
    // 并集,蜡烛的空白槽会自动把它们对齐到正确位置(2026-10-09 实测),故指标
    // 侧零改动、数学不受影响。
    const candleData = buildSlots(rows, (KF_INTERVAL_MS[tf] ?? NaN) / 1000).map((s) =>
      s.row
        ? { time: s.time, open: s.row.open, high: s.row.high, low: s.row.low, close: s.row.close }
        : { time: s.time },
    );
    seriesRef.current.setData(candleData);
    setRows(rows as unknown as { time: number; open: number; high: number; low: number; close: number }[], dataKey);
    syncDrawingSlots(rows as unknown as { time: number; open: number; high: number; low: number; close: number }[], stepSec);

    // 重建 MA/VOL series（参数变化或首次加载）
    if (chartRef.current) {
      // MA/VOL/MACD 不在此处 remove——它们走下方懒创建 + visible 开关。
      // 高频 removeSeries/setData/addSeries 交错会把 LWC timescale 打成永久
      // 内部空态（getVisibleLogicalRange 永久 null、无异常），表现为整图空白
      // 且不可恢复（2026-10-01 实机取证）。
      // 以下 picker 叠层仍按需建删：RSI/KDJ/ATR 占独立窗格，visible 开关会让
      // 空窗格残留在图上（LWC 不会因 series 全隐而回收 pane）。
      if (ema20SeriesRef.current) {
        chartRef.current.removeSeries(ema20SeriesRef.current);
        ema20SeriesRef.current = null;
      }
      if (ema50SeriesRef.current) {
        chartRef.current.removeSeries(ema50SeriesRef.current);
        ema50SeriesRef.current = null;
      }
      if (bollMidRef.current) {
        chartRef.current.removeSeries(bollMidRef.current);
        bollMidRef.current = null;
      }
      if (bollUpperRef.current) {
        chartRef.current.removeSeries(bollUpperRef.current);
        bollUpperRef.current = null;
      }
      if (bollLowerRef.current) {
        chartRef.current.removeSeries(bollLowerRef.current);
        bollLowerRef.current = null;
      }
      if (rsiSeriesRef.current) {
        chartRef.current.removeSeries(rsiSeriesRef.current);
        rsiSeriesRef.current = null;
      }
      if (kdjKRef.current) {
        chartRef.current.removeSeries(kdjKRef.current);
        kdjKRef.current = null;
      }
      if (kdjDRef.current) {
        chartRef.current.removeSeries(kdjDRef.current);
        kdjDRef.current = null;
      }
      if (kdjJRef.current) {
        chartRef.current.removeSeries(kdjJRef.current);
        kdjJRef.current = null;
      }
      if (atrSeriesRef.current) {
        chartRef.current.removeSeries(atrSeriesRef.current);
        atrSeriesRef.current = null;
      }

      // MA：懒创建 + visible 开关 + 常驻 setData（热路径零 removeSeries/addSeries）
      if (!maSeriesRef.current) {
        maSeriesRef.current = chartRef.current.addSeries(LineSeries, {
          visible: showMA,
          color: "#cf9b00",
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        });
      } else if (maSeriesRef.current.options().visible !== showMA) {
        maSeriesRef.current.applyOptions({ visible: showMA });
      }
      if (showMA) {
        const closes = rows.map((r) => r.close);
        // 真 SMA(20)：历史错位修复（此前画的是 EMA 却标 MA20）
        const s = sma(closes, 20);
        maSeriesRef.current.setData(
          rows.slice(s.start).map((r, i) => ({
            time: r.time,
            value: s.values[i],
          })),
        );
      }

      // VOL
      if (!volSeriesRef.current) {
        volSeriesRef.current = chartRef.current.addSeries(HistogramSeries, {
          visible: showVOL,
          priceFormat: { type: "volume" },
          priceScaleId: "vol",
          color: "rgba(50, 102, 154, 0.5)",
        });
        volSeriesRef.current.priceScale().applyOptions({
          scaleMargins: { top: 0.85, bottom: 0 },
        });
      } else if (volSeriesRef.current.options().visible !== showVOL) {
        volSeriesRef.current.applyOptions({ visible: showVOL });
      }
      if (showVOL) {
        volSeriesRef.current.setData(
          rows.map((r) => ({
            time: r.time,
            value: Math.abs(r.close - r.open) * 10,
            color:
              r.close >= r.open
                ? "rgba(97, 189, 103, 0.6)"
                : "rgba(243, 98, 93, 0.6)",
          })),
        );
      }

      // MACD：三 series 同组 visible 开关（"macd" scale 由首个 addSeries 创建）
      if (!macdHistRef.current) {
        macdHistRef.current = chartRef.current.addSeries(HistogramSeries, {
          visible: showMACD,
          priceScaleId: "macd",
          priceFormat: { type: "price", precision: 2, minMove: 0.01 },
        });
        macdHistRef.current.priceScale().applyOptions({
          scaleMargins: { top: 0.78, bottom: 0 },
        });
      } else if (macdHistRef.current.options().visible !== showMACD) {
        macdHistRef.current.applyOptions({ visible: showMACD });
      }
      if (!macdLineRef.current) {
        macdLineRef.current = chartRef.current.addSeries(LineSeries, {
          visible: showMACD,
          priceScaleId: "macd",
          color: "#e3b650", /* 正典金=var(--color-gold) */
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        });
      } else if (macdLineRef.current.options().visible !== showMACD) {
        macdLineRef.current.applyOptions({ visible: showMACD });
      }
      if (!macdSignalRef.current) {
        macdSignalRef.current = chartRef.current.addSeries(LineSeries, {
          visible: showMACD,
          priceScaleId: "macd",
          color: "#5fb6f3",
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        });
      } else if (macdSignalRef.current.options().visible !== showMACD) {
        macdSignalRef.current.applyOptions({ visible: showMACD });
      }
      if (showMACD) {
        const closes = rows.map((r) => r.close);
        const { dif, dea, hist } = computeMacd(closes);
        if (dif.length > 0) {
          macdHistRef.current.setData(
            rows.map((r, i) => ({
              time: r.time,
              value: hist[i],
              color:
                hist[i] >= 0
                  ? "rgba(97, 189, 103, 0.5)"
                  : "rgba(243, 98, 93, 0.5)",
            })),
          );
          macdLineRef.current.setData(
            rows.map((r, i) => ({
              time: r.time,
              value: dif[i],
            })),
          );
          macdSignalRef.current.setData(
            rows.map((r, i) => ({
              time: r.time,
              value: dea[i],
            })),
          );
        }
      }

      // picker 指标建档(0923):EMA/BOLL 叠主图 pane 0(PS「同画布图层」),
      // RSI/KDJ/ATR addSeries 第三参 paneIndex 自动开独立窗格(PS「独立画板」)。
      // 每窗格用独立 priceScaleId + scaleMargins 压高度,主图 stretchFactor 压窗格。
      if (showEma20 || showEma50 || showBoll || showRsi || showKdj || showAtr) {
        try {
          chartRef.current.panes()[0]?.setStretchFactor?.(10);
        } catch { /* ignore */ }
      }
      if (showEma20) {
        const closes = rows.map((r) => r.close);
        const v = ema(closes, 20);
        const line = chartRef.current.addSeries(LineSeries, {
          color: "#5fb6f3",
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        line.setData(rows.map((r, i) => ({ time: r.time, value: v[i] })));
        ema20SeriesRef.current = line;
      }
      if (showEma50) {
        const closes = rows.map((r) => r.close);
        const v = ema(closes, 50);
        const line = chartRef.current.addSeries(LineSeries, {
          color: "#b57de8",
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        line.setData(rows.map((r, i) => ({ time: r.time, value: v[i] })));
        ema50SeriesRef.current = line;
      }
      if (showBoll) {
        const closes = rows.map((r) => r.close);
        const b = computeBoll(closes);
        const mid = chartRef.current.addSeries(LineSeries, {
          color: "#cf9b00",
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        mid.setData(rows.slice(b.start).map((r, i) => ({ time: r.time, value: b.mid[i] })));
        bollMidRef.current = mid;
        const chart = chartRef.current;
        const mkEdge = (color: string) =>
          chart.addSeries(LineSeries, {
            color,
            lineWidth: 1,
            lineStyle: LineStyle.Dotted,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          });
        const upper = mkEdge("#4d8f6b");
        upper.setData(rows.slice(b.start).map((r, i) => ({ time: r.time, value: b.upper[i] })));
        bollUpperRef.current = upper;
        const lower = mkEdge("#4d8f6b");
        lower.setData(rows.slice(b.start).map((r, i) => ({ time: r.time, value: b.lower[i] })));
        bollLowerRef.current = lower;
      }
      if (showRsi) {
        const closes = rows.map((r) => r.close);
        const rsi = computeRsi(closes);
        if (rsi.values.length > 0) {
          const line = chartRef.current.addSeries(
            LineSeries,
            {
              color: "#c98fe4",
              lineWidth: 1,
              priceLineVisible: false,
              lastValueVisible: false,
              priceFormat: { type: "price", precision: 1, minMove: 0.1 },
            },
            chartRef.current.panes().length,
          );
          line.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.05 } });
          line.setData(rows.slice(rsi.start).map((r, i) => ({ time: r.time, value: rsi.values[i] })));
          rsiSeriesRef.current = line;
        }
      }
      if (showKdj) {
        const kd = computeKdj(rows);
        if (kd.k.length > 0) {
          // K/D/J 三线同窗格：目标索引只取一次（= 现有窗格数，addSeries 自动新建）
          const kdChart = chartRef.current;
          const paneIdx = kdChart.panes().length;
          const mk = (color: string) =>
            kdChart.addSeries(
              LineSeries,
              {
                color,
                lineWidth: 1,
                priceLineVisible: false,
                lastValueVisible: false,
                priceFormat: { type: "price", precision: 1, minMove: 0.1 },
              },
              paneIdx,
            );
          const k = mk("#5fb6f3");
          k.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.05 } });
          k.setData(rows.slice(kd.start).map((r, i) => ({ time: r.time, value: kd.k[i] })));
          kdjKRef.current = k;
          const d = mk("#e3b650");
          d.setData(rows.slice(kd.start).map((r, i) => ({ time: r.time, value: kd.d[i] })));
          kdjDRef.current = d;
          const j = mk("#b57de8");
          j.setData(rows.slice(kd.start).map((r, i) => ({ time: r.time, value: kd.j[i] })));
          kdjJRef.current = j;
        }
      }
      if (showAtr) {
        const at = computeAtr(rows);
        if (at.values.length > 0) {
          const line = chartRef.current.addSeries(
            LineSeries,
            {
              color: "#8a9aa8",
              lineWidth: 1,
              priceLineVisible: false,
              lastValueVisible: false,
              priceFormat: { type: "price", precision: 2, minMove: 0.01 },
            },
            chartRef.current.panes().length,
          );
          line.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.05 } });
          line.setData(rows.slice(at.start).map((r, i) => ({ time: r.time, value: at.values[i] })));
          atrSeriesRef.current = line;
        }
      }
    }

    // 视区：首次加载/换标的换周期 fitContent，历史追加保持视区偏移，其他情况不动
    if (isFirstLoad || identityChanged) {
      chartRef.current?.timeScale().fitContent();
    } else if (isHistoryAppend && savedRange && chartRef.current) {
      const addedCount = rows.length - prev.length;
      try {
        chartRef.current.timeScale().setVisibleLogicalRange({
          from: savedRange.from + addedCount,
          to: savedRange.to + addedCount,
        });
      } catch { /* ignore */ }
    }

    prevDataRef.current = { length: rows.length, lastTs, firstTs, key: dataKey };
  }, [deferredKlines, deferredLive.key, dataKey, tf, setRows, showMA, showVOL, showMACD, showEma20, showEma50, showBoll, showRsi, showKdj, showAtr, frozen]);

  // WS ticker 实时价驱动最后一根 K 线 close 跳动（不依赖 useKlines 30s 轮询）。
  // tickerLast 每次变化（~10s REST poller 推一次）→ 更新最后 K 线的 close/high/low。
  // 注意：只做视觉层 series.update，不碰 rowsRef（SWR 权威快照）——
  // 下次 klines useEffect 跑时用真实 K线数据覆盖，避免 mutation 污染 prevDataRef 判断。
  useEffect(() => {
    if (frozen) return; // W3-A 冻结期间最后一根 K 线不跳动(图 = AI 所见)
    if (tickerLast == null || !Number.isFinite(tickerLast)) return;
    const series = seriesRef.current;
    const rows = rowsRef.current;
    if (!series || rows.length === 0) return;
    // 出口校验:rowsRef 里这组行必须属于当前图。tickerLast 来自 kf-page 的
    // wsTicker/tickerReal —— 换标的那几帧它们是上一个标的的价(旧值未清或
    // REST 兜底迟到),若拿它去 update 新图的末根,就会把旧标的的价画到新图上。
    if (dataKeyRef.current !== dataKey) return;

    const last = rows[rows.length - 1];
    // 用实时价更新最后 K 线：close=实时价，high/low 突破则扩展
    const newHigh = Math.max(last.high, tickerLast);
    const newLow = Math.min(last.low, tickerLast);
    if (tickerLast === last.close && newHigh === last.high && newLow === last.low) return;

    series.update({
      time: last.time as UTCTimestamp,
      open: last.open,
      high: newHigh,
      low: newLow,
      close: tickerLast,
    });
    // VOL 柱也跟着重算（close 变了，实体长度变）
    if (volSeriesRef.current) {
      volSeriesRef.current.update({
        time: last.time as UTCTimestamp,
        value: Math.abs(tickerLast - last.open) * 10,
        color: tickerLast >= last.open
          ? "rgba(97, 189, 103, 0.6)"
          : "rgba(243, 98, 93, 0.6)",
      });
    }
  }, [tickerLast, frozen, dataKey]);

  // WS tick 直达路径（kf卡vs QD 2026-09-22）：当前-bar 逐秒更新不经 SWR/deferred
  // 管线，直接 rAF 合帧 series.update 单根。指标递推 O(1) 演进，替代每 tick 全量重算。
  // 语义与 tickerLast 相同：只做视觉层，不碰 rowsRef（SWR 快照才是权威）。
  const lastWsKline = useMarketStore((s) => s.lastWsKline);
  const macdStateRef = useRef<MacdState | null>(null);
  const rafRef = useRef<number | null>(null);
  const pendingTickRef = useRef<{ ts: number; open: number; high: number; low: number; close: number } | null>(null);

  // 全量 setData / 增量路径后重播种递推状态（新 bar、重建 series、切品种周期都会走这）
  useEffect(() => {
    if (!rowsRef.current.length) return;
    const closes = rowsRef.current.map((r) => r.close);
    macdStateRef.current = seedMacdState(closes);
  }, [deferredKlines, showMA, showMACD]);

  useEffect(() => {
    if (frozen) return;
    if (!lastWsKline) return;
    const wk = lastWsKline.kline;
    if (wk.interval !== tf) return;
    if (wk.symbol) {
      const normWs = wk.symbol.replace(/[/\-]/g, "").toUpperCase();
      const normCur = (symbol ?? "").replace(/[/\-]/g, "").toUpperCase();
      if (normWs !== normCur) return;
    }
    pendingTickRef.current = {
      ts: Math.floor((wk.timestamp ?? wk.open_time ?? 0) / 1000),
      open: wk.open,
      high: wk.high,
      low: wk.low,
      close: wk.close,
    };
    if (rafRef.current != null) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null;
      const tick = pendingTickRef.current;
      pendingTickRef.current = null;
      const series = seriesRef.current;
      const rows = rowsRef.current;
      if (!tick || !series || rows.length === 0) return;
      // 出口校验:上面只校验了**来消息的** symbol(入口),这里校验**要写的**那根
      // bar 属于当前图(出口)。换标时 rowsRef 可能仍是旧标的的行,而两标的末根
      // 时间戳相同 → tick.ts === last.time 成立 → 会在旧标的末根上写入新标的的
      // close,造出"旧 open/high/low + 新 close"的嵌合 K 线。dataKeyRef 不一致
      // 时整条通道不执行,等主 effect 把新数据 setData 上来。
      if (dataKeyRef.current !== dataKey) return;
      const last = rows[rows.length - 1];

      if (tick.ts === (last.time as number)) {
        // 当前 bar：视觉层单根直更（真实快照由 30s REST 回填）
        if (!tickChangesBar(last, tick)) return;
        series.update({
          time: last.time as UTCTimestamp,
          open: last.open,
          high: Math.max(last.high, tick.high),
          low: Math.min(last.low, tick.low),
          close: tick.close,
        });
        if (volSeriesRef.current) {
          volSeriesRef.current.update({
            time: last.time as UTCTimestamp,
            value: Math.abs(tick.close - last.open) * 10,
            color: tick.close >= last.open ? "rgba(97, 189, 103, 0.6)" : "rgba(243, 98, 93, 0.6)",
          });
        }
        if (maSeriesRef.current) {
          // SMA(20) 末点 = 最近 19 根收盘 + tick.close（rowsRef 尾根 close 是
          // REST 旧快照，被 tick 取代）。窗口 20 次 add，与 EMA 递推同量级。
          const n = rows.length;
          const cnt = Math.min(20, n);
          let sum = tick.close;
          for (let i = n - cnt; i < n - 1; i++) sum += rows[i].close;
          maSeriesRef.current.update({ time: last.time as UTCTimestamp, value: sum / cnt });
        }
        if (macdLineRef.current && macdStateRef.current) {
          const { dif, hist } = stepMacd(macdStateRef.current, tick.close);
          macdLineRef.current.update({ time: last.time as UTCTimestamp, value: dif });
          macdSignalRef.current?.update({ time: last.time as UTCTimestamp, value: macdStateRef.current.dea });
          macdHistRef.current?.update({
            time: last.time as UTCTimestamp,
            value: hist,
            color: hist >= 0 ? "rgba(97, 189, 103, 0.5)" : "rgba(243, 98, 93, 0.5)",
          });
        }
        // 实时价改了最后一根的 high/low → 自动价格刻度会重算 → 绘图线的 y 跟着变。
        // 槽位没变(还是这批 bar),只让图层按当前刻度重投影。
        drawingLayerRef.current?.redraw();
      } else if (tick.ts > (last.time as number)) {
        // 新 bar 开始：不动（等 SWR 管线走情况2正规增量，含 rowsRef/prevDataRef 一致性）
        return;
      }
    });
    return () => {
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [lastWsKline, frozen, tf, symbol, dataKey]);

  // 换图（标的/周期/交易所）时清掉**不经过渲染管线**的残留状态。这些 ref 都不被
  // pickRenderPath 看见,主 effect 重建数据也不会碰它们,不清就会带着上一张图的
  // 递推状态继续算:
  //  - macdStateRef:EMA12/26/DEA 递推种子来自 rowsRef 末根,旧标的的种子会让
  //    新标的的 MACD 从错误的起点演进(末点错,且要等下次全量重播种才纠正)。
  //  - pendingTickRef:换图瞬间在途的 WS tick,属于旧图。
  //  - rafRef:挂在旧图上的回调,它内部会再查一次 dataKey,但直接取消更干净。
  //  - noMore/loadingMore:旧标的"已到最早"不代表新标的也到最早。
  useEffect(() => {
    macdStateRef.current = null;
    pendingTickRef.current = null;
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    noMoreRef.current = false;
    loadingMoreRef.current = false;
  }, [dataKey]);

  // AI 决策价格线：入场/SL/TP 画在图上。series 就绪 + overlays 变化时重建。
  // 每次依赖变化先移除旧线再画新的，避免叠加残留。
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    if (!decisionOverlays || decisionOverlays.length === 0) return;

    const lines: IPriceLine[] = decisionOverlays.map((o) =>
      series.createPriceLine({
        price: o.price,
        color: o.color,
        lineStyle: o.lineStyle ?? LineStyle.Dashed,
        lineWidth: 1,
        axisLabelVisible: true,
        title: o.title,
      }),
    );
    return () => {
      for (const line of lines) {
        try {
          series.removePriceLine(line);
        } catch { /* ignore */ }
      }
    };
  }, [decisionOverlays, containerReady]);

  // 批3 手画预警线 + 待确认虚线。两者都是 priceLine，和 decisionOverlays
  // 共用 series，但分开一个 effect：决策线由 AI 结果驱动，手画线由用户操作
  // 驱动，生命周期不同（手画线刷新后要能从列表重建）。
  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !chartReady) return;

    const made: IPriceLine[] = [];
    for (const line of alertLines ?? []) {
      made.push(
        series.createPriceLine({
          price: line.price,
          color: ALERT_LINE_COLOR,
          lineStyle: LineStyle.Solid,
          lineWidth: 1,
          axisLabelVisible: true,
          title: "预警",
        }),
      );
    }
    if (pendingLevel !== null) {
      made.push(
        series.createPriceLine({
          price: pendingLevel,
          color: ALERT_LINE_COLOR,
          lineStyle: LineStyle.Dotted,
          lineWidth: 1,
          axisLabelVisible: true,
          title: "待确认",
        }),
      );
    }
    return () => {
      for (const line of made) {
        try {
          series.removePriceLine(line);
        } catch { /* ignore */ }
      }
    };
  }, [alertLines, pendingLevel, chartReady]);

  // ICT 结构点：swing 分层标记。markers 插件挂在主 K 线 series 上，
  // 数据变化整体替换（setMarkers 是覆盖语义，不用先清空）。
  useEffect(() => {
    const api = markersApiRef.current;
    if (!api) return;
    api.setMarkers(
      showIctSwings
        ? (swingMarkers as unknown as SeriesMarker<Time>[])
        : [],
    );
  }, [swingMarkers, showIctSwings, containerReady]);

  // ICT 结构位（BOS/MSS）与溢价/折价分位线共用同一条渲染路径：两者都是水平
  // 价位线，生命周期相同，合并成一次 create/remove，避免两个 effect 抢同一
  // series 的 priceLine 槽。任一层的开关关掉，就只有另一层的线被画。
  const ictPriceLines = useMemo(
    () => [
      ...(showIctEvents ? structureLines : []),
      ...(showIctDealingRange ? dealingRangeLines : []),
    ],
    [structureLines, dealingRangeLines, showIctEvents, showIctDealingRange],
  );
  useEffect(() => {
    const series = seriesRef.current;
    if (!series || ictPriceLines.length === 0) return;

    const drawn: IPriceLine[] = ictPriceLines.map((l) =>
      series.createPriceLine({
        price: l.price,
        color: l.color,
        lineStyle: LineStyle.Dotted,
        lineWidth: 1,
        axisLabelVisible: false,
        title: l.title,
      }),
    );
    return () => {
      for (const line of drawn) {
        try {
          series.removePriceLine(line);
        } catch { /* ignore — chart 可能已销毁 */ }
      }
    };
  }, [ictPriceLines, containerReady]);

  // ICT 时段门：以 series primitive 画在主图背景层。挂载只跟「时段集合」和
  // series 走；bar 时间轴经 ref 递进去，避免 WS 每秒一根新 bar 就把 primitive
  // 拆了重挂——它内部按当前 timeScale 现算像素，重算由 LWC 的 updateAllViews 触发。
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    if (!showIctKillzone || killzoneBands.length === 0) {
      const stale = killzonePrimitiveRef.current;
      if (stale) {
        try {
          series.detachPrimitive(stale);
        } catch { /* ignore — series 可能已销毁 */ }
        killzonePrimitiveRef.current = null;
      }
      return;
    }

    const primitive = new KillzonePrimitive(
      killzoneBands,
      () => barTimesRef.current,
      killzoneFill,
    );
    series.attachPrimitive(primitive);
    killzonePrimitiveRef.current = primitive;
    return () => {
      try {
        series.detachPrimitive(primitive);
      } catch { /* ignore — series 可能已销毁 */ }
      if (killzonePrimitiveRef.current === primitive) killzonePrimitiveRef.current = null;
    };
  }, [killzoneBands, showIctKillzone, killzoneFill, containerReady]);

  // W3-B 保鲜差距:AI 所见截至 staleAsOf 之后数据源进了几根新 bar。直接数
  // deferredKlines(而非 rowsRef——effect 后写会慢一个渲染周期);冻结期间
  // 新 bar 被 clamp 不上图,但徽章仍如实显示「现实在走」。0 或 null → 不显示。
  const liveRows = deferredKlines ? toRows(deferredKlines) : [];
  const staleCount = countStaleBars(liveRows, staleAsOf);

  // W3-D 活 bar 收盘倒计时。基准必须是**当前**这根 bar 的收盘,不能用 staleAsOf/
  // windowEnd——那是冻结锚,其收盘已成过去 → remaining<=0 → 组件返回 null → 静默消失。
  const closeAt = closeAtFromRows(liveRows, tf);
  const staleTimeLabel =
    staleCount > 0 && staleAsOf != null
      ? new Date(staleAsOf * 1000).toLocaleTimeString("zh-CN", {
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        })
      : null;

  // Task A 缺口提示:图上空白之外,用一行文字说清"这是数据缺口,不是没加载完"。
  // 空白可能被误读成加载中;文字给范围 + 缺失根数,消歧义。
  const gapSec = (KF_INTERVAL_MS[tf] ?? NaN) / 1000;
  const gaps = findRowGaps(liveRows, gapSec);
  const gapSummary =
    gaps.length === 0
      ? null
      : (() => {
          const totalMissing = gaps.reduce((a, g) => a + g.missing, 0);
          // 只列最靠前的一个缺口范围,避免多洞时提示条过长
          const first = gaps[0];
          // 显示**缺失区间**(洞内首根→末根),不是两侧真 bar —— 后者会让人以为两端有数
          const gapStart = first.from + gapSec;
          const gapEnd = first.to - gapSec;
          const fmt = (sec: number) =>
            new Date(sec * 1000).toLocaleDateString("zh-CN", {
              month: "2-digit",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
              hour12: false,
            });
          const more = gaps.length > 1 ? tr(" 等 {n} 处", { n: gaps.length }) : "";
          return tr("数据缺口 · {from}→{to}{more} · 缺 {missing} 根", { from: fmt(gapStart), to: fmt(gapEnd), more, missing: totalMissing.toLocaleString("en-US") });
        })();

  // 徽章只报**当前开着**的层：拆开之后若仍四层全报，关掉的层也会占字数，
  // 又变成"一开一大堆"的读数噪音。空数组 = 不渲染徽章。
  const ictBadgeParts = [
    showIctSwings && `${swingMarkers.length} 结构点`,
    showIctEvents && `${structureLines.length} 破位`,
    showIctKillzone && killzonePoints.length > 0 &&
      `时段 ${killzonePoints.filter((p) => p.value === 1).length}/${killzonePoints.length}`,
    showIctDealingRange && dealingRangeLines.length > 0 &&
      `区间 ${dealingRangeLines.length} 线`,
  ].filter((p): p is string => typeof p === "string");
  const ictBadgeText = ictBadgeParts.length > 0 ? `ICT ${ictBadgeParts.join(" · ")}` : null;
  const indSuffix = `${showMA ? " + MA20" : ""}${showEma20 ? " + EMA20" : ""}${showEma50 ? " + EMA50" : ""}${showBoll ? " + BOLL" : ""}${showVOL ? " + VOL" : ""}${showMACD ? " + MACD" : ""}${showRsi ? " + RSI" : ""}${showKdj ? " + KDJ" : ""}${showAtr ? " + ATR" : ""}`;

  // 本条网格的可见对象:按标的分流,不做周期过滤(锚点是时间+价格,与周期无关)。
  const drawingGrids = useMemo(() => selectVisibleGrids({ grids: allGrids }, symbol), [allGrids, symbol]);
  /**
   * 画布点选。修饰键语义与对象树一致:
   *   · 无修饰 = 单选,点空白清空;
   *   · ⌘/Ctrl = 加选/减选;
   *   · Shift = 扩选区间,区间顺序取"当前标的在画布上能看见的对象";
   *     锚点取上一次点中的那个(不在当前标的里就退化为单选,由 selectRange 处理)。
   */
  const rangeAnchorRef = useRef<string | null>(null);
  const handleDrawingSelect = useCallback(
    (id: string | null, mods?: { shift: boolean; toggle: boolean }) => {
      if (id === null) {
        rangeAnchorRef.current = null;
        selectDrawing(null);
        return;
      }
      if (mods?.shift) {
        const order = drawingGrids.map((g) => g.id);
        const anchor = rangeAnchorRef.current ?? drawingGrids.find((g) => selectedDrawingIds.has(g.id))?.id ?? id;
        selectRangeTo(order, anchor, id);
        return;
      }
      if (mods?.toggle) {
        toggleDrawingSelection(id);
        rangeAnchorRef.current = id;
        return;
      }
      selectDrawing(id);
      rangeAnchorRef.current = id;
    },
    [drawingGrids, selectedDrawingIds, selectDrawing, selectRangeTo, toggleDrawingSelection],
  );
  // 档位标签的小数位跟当前价格量级走:56000 要 1 位,0.0231 要 4 位。
  // 取图上最后一根的收盘 —— 空图时退回 2 位(不猜)。
  const drawingPrecision = useMemo(() => {
    const last = liveRows[liveRows.length - 1];
    return last ? pricePrecision(last.close) : 2;
  }, [liveRows]);

  return (
    <div
      className="flex h-full flex-col"
      style={{ position: "relative" }}
      // 曾用 role="img" —— 但 lightweight-charts 会在这棵子树里注入
      // TradingView 署名 <a>(库许可要求保留),role="img" 会把后代当装饰,
      // axe 报 nested-interactive。figure 保留可访问名称且不吞后代语义。
      role="figure"
      aria-label={`${symbol}-PERP K线${showMA ? " + MA20" : ""}${showEma20 ? " + EMA20" : ""}${showEma50 ? " + EMA50" : ""}${showBoll ? " + BOLL" : ""}${showVOL ? " + VOL" : ""}${showMACD ? " + MACD" : ""}${showRsi ? " + RSI" : ""}${showKdj ? " + KDJ" : ""}${showAtr ? " + ATR" : ""}图`}
    >
      <div
        className="kline-overlay"
        style={{
          position: "absolute",
          top: 8,
          left: 12,
          zIndex: 5,
          pointerEvents: "none",
          fontSize: 10,
          color: "var(--color-ink-3)",
        }}
      >
        {symbol}-PERP {tr("主")} · {tf}
        <span className="kf-source-badge">{exchange} · {tr("永续")}</span>
        {showMA && " · MA20"}
        {showEma20 && " · EMA20"}
        {showEma50 && " · EMA50"}
        {showBoll && " · BOLL"}
        {showVOL && " · VOL"}
        {showMACD && " · MACD"}
        {showRsi && " · RSI"}
        {showKdj && " · KDJ"}
        {showAtr && " · ATR"}
        {ictBadgeText && <span className="kf-ict-badge">{ictBadgeText}</span>}
        {(source === "stale" || fromCache || wsDegraded) && (
          <span className="kf-stale-badge">{tr(wsDegraded ? "行情延迟" : "延迟")}</span>
        )}
        {staleTimeLabel && (
          <span className="kf-freshness-badge">
            {tr("AI 所见截至 {t} · +{n} 根新K线", { t: staleTimeLabel, n: staleCount })}
          </span>
        )}
        {gapSummary && <span className="kf-gap-badge">{gapSummary}</span>}
        {closeAt != null && (
          <CloseCountdown
            key={closeAt}
            closeAt={closeAt}
            autoSubmit={autoSubmit}
            onElapsed={onCloseElapsed}
          />
        )}
      </div>
      <div
        ref={tipRef}
        className="kf-cross-tip"
        style={{
          position: "absolute",
          display: "none",
          zIndex: 20,
          pointerEvents: "none",
          minWidth: 140,
        }}
      />
      {/* 滚动加载历史状态提示：加载中 / 已到最早数据 */}
      {(loadingMore || noMore) && (
        <div
          className="absolute z-20 flex items-center justify-center gap-1 text-[10px]"
          style={{ top: 8, left: "50%", transform: "translateX(-50%)", pointerEvents: "none" }}
        >
          {loadingMore ? (
            <span className="rounded bg-background/80 px-1.5 py-0.5 text-ink-3 border border-border-soft">{tr("加载历史数据…")}</span>
          ) : noMore ? (
            <span className="rounded bg-background/80 px-1.5 py-0.5 text-ink-4 border border-border-soft">{tr("已到最早数据")}</span>
          ) : null}
        </div>
      )}
      <div ref={containerRefCallback} className="h-full w-full" />
      {/* 手绘图层:盖在 K 线画布之上。容器是唯一的流内子元素且铺满整格,
          所以绝对定位 (0,0) 与绘图区左上角重合。 */}
      <DrawingLayer
        ref={drawingLayerRef}
        container={containerEl}
        chart={chartApi}
        series={seriesApi}
        slotRef={slotRef}
        grids={drawingGrids}
        selectedIds={selectedDrawingIds}
        precision={drawingPrecision}
        activeTool={activeDrawingTool}
        magnet={drawingPrefs.magnet}
        barsRef={rowsMapRef}
        symbol={symbol}
        timeframe={tf}
        onCreate={(start, end, sym, tframe, mode) =>
          createDrawing({ start, end, symbol: sym, timeframe: tframe, mode })
        }
        // 连续绘制关着 = 每画完一个回到选择态(TradingView 的 Keep drawing 未开时
        // 就是这个手感)。开着就什么都不做,工具保持武装。
        onPlaced={() => {
          if (!drawingPrefs.keepDrawing) setActiveDrawingTool("select");
        }}
        onMove={moveDrawing}
        onSelect={handleDrawingSelect}
        onDelete={removeDrawings}
        onClearSelection={clearDrawingSelection}
        onCancelTool={() => setActiveDrawingTool("select")}
      />
      {/* 批3 落点确认条。画线是写操作(会在后端注册一条规则)，所以先给一步
          确认再 POST —— 直接点一下就在服务器留东西太容易误触。 */}
      {pendingLevel !== null && (
        <div className="kf-draw-confirm" role="dialog" aria-label="挂价格预警">
          <span className="kf-draw-confirm-price">
            在 {pendingLevel.toLocaleString("en-US", { maximumFractionDigits: 2 })} 提醒我
          </span>
          <button
            type="button"
            className="kf-draw-confirm-ok"
            onClick={() => {
              const p = pendingLevel;
              setPendingLevel(null);
              if (p !== null) onDrawLevelRef.current?.(p);
            }}
          >
            挂上
          </button>
          <button
            type="button"
            className="kf-draw-confirm-cancel"
            onClick={() => setPendingLevel(null)}
          >
            取消
          </button>
        </div>
      )}
      {error && (
        <div className="absolute inset-0 flex items-center justify-center text-[11px] text-ink-3 pointer-events-none">
          {tr("等待 K 线数据…")}
        </div>
      )}
      {isLoading && !error && (
        <div className="absolute inset-0 flex items-center justify-center text-[11px] text-ink-3 pointer-events-none">
          {tr("加载中…")}
        </div>
      )}
    </div>
  );
}

// lib/monitor-feed.ts — 底部监控条纯函数层(无 WS/无副作用,可直接 vitest)

import { identityTranslate, type Translate } from "@/lib/i18n/types";

export const DEFAULT_SYMBOLS = [
  "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE",
  "AVAX", "LINK", "ADA", "DOT", "MATIC", "LTC",
] as const;

// (exchange, market_type) pairs the feed bar subscribes to on open. A-share
// intraday anomalies are published on market:astock:intraday:ALL (the detector's
// own market_type), so they need their own pair — subscribing astock under
// market_type "futures" would register a channel nothing ever publishes to and
// the events would silently never arrive.
export const FEED_SUBSCRIPTIONS = [
  { exchange: "binance", market_type: "futures" },
  { exchange: "okx", market_type: "futures" },
  { exchange: "astock", market_type: "intraday" },
] as const;

export type FeedKind = "liquidation" | "large_trade" | "anomaly";
export type FeedDir = "long" | "short" | "buy" | "sell" | "neutral";
export type FeedTab = "all" | FeedKind | "system";

export interface FeedDraft {
  kind: FeedKind;
  text: string;
  dir: FeedDir;
  symbol?: string;
  /** Raw notional USD for size filtering (large_trade/liquidation only). */
  usd?: number;
  /**
   * 用当前 locale 的 tr 重算整条文本。
   *
   * 适配器在**取数时**就把译文拼进了 `text`(整串随数据变化,源串查表命中
   * 不了,只能拼接时就地译)。条目在队列里最长存活 50 条,期间一次语言
   * 切换会让旧条目保持旧语言 —— 实测 /watch/gex 上就是残留的「信号强度 N」。
   * 保留 `render` 后,渲染层可以用当前 tr 重算,不必重建数据流。
   */
  render?: (tr: Translate) => string;
}

export interface FeedItem extends FeedDraft {
  id: string;
  time: string;
}

export const FEED_CAP = 50;

// ---- 格式化(对齐 tactical 组件写法) ----
export function formatUSD(val: number): string {
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
  return `$${val.toFixed(0)}`;
}

export function formatFeedTime(ts: number): string {
  return new Date(ts).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

// ---- 原始消息契约(后端 WS/REST) ----
export interface LiquidationData {
  symbol?: string;
  side?: string;
  quantity?: number;
  price?: number;
  notional_usd?: number;
}

export interface LargeTradeData {
  exchange?: string;
  symbol?: string;
  side?: string;
  price?: number;
  quantity?: number;
  notional_usd?: number;
  timestamp?: number;
}

export interface SignalData {
  symbol?: string;
  side?: string;
  strength?: number;
}

export interface FundingRateData {
  symbol?: string;
  rate?: number;
  funding_rate?: number;
}

/** A-share intraday anomaly pushed by the S3a detector (AnomalyEvent). */
export interface AnomalyData {
  exchange?: string;
  symbol?: string;
  name?: string;
  kind?: string;
  price?: number;
  prev_close?: number;
  market_type?: string;
}

function base(symbol?: string): string {
  return (symbol ?? "?").split("/")[0].toUpperCase();
}

/**
 * 展示文案翻译器 —— 由调用方(use-monitor-feed)注入当前 locale 的 `tr()`。
 *
 * 为什么适配器要收 tr:下面 `adaptXxx` 产出的是**拼好的动态串**
 * (如 `涨停 600519 茅台 @1786.50 +10.00%`),整串随数据变化,源串查表
 * 永远命中不了。所以只能把「可译的那几段」在**拼接时**就地译好。
 * 默认恒等函数 —— 纯函数测试仍断言中文原文(vitest 不注入 provider)。
 */
/** A-share display code: "600519.SH" → "600519" (the venue suffix is redundant). */
function astockCode(symbol?: string): string {
  return base(symbol).split(".")[0];
}

/**
 * text 与 render 同源:模板只在 build 里写一次,`text` 用调用时的 tr 求值,
 * `render` 留到渲染时用当时的 tr 重算。两处若各写一份模板,改文案必然漏一处。
 */
function draft(build: (tr: Translate) => string, tr: Translate, rest: Omit<FeedDraft, "text" | "render">): FeedDraft {
  return { ...rest, text: build(tr), render: build };
}

// ---- 适配器:原始消息 → FeedDraft ----
export function adaptLiquidation(d: LiquidationData, tr: Translate = identityTranslate): FeedDraft {
  const sym = base(d.symbol);
  const long = d.side === "long";
  const usd = formatUSD(Number(d.notional_usd ?? 0));
  const price = d.price != null && d.price !== 0 ? ` @${d.price}` : "";
  return draft(
    (t) => `${t("爆单")} ${sym} ${t(long ? "多" : "空")} ${usd}${price}`,
    tr,
    {
      kind: "liquidation",
      dir: long ? "long" : "short",
      symbol: sym,
      usd: Number(d.notional_usd ?? 0),
    },
  );
}

export function adaptLargeTrade(d: LargeTradeData, tr: Translate = identityTranslate): FeedDraft {
  const sym = base(d.symbol);
  const buy = d.side === "buy";
  return draft(
    (t) => `${t("主力")} ${t(buy ? "买" : "卖")} ${sym} ${formatUSD(Number(d.notional_usd ?? 0))}`,
    tr,
    {
      kind: "large_trade",
      dir: buy ? "buy" : "sell",
      symbol: sym,
      usd: Number(d.notional_usd ?? 0),
    },
  );
}

export function adaptSignal(d: SignalData, tr: Translate = identityTranslate): FeedDraft {
  const sym = base(d.symbol);
  const side = (d.side ?? "neutral").toLowerCase();
  const strength = Number(d.strength ?? 0);
  const dir: FeedDir = side === "buy" ? "buy" : side === "sell" ? "sell" : "neutral";
  return draft(
    (t) =>
      side === "buy"
        ? `${t("买入信号")} ${sym} ★ ${strength.toFixed(0)}`
        : side === "sell"
          ? `${t("卖出信号")} ${sym} ★ ${strength.toFixed(0)}`
          : `${sym} ${t("信号强度")} ${strength.toFixed(0)}`,
    tr,
    { kind: "anomaly", dir, symbol: sym },
  );
}

export function adaptFundingRate(d: FundingRateData, tr: Translate = identityTranslate): FeedDraft {
  const sym = base(d.symbol);
  const rate = Number(d.rate ?? d.funding_rate ?? 0);
  const dir: FeedDir = rate >= 0 ? "buy" : "sell";
  return draft(
    (t) => `${sym} ${t("资金费率")} ${rate >= 0 ? "+" : ""}${(rate * 100).toFixed(4)}%`,
    tr,
    { kind: "anomaly", dir, symbol: sym },
  );
}

// kind → (Chinese label, feed direction). Direction drives the direction filter
// and colour-class intent: limit-up/break-out read bullish, limit-down/break-down
// bearish; 炸板 (failed limit-up) is a rejection so it reads bearish, 翘板
// (recovery off the floor) bullish. volume_surge is direction-agnostic.
const ANOMALY_LABELS: Record<string, { label: string; dir: FeedDir }> = {
  limit_up: { label: "涨停", dir: "buy" },
  broken_limit_up: { label: "炸板", dir: "sell" },
  limit_down: { label: "跌停", dir: "sell" },
  limit_down_recovery: { label: "翘板", dir: "buy" },
  n_day_high: { label: "N日新高", dir: "buy" },
  n_day_low: { label: "N日新低", dir: "sell" },
  volume_surge: { label: "放量", dir: "neutral" },
};

export function adaptAnomaly(d: AnomalyData, tr: Translate = identityTranslate): FeedDraft {
  const sym = astockCode(d.symbol);
  const meta = ANOMALY_LABELS[d.kind ?? ""] ?? { label: d.kind ?? "异动", dir: "neutral" as FeedDir };
  const name = d.name ? ` ${d.name}` : "";
  const price = d.price != null && d.price !== 0 ? ` @${Number(d.price).toFixed(2)}` : "";
  const prev = Number(d.prev_close ?? 0);
  const pct =
    d.price != null && prev !== 0
      ? ` ${(Number(d.price) / prev - 1) * 100 >= 0 ? "+" : ""}${((Number(d.price) / prev - 1) * 100).toFixed(2)}%`
      : "";
  return draft(
    (t) => `${t(meta.label)} ${sym}${name}${price}${pct}`,
    tr,
    { kind: "anomaly", dir: meta.dir, symbol: sym },
  );
}

// ---- merge reducer:新条目在前,按 id 去重,上限 ----
export function mergeFeedItem(prev: FeedItem[], draft: FeedDraft, seq: number, now: number): FeedItem[] {
  const id = `${draft.kind}-${seq}`;
  if (prev.some((f) => f.id === id)) return prev;
  const item: FeedItem = { ...draft, id, time: formatFeedTime(now) };
  return [item, ...prev].slice(0, FEED_CAP);
}

// ---- tab 过滤 ----
export const FEED_TABS: { key: FeedTab; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "liquidation", label: "爆单" },
  { key: "large_trade", label: "主力" },
  { key: "anomaly", label: "异动" },
  { key: "system", label: "系统" },
];

export function filterByTab(items: FeedItem[], tab: FeedTab): FeedItem[] {
  return tab === "all" ? items : items.filter((f) => f.kind === tab);
}

// ---- 综合筛选:tab + 金额阈值 + 方向 + 币种 ----
export type FeedDirFilter = "all" | "buy" | "sell";

export interface FeedFilter {
  tab?: FeedTab;
  /** 金额阈值(USD)。liquidation/large_trade 有 usd 字段,不达标剔除;anomaly 无 usd 恒通过。 */
  minUsd?: number;
  dir?: FeedDirFilter;
  /** 币种(大小写不敏感,如 "eth")。空 = 不过滤。 */
  symbol?: string;
}

export function filterFeed(items: FeedItem[], f: FeedFilter = {}): FeedItem[] {
  const dirSet = f.dir === "buy" ? new Set(["buy", "long"]) : f.dir === "sell" ? new Set(["sell", "short"]) : null;
  const sym = f.symbol?.trim().toUpperCase();
  return items.filter((item) => {
    if (f.tab && f.tab !== "all" && item.kind !== f.tab) return false;
    if (f.minUsd != null && f.minUsd > 0 && item.usd != null && item.usd < f.minUsd) return false;
    if (dirSet && !dirSet.has(item.dir)) return false;
    if (sym && item.symbol !== sym) return false;
    return true;
  });
}

/** 金额阈值档位(对齐 large-trade-feed 的 whale/large/medium 分级)。 */
export const SIZE_TIERS: { key: string; label: string; minUsd: number }[] = [
  { key: "all", label: "全部", minUsd: 0 },
  { key: "100k", label: "≥$100K", minUsd: 100_000 },
  { key: "500k", label: "≥$500K", minUsd: 500_000 },
  { key: "1m", label: "≥$1M", minUsd: 1_000_000 },
];

export const DIR_FILTERS: { key: FeedDirFilter; label: string }[] = [
  { key: "all", label: "全部方向" },
  { key: "buy", label: "买入/多头" },
  { key: "sell", label: "卖出/空头" },
];

// ---- 金额设置(预设档位 + 自定义阈值) ----
/** 自定义金额档位 key:minUsd 取用户输入,而非 SIZE_TIERS 预设。 */
export const CUSTOM_KEY = "custom";

export interface FeedSettings {
  sizeKey: string; // "all" | "100k" | "500k" | "1m" | "custom"
  customMinUsd: string; // 自定义档位的输入原值(空=不限)
}

export const FEED_FILTER_STORAGE_KEY = "selene-feed-filter";

/** 由档位 key + 自定义输入解析出最终 minUsd。custom 空/非法输入回退 0(不限)。 */
export function resolveMinUsd(sizeKey: string, customMinUsd: string): number {
  if (sizeKey === CUSTOM_KEY) {
    const n = Number(customMinUsd);
    return Number.isFinite(n) && n > 0 ? n : 0;
  }
  return SIZE_TIERS.find((t) => t.key === sizeKey)?.minUsd ?? 0;
}

// ---- 演示数据(demo 兜底,id 带 demo- 前缀以便切换 live 时剔除) ----
export function makeSampleItems(now: number, tr: Translate = identityTranslate): FeedItem[] {
  let seq = 0;
  const mk = (draft: FeedDraft, agoSec: number): FeedItem => {
    seq += 1;
    return { ...draft, id: `demo-${draft.kind}-${seq}`, time: formatFeedTime(now - agoSec * 1000) };
  };
  return [
    mk(adaptLiquidation({ symbol: "BTC", side: "long", notional_usd: 2_500_000, price: 61230 }, tr), 4),
    mk(adaptLiquidation({ symbol: "ETH", side: "short", notional_usd: 980_000, price: 3412 }, tr), 11),
    mk(adaptLargeTrade({ symbol: "SOL", side: "buy", notional_usd: 1_200_000 }, tr), 18),
    mk(adaptLargeTrade({ symbol: "XRP", side: "sell", notional_usd: 540_000 }, tr), 25),
    mk(adaptSignal({ symbol: "DOGE", side: "buy", strength: 78 }, tr), 33),
    mk(adaptFundingRate({ symbol: "BNB", rate: 0.00021 }, tr), 41),
    mk(adaptLiquidation({ symbol: "LINK", side: "short", notional_usd: 310_000, price: 15.8 }, tr), 50),
    mk(adaptLargeTrade({ symbol: "AVAX", side: "buy", notional_usd: 760_000 }, tr), 60),
  ];
}

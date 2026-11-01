"use client";

import { useMemo } from "react";

import { useApi } from "@/lib/hooks/use-api";
import { normalizeSymbol } from "@/lib/market/route-symbol";
import type { Candle } from "@/components/chart/kline-chart";

// 统一 REST 网关 /chart/bars 契约（决策 A：非加密市场走 REST，加密走 WS 实时）
export interface UnifiedBar {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface BarsResponse {
  market: string;
  symbol: string;
  interval: string;
  bars: UnifiedBar[];
}

export const toCandles = (bars: UnifiedBar[]): Candle[] =>
  bars.map((b) => ({
    t: b.t,
    open: b.o,
    high: b.h,
    low: b.l,
    close: b.c,
    volume: b.v,
    up: b.c >= b.o,
  }));

/** 共享空数组常量：无数据时也返回稳定引用，避免下游 effect 每次重跑。 */
const NO_CANDLES: Candle[] = [];

// 非加密市场 K 线拉取。market === "crypto" 或 symbol 为空时不发请求（返回空 candles）。
// symbol 先归一化剥后缀：自选/最近里的脏数据（"300750.SZ"）会让后端 404/502，
// 后端 /chart/bars 只认裸符号。
export function useMultiMarketBars(market: string, symbol: string, interval: string) {
  const bare = normalizeSymbol(symbol);
  const path =
    bare && market !== "crypto"
      ? `/api/v1/chart/bars?market=${market}&symbol=${encodeURIComponent(bare)}&interval=${interval}`
      : null;
  const { data } = useApi<BarsResponse>(path);

  // 引用必须按"数据"而不是按"渲染次数"稳定：调用方 kf-page 每秒 tick 重渲染
  // （资金费倒计时），裸 `bars.map(...)` 会让 KlineChart 的 [candles] effect 每秒
  // 重跑 —— setData 重灌之外还带一次 fitContent()，于是用户的缩放/平移每秒被按回去。
  const bars = data?.bars;
  return {
    candles: useMemo(
      () => (bars && bars.length ? toCandles(bars) : NO_CANDLES),
      [bars]
    ),
  };
}

"use client";

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
  return { candles: toCandles(data?.bars ?? []) };
}

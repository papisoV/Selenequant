import { api } from "@/lib/api-client";
import { getCachedKlines, getLatestTimestamp, putCachedKlines } from "@/lib/kline-cache";
import type { Kline, KlineResponse, Ticker, OrderBook } from "@/lib/types";

// 拉取深度:端点默认 limit=200,只给最新 200 根(15m≈2天),图表在 200 根后断成"缺口"。
// 显式传端点上限 1500:15m≈15.6 天 / 4h≈250 天 / 1h≈62 天,并让 since 增量窗口必然落在 200 根旧值之外。
export const KLINE_FETCH_LIMIT = 1500;

export async function getKline(symbol: string, interval = "1h", exchange = "binance", marketType = "futures", endTime?: number): Promise<KlineResponse> {
  const end = endTime ? `&end_time=${endTime}` : "";
  return api<KlineResponse>(`/api/v1/markets/${symbol}/kline?interval=${interval}&exchange=${exchange}&market_type=${marketType}&limit=${KLINE_FETCH_LIMIT}${end}`);
}

// 带 IndexedDB 缓存的 getKline：
// 1. 先读缓存立即返回（cached 字段）
// 2. 用 latestTs 作 since 增量 fetch
// 3. fetch 返回后合并 + 写回缓存
// 4. 返回完整 Kline[]（缓存 + 增量）
// 如果后端断或 fetch 失败，返回纯缓存数据（可能为空数组）
//
// opts.endTime：向后翻页加载历史。传入时跳过缓存读、跳过 since 增量——
// 直接 REST 拉 endTime 之前的更早一批（后端原样透传 end_time），合并写回缓存，
// 让滚动加载的历史持久化到 IndexedDB。用于"滚动到最左加载更早 K 线"。
export async function getKlineWithCache(
  symbol: string,
  interval = "1h",
  exchange = "binance",
  marketType = "futures",
  opts?: { endTime?: number },
): Promise<{ klines: Kline[]; fromCache: boolean; source: string }> {
  // 向后翻页：跳过缓存读，直接 REST 拉历史（合并写回由调用方决定是否保留）
  if (opts?.endTime != null) {
    try {
      const resp = await api<KlineResponse>(
        `/api/v1/markets/${symbol}/kline?interval=${interval}&exchange=${exchange}&market_type=${marketType}&limit=${KLINE_FETCH_LIMIT}&end_time=${opts.endTime}`,
      );
      const fresh = resp.klines ?? [];
      if (fresh.length > 0) {
        // 合并进缓存（老 K 线在前），刷新后仍可滚动
        const cached = await getCachedKlines(exchange, marketType, symbol, interval);
        const merged = mergeKlines(cached, fresh);
        void putCachedKlines(exchange, marketType, symbol, interval, merged);
        return { klines: merged, fromCache: false, source: resp.source ?? "exchange" };
      }
    } catch {
      // fall through to cached
    }
    // 翻页失败/无新数据 → 返回缓存现状（可能不含更早历史）
    const cached = await getCachedKlines(exchange, marketType, symbol, interval);
    return { klines: cached, fromCache: cached.length > 0, source: "stale" };
  }

  const cached = await getCachedKlines(exchange, marketType, symbol, interval);
  const latestTs = cached.length > 0 ? cached[cached.length - 1].timestamp ?? cached[cached.length - 1].open_time ?? null : null;

  let fresh: Kline[] = [];
  let fetchFailed = false;
  let respSource = "exchange";
  try {
    const sinceParam = latestTs ? `&since=${latestTs}` : "";
    const resp = await api<KlineResponse>(
      `/api/v1/markets/${symbol}/kline?interval=${interval}&exchange=${exchange}&market_type=${marketType}&limit=${KLINE_FETCH_LIMIT}${sinceParam}`,
    );
    fresh = resp.klines ?? [];
    respSource = resp.source ?? "exchange";
  } catch {
    fetchFailed = true;
  }

  // fetch 失败 → 本地 IndexedDB 缓存兜底，本质也是 stale 数据
  if (fetchFailed) {
    return { klines: cached, fromCache: true, source: "stale" };
  }

  // 合并缓存 + 增量，写回 IndexedDB（fire-and-forget）
  if (fresh.length > 0) {
    const merged = mergeKlines(cached, fresh);
    void putCachedKlines(exchange, marketType, symbol, interval, merged);
    return { klines: merged, fromCache: cached.length > 0, source: respSource };
  }

  // fresh 为空但有缓存：后端可能走了 SQLite 兜底返回空（或真无增量），用缓存 + 后端 source
  return { klines: cached, fromCache: cached.length > 0, source: respSource };
}

function mergeKlines(cached: Kline[], fresh: Kline[]): Kline[] {
  const map = new Map<number, Kline>();
  const ts = (k: Kline) => k.timestamp ?? k.open_time ?? 0;
  for (const k of cached) map.set(ts(k), k);
  for (const k of fresh) map.set(ts(k), k);
  return Array.from(map.values()).sort((a, b) => ts(a) - ts(b));
}

export async function getTicker(symbol: string, exchange = "binance", marketType = "futures"): Promise<Ticker> {
  return api<Ticker>(`/api/v1/markets/${symbol}/ticker?exchange=${exchange}&market_type=${marketType}`);
}

export async function getOrderBook(symbol: string, exchange = "binance", marketType = "futures", depth = 20): Promise<OrderBook> {
  return api<OrderBook>(`/api/v1/markets/${symbol}/orderbook?exchange=${exchange}&market_type=${marketType}&depth=${depth}`);
}

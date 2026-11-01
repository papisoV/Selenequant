"use client";

// IndexedDB K 线缓存（参考旧代码 kline-cache.ts）
// 不依赖 idb 库，用原生 IndexedDB API
//
// Key schema: `{exchange}:{marketType}:{symbol}:{interval}`
// 每个 key 存 Kline[] 按 timestamp 升序
//
// 价值：
// - 刷新页面立即显示缓存 K 线（< 50ms）再 fetch 增量
// - since 增量 fetch 减少 10-100x payload
// - 离线可看历史

import type { Kline } from "@/lib/types/backend";

const DB_NAME = "selene-klines";
const DB_VERSION = 1;
const STORE_NAME = "klines";
// 缓存深度上限:5000 只够最新窗口,滚动加载更早历史(loadMore)后会被 slice 裁掉。
// 提升到 30000 让 IndexedDB 承载多页历史(1m≈21天/15m≈312天),刷新后仍可滚动。
const MAX_CANDLES = 30000;

function cacheKey(exchange: string, marketType: string, symbol: string, interval: string): string {
  return `${exchange}:${marketType}:${symbol}:${interval}`;
}

function klineTs(k: Kline): number {
  return k.timestamp ?? k.open_time ?? 0;
}

let dbPromise: Promise<IDBDatabase | null> | null = null;

function getDB(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === "undefined") return Promise.resolve(null);
  if (!dbPromise) {
    dbPromise = new Promise((resolve) => {
      try {
        const req = indexedDB.open(DB_NAME, DB_VERSION);
        req.onupgradeneeded = () => {
          const db = req.result;
          if (!db.objectStoreNames.contains(STORE_NAME)) {
            db.createObjectStore(STORE_NAME);
          }
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => resolve(null);
      } catch {
        resolve(null);
      }
    });
  }
  return dbPromise;
}

export async function getCachedKlines(
  exchange: string,
  marketType: string,
  symbol: string,
  interval: string,
): Promise<Kline[]> {
  try {
    const db = await getDB();
    if (!db) return [];
    const key = cacheKey(exchange, marketType, symbol, interval);
    return await new Promise((resolve) => {
      const tx = db.transaction(STORE_NAME, "readonly");
      const req = tx.objectStore(STORE_NAME).get(key);
      req.onsuccess = () => {
        const data = req.result;
        resolve(Array.isArray(data) ? (data as Kline[]) : []);
      };
      req.onerror = () => resolve([]);
    });
  } catch {
    return [];
  }
}

export async function putCachedKlines(
  exchange: string,
  marketType: string,
  symbol: string,
  interval: string,
  candles: Kline[],
): Promise<void> {
  try {
    const db = await getDB();
    if (!db) return;
    const key = cacheKey(exchange, marketType, symbol, interval);
    const existing = await getCachedKlines(exchange, marketType, symbol, interval);

    let merged: Kline[];
    if (existing.length > 0) {
      const map = new Map<number, Kline>();
      for (const k of existing) map.set(klineTs(k), k);
      for (const k of candles) map.set(klineTs(k), k);
      merged = Array.from(map.values()).sort((a, b) => klineTs(a) - klineTs(b));
    } else {
      merged = [...candles].sort((a, b) => klineTs(a) - klineTs(b));
    }

    if (merged.length > MAX_CANDLES) {
      merged = merged.slice(merged.length - MAX_CANDLES);
    }

    await new Promise<void>((resolve) => {
      const tx = db.transaction(STORE_NAME, "readwrite");
      tx.objectStore(STORE_NAME).put(merged, key);
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
    });
  } catch {
    // IndexedDB write failure — non-critical
  }
}

export async function getLatestTimestamp(
  exchange: string,
  marketType: string,
  symbol: string,
  interval: string,
): Promise<number | null> {
  const cached = await getCachedKlines(exchange, marketType, symbol, interval);
  if (cached.length === 0) return null;
  return klineTs(cached[cached.length - 1]);
}

// 清空指定 key 的缓存（用于发现大缺口后强制全量重拉，避免带洞缓存污染后续增量）
export async function clearCachedKlines(
  exchange: string,
  marketType: string,
  symbol: string,
  interval: string,
): Promise<void> {
  try {
    const db = await getDB();
    if (!db) return;
    const key = cacheKey(exchange, marketType, symbol, interval);
    await new Promise<void>((resolve) => {
      const tx = db.transaction(STORE_NAME, "readwrite");
      tx.objectStore(STORE_NAME).delete(key);
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
    });
  } catch {
    // ignore
  }
}

// 清空所有缓存（用于调试 / 重置）
export async function clearKlineCache(): Promise<void> {
  try {
    const db = await getDB();
    if (!db) return;
    await new Promise<void>((resolve) => {
      const tx = db.transaction(STORE_NAME, "readwrite");
      tx.objectStore(STORE_NAME).clear();
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
    });
  } catch {
    // ignore
  }
}

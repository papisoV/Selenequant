"use client";

import { useCallback, useState } from "react";

// 最近浏览符号 hook：纯 localStorage（无后端同步）。
// ⌘K 选中交易对时 recordRecent，空态搜索列表置顶展示。
// 与 use-symbol-favorites 同构（read/write + MAX 截断），但存 {symbol, market, name}。

const RECENTS_KEY = "selenespace-recent-symbols";
const MAX_RECENTS = 10;

export interface RecentSymbol {
  symbol: string;
  market: string;
  name?: string;
}

function readLocalStorageRecents(): RecentSymbol[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(RECENTS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (item): item is RecentSymbol =>
          !!item && typeof item.symbol === "string" && typeof item.market === "string",
      )
      .slice(0, MAX_RECENTS);
  } catch {
    return [];
  }
}

function writeLocalStorageRecents(recents: RecentSymbol[]): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(RECENTS_KEY, JSON.stringify(recents));
  } catch {
    // ignore quota / serialization errors
  }
}

export function useRecentSymbols() {
  const [recents, setRecents] = useState<RecentSymbol[]>(readLocalStorageRecents);

  const recordRecent = useCallback((item: RecentSymbol): void => {
    // 去重（同 symbol）后置顶，截断到 MAX_RECENTS；immutable
    setRecents((prev) => {
      const next = [item, ...prev.filter((r) => r.symbol !== item.symbol)].slice(0, MAX_RECENTS);
      writeLocalStorageRecents(next);
      return next;
    });
  }, []);

  const clearRecents = useCallback((): void => {
    setRecents(() => {
      writeLocalStorageRecents([]);
      return [];
    });
  }, []);

  return { recents, recordRecent, clearRecents };
}

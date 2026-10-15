"use client";

import { create } from "zustand";
import { useCallback, useEffect } from "react";
import { api, apiPost, apiPut } from "@/lib/api-client";
import { routeSymbol, normalizeSymbol } from "@/lib/market/route-symbol";

// 自选列表：zustand 共享 store + localStorage 乐观更新 + 后端 GET/PUT/POST /favorites
// 后端端点：
//   GET  /api/v1/favorites       → { symbols: [...] }（APIResponse 包装，api() 自动解包）
//   PUT  /api/v1/favorites       → 覆盖式同步 { symbols: [...] }
//   POST /api/v1/favorites/toggle → { symbol } → 返回最新 { symbols: [...] }
// 存储格式按市场分流：crypto 存完整对（"BTC/USDT"），非加密（us/ashare/taiwan/commodities）
// 存裸符号（"AAPL"/"600519"/"CU0"）；ambiguous 走 crypto 兜底。
// 历史裸符号（"BTC"）读取时按 crypto 视为 /USDT 并惰性迁移回写。
//
// 共享 store 动机：kf 星标与 sidebar 自选区必须实时同步（同一 favorites 状态源）。
// 旧实现（每组件一个 useState 实例）导致 kf 加星后 sidebar 不刷新——0923 验收实证。

const FAVORITES_KEY = "selenespace-symbol-favorites";
const MAX_FAVORITES = 30;

interface FavoritesResponse {
  symbols: string[];
}

// 符号 → 自选存储键：crypto（含 ambiguous 兜底）补 /USDT 存完整对；
// 非加密市场路由（AAPL→us、600519→ashare、CU0→commodities）存裸符号原样。
// 统一先经 normalizeSymbol 剥交易所/合约后缀（"300750.SZ"→"300750"、"BTC-PERP"→"BTC"），
// 否则带后缀的脏数据会被存进后端并被路由到 us 取不到数（1013 A股"不能显示"根因）。
export function toPair(sym: string): string {
  const s = sym.trim();
  if (!s) return s;
  const bare = normalizeSymbol(s);
  const m = routeSymbol(s);
  if (m === "us" || m === "ashare" || m === "taiwan" || m === "commodities") return bare;
  // crypto：显式斜杠对保留原样（如 ETH/BTC 不能被改成 ETH/USDT）；其余补 /USDT
  if (s.includes("/")) return s.toUpperCase();
  return `${bare}/USDT`;
}

function readLocalStorageFavorites(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(FAVORITES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item): item is string => typeof item === "string")
      .map(toPair)
      .slice(0, MAX_FAVORITES);
  } catch {
    return [];
  }
}

function writeLocalStorageFavorites(symbols: string[]): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(symbols));
  } catch {
    // ignore quota / serialization errors
  }
}

interface FavoritesState {
  favorites: string[];
  isLoading: boolean;
  setFavorites: (symbols: string[]) => void;
  setLoading: (v: boolean) => void;
}

const useFavoritesStore = create<FavoritesState>((set) => ({
  favorites: [],
  isLoading: true,
  setFavorites: (symbols) => set({ favorites: symbols }),
  setLoading: (v) => set({ isLoading: v }),
}));

// 模块级一次性同步锁：React 18 StrictMode / 多组件挂载只跑一次后端同步
let initStarted = false;

// 测试专用：重置共享 store + init 锁（生产代码勿调）
export function resetFavoritesStoreForTest(): void {
  initStarted = false;
  useFavoritesStore.setState({ favorites: [], isLoading: true });
}

export function useSymbolFavorites() {
  const favorites = useFavoritesStore((s) => s.favorites);
  const isLoading = useFavoritesStore((s) => s.isLoading);

  // 初始化：GET /favorites 同步后端 → store + localStorage；发现裸符号 → PUT 回写迁移
  useEffect(() => {
    if (initStarted) return;
    initStarted = true;
    let cancelled = false;
    (async () => {
      try {
        const res = await api<FavoritesResponse>("/api/v1/favorites");
        if (!cancelled && res?.symbols) {
          const syms = res.symbols.map(toPair).slice(0, MAX_FAVORITES);
          useFavoritesStore.getState().setFavorites(syms);
          writeLocalStorageFavorites(syms);
          if (syms.some((s, i) => s !== res.symbols[i])) {
            // 后端存的是裸符号 → 覆盖式回写完整对（fire-and-forget）
            try {
              await apiPut<FavoritesResponse>("/api/v1/favorites", { symbols: syms });
            } catch {
              // 迁移失败不影响使用，next init 重试
            }
          }
        }
      } catch {
        // Backend unavailable, keep localStorage values
      } finally {
        if (!cancelled) useFavoritesStore.getState().setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // toggle 的乐观更新 + PUT 兜底不能依赖 setState updater 的执行时机——用 store 当前值
  // 确定性计算 next（setState updater 惰性执行会拿到空数组，兜底同步把后端清空——测试实证）
  const toggleFavorite = useCallback(async (symbol: string): Promise<void> => {
    const pair = toPair(symbol);
    const current = useFavoritesStore.getState().favorites;
    const nextSymbols = current.includes(pair)
      ? current.filter((s) => s !== pair)
      : [pair, ...current].slice(0, MAX_FAVORITES);
    useFavoritesStore.getState().setFavorites(nextSymbols);
    writeLocalStorageFavorites(nextSymbols);

    try {
      const res = await apiPost<FavoritesResponse>("/api/v1/favorites/toggle", { symbol: pair });
      if (res?.symbols) {
        const syms = res.symbols.map(toPair).slice(0, MAX_FAVORITES);
        useFavoritesStore.getState().setFavorites(syms);
        writeLocalStorageFavorites(syms);
      }
    } catch {
      // toggle 失败：回退用 PUT 覆盖式同步，确保后端与本地一致
      try {
        const res = await apiPut<FavoritesResponse>("/api/v1/favorites", {
          symbols: nextSymbols,
        });
        if (res?.symbols) {
          const syms = res.symbols.map(toPair).slice(0, MAX_FAVORITES);
          useFavoritesStore.getState().setFavorites(syms);
          writeLocalStorageFavorites(syms);
        }
      } catch {
        // Already applied optimistically; backend will catch up
      }
    }
  }, []);

  const isFavorite = useCallback(
    (symbol: string): boolean => favorites.includes(toPair(symbol)),
    [favorites],
  );

  return { favorites, toggleFavorite, isFavorite, isLoading };
}

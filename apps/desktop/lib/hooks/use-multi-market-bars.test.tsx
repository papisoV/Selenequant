import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

/**
 * 换标的/换周期 K 线残留治本（批3.4）。
 *
 * `useMultiMarketBars` 返回的 candles 是 `bars.map(...)` 的产物 —— 每次 render
 * 都是全新数组。父组件 kf-page 有 1s tick（资金费倒计时），于是每秒都喂给
 * KlineChart 一个新引用，下游 `useEffect(..., [candles])` 跟着每秒重跑：
 * `setData` 三条序列每秒重灌一遍，`fitContent()` 每秒把视口按回去。
 * 症状就是"缩放/平移一放手就被弹回"。
 *
 * 这里锁住引用稳定性：数据没变，candles 就必须是同一个对象。
 */

const apiState: { data: unknown } = { data: null };

vi.mock("@/lib/hooks/use-api", () => ({
  useApi: () => apiState,
}));

vi.mock("@/lib/market/route-symbol", () => ({
  normalizeSymbol: (s: string) => s,
}));

import { useMultiMarketBars, toCandles, type UnifiedBar } from "./use-multi-market-bars";

const BARS: UnifiedBar[] = Array.from({ length: 5 }, (_, i) => ({
  t: 1792000000 + i * 3600,
  o: 100 + i,
  h: 108 + i,
  l: 95 + i,
  c: 104 + i,
  v: 1000 + i,
}));

beforeEach(() => {
  apiState.data = null;
});

describe("toCandles 字段映射", () => {
  it("UnifiedBar → Candle：t/o/h/l/c/v 落到对应字段，up 由 c>=o 推出", () => {
    const out = toCandles([{ t: 1, o: 10, h: 12, l: 8, c: 11, v: 5 }]);
    expect(out).toEqual([
      { t: 1, open: 10, high: 12, low: 8, close: 11, volume: 5, up: true },
    ]);
  });

  it("平盘（c==o）算阳（up=true），与图上 tooltip 口径一致", () => {
    expect(toCandles([{ t: 1, o: 10, h: 10, l: 10, c: 10, v: 0 }])[0].up).toBe(true);
  });
});

describe("useMultiMarketBars 引用稳定性", () => {
  it("同一份数据重渲染 → candles 引用不变（否则父组件每秒重渲染都触发下游 effect）", () => {
    apiState.data = { market: "us", symbol: "AAPL", interval: "1d", bars: BARS };

    const { result, rerender } = renderHook(() => useMultiMarketBars("us", "AAPL", "1d"));
    const first = result.current.candles;

    // 模拟 kf-page 的 1s tick：同样的数据，但 hook 再跑一遍
    apiState.data = { market: "us", symbol: "AAPL", interval: "1d", bars: BARS };
    rerender();

    expect(result.current.candles).toBe(first);
  });

  it("无数据时返回的也是稳定引用，不是每次新建的空数组", () => {
    apiState.data = null;

    const { result, rerender } = renderHook(() => useMultiMarketBars("us", "AAPL", "1d"));
    const first = result.current.candles;

    rerender();

    expect(result.current.candles).toBe(first);
    expect(first).toEqual([]);
  });

  it("数据真的换了 → 引用必须变（别把 memo 做成永不更新）", () => {
    apiState.data = { market: "us", symbol: "AAPL", interval: "1d", bars: BARS };

    const { result, rerender } = renderHook(() => useMultiMarketBars("us", "AAPL", "1d"));
    const first = result.current.candles;

    const grown: UnifiedBar[] = [
      ...BARS,
      { t: 1792000000 + 5 * 3600, o: 105, h: 113, l: 100, c: 110, v: 1005 },
    ];
    apiState.data = { market: "us", symbol: "AAPL", interval: "1d", bars: grown };
    rerender();

    expect(result.current.candles).not.toBe(first);
    expect(result.current.candles).toHaveLength(6);
  });
});

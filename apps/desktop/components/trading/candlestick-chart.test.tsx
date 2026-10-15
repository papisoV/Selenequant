import { describe, it, expect } from "vitest";
import { renderHook } from "@testing-library/react";
import { candleToOHLC, useLatestRef } from "./candlestick-chart";

// 行为锁: candlestick-chart 抽出的 helper 契约。cleanroom 重写必须保持; 撤实现 → 必红。

describe("candleToOHLC", () => {
  it("timestamp 存在 → time 用它", () => {
    expect(candleToOHLC({ timestamp: 123, open: 1, high: 2, low: 0.5, close: 1.5 })).toEqual({
      time: 123,
      open: 1,
      high: 2,
      low: 0.5,
      close: 1.5,
    });
  });
  it("timestamp 缺失 → time=0", () => {
    expect(candleToOHLC({ open: 1, high: 2, low: 0.5, close: 1.5 }).time).toBe(0);
  });
});

describe("useLatestRef", () => {
  it("渲染后 ref.current 同步为值", () => {
    const ref = { current: 0 };
    renderHook(({ v }) => useLatestRef(ref, v), { initialProps: { v: 7 } });
    expect(ref.current).toBe(7);
  });
});

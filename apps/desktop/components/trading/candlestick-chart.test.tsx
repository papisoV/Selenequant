import { describe, it, expect } from "vitest";
import { renderHook } from "@testing-library/react";
import { candleToOHLC, classifyDataChange, useLatestRef } from "./candlestick-chart";

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

// 回归锁:全景取回全区间数据时必须 fit,而不是把视口钉死在末 120 根。
describe("classifyDataChange(视口处置)", () => {
  const T0 = 1_700_000_000_000;
  const DAY = 86_400_000;

  it("首帧(prevLen=0)→ fit(收全量)", () => {
    expect(classifyDataChange(0, 0, 0, 500, T0, T0 + 500 * DAY))
      .toEqual({ kind: "fit" });
  });

  it("末根追加(根数 +1,首根不变)→ incremental", () => {
    expect(classifyDataChange(100, T0, T0 + 99 * DAY, 101, T0, T0 + 100 * DAY))
      .toEqual({ kind: "incremental" });
  });

  it("同 bar 覆盖(根数不变)→ incremental", () => {
    expect(classifyDataChange(100, T0, T0 + 99 * DAY, 100, T0, T0 + 99 * DAY))
      .toEqual({ kind: "incremental" });
  });

  it("向前追加历史(根数变多/首根前移/末根不动)→ preserve 并按增量平移", () => {
    expect(classifyDataChange(50, T0, T0 + 49 * DAY, 80, T0 - 30 * DAY, T0 + 49 * DAY))
      .toEqual({ kind: "preserve", shift: 30 });
  });

  it("换窗口(首末全变,根数不同)→ fit —— 全景的关键路径", () => {
    // 聚焦时的窄窗口 → 全景时的整个回测区间
    expect(classifyDataChange(120, T0, T0 + 119 * DAY, 365, T0 - 200 * DAY, T0 + 165 * DAY))
      .toEqual({ kind: "fit" });
  });

  it("换周期(同样根数但首末全变)→ fit", () => {
    expect(classifyDataChange(365, T0, T0 + 364 * DAY, 365, T0 - 10 * DAY, T0 + 354 * DAY))
      .toEqual({ kind: "fit" });
  });

  it("数据被清空(nextLen=0)→ fit(不遗留旧视口)", () => {
    expect(classifyDataChange(100, T0, T0 + 99 * DAY, 0, 0, 0)).toEqual({ kind: "fit" });
  });
});

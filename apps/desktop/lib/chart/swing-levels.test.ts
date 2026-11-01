import { describe, it, expect } from "vitest";
import {
  DEFAULT_MAX_PER_SIDE,
  findPivotHighs,
  findPivotLows,
  groupLevels,
  computeSupportResistance,
} from "@/lib/chart/swing-levels";
import type { KlineBar } from "@/lib/api/market-analysis";

const bar = (high: number, low: number): KlineBar => ({
  timestamp: 0,
  open: low,
  high,
  low,
  close: (high + low) / 2,
  volume: 1,
});

describe("findPivotHighs / findPivotLows", () => {
  it("单边行情：严格单调递增只有全局最高点是 pivot", () => {
    expect(findPivotHighs([1, 2, 3, 4, 5])).toEqual([4]);
  });

  it("锯齿行情：k=1 每个局部最高点都是 pivot", () => {
    expect(findPivotHighs([5, 3, 7, 3, 5], 1)).toEqual([0, 2, 4]);
    expect(findPivotLows([3, 5, 1, 5, 3], 1)).toEqual([0, 2, 4]);
  });

  it("相等相邻值不算严格 pivot（避免水平线误判）", () => {
    expect(findPivotHighs([4, 4, 4])).toEqual([]);
    expect(findPivotLows([4, 4, 4])).toEqual([]);
  });

  it("k=2 边界：窗口右端可超出数组", () => {
    // 末位最高点在截断窗口内仍是严格最大值
    expect(findPivotHighs([2, 3, 1, 2, 6], 2)).toContain(4);
  });
});

describe("groupLevels", () => {
  it("相邻价差在容差内合并为加权均值簇", () => {
    const levels = groupLevels([100.0, 100.1, 100.2], 0.003);
    expect(levels).toHaveLength(1);
    expect(levels[0].touches).toBe(3);
    expect(levels[0].price).toBeCloseTo(100.1, 6);
  });

  it("价差超容差则分开成多个簇", () => {
    const levels = groupLevels([100.0, 101.0, 102.0], 0.003);
    expect(levels.map((l) => l.price)).toEqual([100.0, 101.0, 102.0]);
    expect(levels.every((l) => l.touches === 1)).toBe(true);
  });

  it("空输入返回空簇", () => {
    expect(groupLevels([])).toEqual([]);
  });
});

describe("computeSupportResistance", () => {
  it("识别明确摆动高低点：支撑在下、阻力在上", () => {
    const highs = [103, 100, 102, 100, 103, 100, 102, 100, 103];
    const lows = [98, 97, 96, 97, 98, 97, 96, 97, 98];
    const bars = highs.map((h, i) => bar(h, lows[i]));

    const { supports, resistances } = computeSupportResistance(bars);
    expect(supports).toEqual([96]);
    expect(resistances).toEqual([103]);
  });

  it("maxPerSide 上限：多个独立阻力位只保留最密的前 N", () => {
    const highs = [110, 100, 108, 101, 106, 102, 104, 103];
    const lows = highs.map((h) => h - 2);
    const bars = highs.map((h, i) => bar(h, lows[i]));

    const { resistances } = computeSupportResistance(bars, { k: 1 });
    expect(resistances).toHaveLength(DEFAULT_MAX_PER_SIDE);
    expect(resistances).toEqual([110, 108, 106]);
  });

  it("输出始终落在可见价格区间内", () => {
    const bars = [100, 90, 110, 95, 105, 92, 108].map((h) => bar(h, h - 3));
    const { supports, resistances } = computeSupportResistance(bars);
    const minLow = Math.min(...bars.map((b) => b.low));
    const maxHigh = Math.max(...bars.map((b) => b.high));
    for (const s of supports) expect(s).toBeGreaterThanOrEqual(minLow);
    for (const r of resistances) expect(r).toBeLessThanOrEqual(maxHigh);
  });

  it("空 K 线安全降级为空", () => {
    expect(computeSupportResistance([])).toEqual({ supports: [], resistances: [] });
  });
});

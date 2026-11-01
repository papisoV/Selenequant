import { describe, expect, it } from "vitest";
import { cursorMarkLine, nearestSorted, withinRange } from "./time-cursor";

describe("nearestSorted", () => {
  it("精确命中 → 原值", () => {
    expect(nearestSorted([10, 20, 30], 20)).toBe(20);
  });

  it("落在两点之间 → 取更近的一个", () => {
    expect(nearestSorted([10, 20, 30], 21)).toBe(20);
    expect(nearestSorted([10, 20, 30], 29)).toBe(30);
  });

  it("等距 → 取左边(稳定,不随实现漂移)", () => {
    expect(nearestSorted([10, 20], 15)).toBe(10);
  });

  it("超出两端 → 夹到端点(不返回区间外的时间)", () => {
    expect(nearestSorted([10, 20, 30], 5)).toBe(10);
    expect(nearestSorted([10, 20, 30], 99)).toBe(30);
  });

  it("空数组 → null", () => {
    expect(nearestSorted([], 42)).toBeNull();
  });

  it("单元素 → 恒返回它", () => {
    expect(nearestSorted([7], -100)).toBe(7);
    expect(nearestSorted([7], 100)).toBe(7);
  });

  it("大数组仍是最近邻(二分不退化)", () => {
    const arr = Array.from({ length: 5000 }, (_, i) => i * 1000);
    expect(nearestSorted(arr, 2_500_400)).toBe(2_500_000);
    expect(nearestSorted(arr, 2_500_600)).toBe(2_501_000);
    // 顶端:数组最大 4_999_000,4_999_600 越界 → 夹到端点
    expect(nearestSorted(arr, 4_999_600)).toBe(4_999_000);
  });
});

describe("withinRange", () => {
  it("区间内 → true,含端点", () => {
    expect(withinRange([10, 20, 30], 20)).toBe(true);
    expect(withinRange([10, 20, 30], 10)).toBe(true);
    expect(withinRange([10, 20, 30], 30)).toBe(true);
  });

  it("区间外 → false(不吸附到边缘谎报位置)", () => {
    expect(withinRange([10, 20, 30], 9)).toBe(false);
    expect(withinRange([10, 20, 30], 31)).toBe(false);
  });

  it("空数组 → false", () => {
    expect(withinRange([], 10)).toBe(false);
  });
});

describe("cursorMarkLine", () => {
  it("有游标 → data 带 xAxis,三段网格同一条", () => {
    const line = cursorMarkLine(1_700_000_000_000, "#8b8f94");
    expect(line.data).toEqual([{ xAxis: 1_700_000_000_000 }]);
    expect(line.silent).toBe(true);
    expect(line.lineStyle.color).toBe("#8b8f94");
  });

  it("无游标 → data 空数组(不画线,而非画在 0)", () => {
    expect(cursorMarkLine(null, "#8b8f94").data).toEqual([]);
  });
});

import { describe, expect, it } from "vitest";

import {
  barsBetween,
  logicalIndexForTime,
  timeForLogicalIndex,
} from "@/lib/chart/drawing-projection";

const H = 3600; // 1h 一秒步长
// 4 根连续 bar,然后一个 3 格的缺口,再 2 根
const SLOTS = [0, H, 2 * H, 3 * H, 7 * H, 8 * H];

describe("logicalIndexForTime", () => {
  it("命中槽位时返回整数索引", () => {
    expect(logicalIndexForTime(0, SLOTS, H)).toBe(0);
    expect(logicalIndexForTime(2 * H, SLOTS, H)).toBe(2);
    expect(logicalIndexForTime(8 * H, SLOTS, H)).toBe(5);
  });

  it("槽位之间线性插值到分数索引(不外推)", () => {
    expect(logicalIndexForTime(H / 2, SLOTS, H)).toBeCloseTo(0.5, 9);
    expect(logicalIndexForTime(2.5 * H, SLOTS, H)).toBeCloseTo(2.5, 9);
  });

  it("跨缺口时钳在两端槽位之间,不按真实时间拉伸", () => {
    // 3h 与 7h 之间是缺口;4h、5h 在真实时间上偏后,但逻辑上仍在 3 与 4 之间
    const a = logicalIndexForTime(4 * H, SLOTS, H);
    const b = logicalIndexForTime(5 * H, SLOTS, H);
    for (const li of [a, b]) {
      expect(li).toBeGreaterThanOrEqual(3);
      expect(li).toBeLessThanOrEqual(4);
    }
    // 单调递增,但被压缩在 1 格内
    expect(b).toBeGreaterThan(a);
    expect(b - a).toBeCloseTo(0.25, 9); // 真实 1h / 缺口 4h = 0.25 格
    // 反证不是按真实步长:若按 stepSec 算,5h 应是索引 5,差 2
    expect(5 - b).toBeGreaterThan(1);
  });

  it("缺口之后的第一根槽位仍是 +1,不是 +4", () => {
    const before = logicalIndexForTime(3 * H, SLOTS, H);
    const after = logicalIndexForTime(7 * H, SLOTS, H);
    expect(after - before).toBe(1);
  });

  it("首槽之前按固定步长左外推(负索引)", () => {
    expect(logicalIndexForTime(-2 * H, SLOTS, H)).toBeCloseTo(-2, 9);
    expect(logicalIndexForTime(-H / 2, SLOTS, H)).toBeCloseTo(-0.5, 9);
  });

  it("末槽之后按固定步长右外推", () => {
    expect(logicalIndexForTime(9 * H, SLOTS, H)).toBeCloseTo(6, 9);
    expect(logicalIndexForTime(8 * H + H / 4, SLOTS, H)).toBeCloseTo(5.25, 9);
  });

  it("空槽位返回 0,不抛", () => {
    expect(logicalIndexForTime(123, [], H)).toBe(0);
  });

  it("步长非正时抛错(调用方保证传对)", () => {
    expect(() => logicalIndexForTime(0, SLOTS, 0)).toThrow(/stepSec/);
    expect(() => logicalIndexForTime(0, SLOTS, -1)).toThrow(/stepSec/);
  });

  it("单槽位也能外推两侧", () => {
    expect(logicalIndexForTime(5 * H, [5 * H], H)).toBe(0);
    expect(logicalIndexForTime(7 * H, [5 * H], H)).toBeCloseTo(2, 9);
    expect(logicalIndexForTime(4 * H, [5 * H], H)).toBeCloseTo(-1, 9);
  });
});

describe("timeForLogicalIndex", () => {
  it("是 logicalIndexForTime 的逆运算(槽位内)", () => {
    for (const t of [0, H / 3, 2.5 * H, 8 * H, 9.5 * H, -H / 4]) {
      const li = logicalIndexForTime(t, SLOTS, H);
      expect(timeForLogicalIndex(li, SLOTS, H)).toBeCloseTo(t, 6);
    }
  });

  it("缺口内的往返自洽 —— 缺口被压成一格,时间随之等比压缩", () => {
    const t = 5 * H; // 缺口中间
    const li = logicalIndexForTime(t, SLOTS, H);
    expect(li).toBeCloseTo(3.5, 9); // 3h 与 7h 之间正好一半
    expect(timeForLogicalIndex(li, SLOTS, H)).toBeCloseTo(t, 6);
    // 但真实时间跨度 4h 只占了 1 个槽位 —— 这就是"压成一格"
    const before = logicalIndexForTime(3 * H, SLOTS, H);
    const after = logicalIndexForTime(7 * H, SLOTS, H);
    expect(after - before).toBe(1);
  });

  it("整数索引取槽位原值", () => {
    expect(timeForLogicalIndex(0, SLOTS, H)).toBe(0);
    expect(timeForLogicalIndex(4, SLOTS, H)).toBe(7 * H);
    expect(timeForLogicalIndex(5, SLOTS, H)).toBe(8 * H);
  });

  it("两侧外推", () => {
    expect(timeForLogicalIndex(-2, SLOTS, H)).toBe(-2 * H);
    expect(timeForLogicalIndex(6, SLOTS, H)).toBe(9 * H);
  });

  it("空槽位返回 0", () => {
    expect(timeForLogicalIndex(3, [], H)).toBe(0);
  });
});

describe("barsBetween", () => {
  it("跨缺口仍按槽位计数", () => {
    expect(barsBetween(0, 5)).toBe(5);
    expect(barsBetween(3, 4)).toBe(1); // 跨一次缺口,逻辑上只差一根
  });
});

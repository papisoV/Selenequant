import { describe, it, expect } from "vitest";
import { suggestGridRange, buildGridLevels, DEFAULT_GRID_LEVELS } from "./grid-suggest";

describe("grid-suggest AI 网格区间启发式", () => {
  it("suggestGridRange — 以当前价为中轴,±波动率/2 为界", () => {
    const r = suggestGridRange({ currentPrice: 100, volatilityPct: 0.05, levelCount: 4 });
    expect(r.upper).toBeCloseTo(102.5, 2);
    expect(r.lower).toBeCloseTo(97.5, 2);
    expect(r.levelCount).toBe(4);
    expect(r.stepPercent).toBeCloseTo(1.25, 2);
  });

  it("ATR 优先于波动率", () => {
    const r = suggestGridRange({ currentPrice: 100, atr: 10, levelCount: 4 });
    // half = 10/100/2 = 0.05 → upper 105, lower 95
    expect(r.upper).toBeCloseTo(105, 2);
    expect(r.lower).toBeCloseTo(95, 2);
  });

  it("非法价格 → 全 0 但保留 levelCount", () => {
    const r = suggestGridRange({ currentPrice: 0, levelCount: 6 });
    expect(r.upper).toBe(0);
    expect(r.lower).toBe(0);
    expect(r.levelCount).toBe(6);
  });

  it("默认 levelCount 与下限保护", () => {
    const r = suggestGridRange({ currentPrice: 100 });
    expect(r.levelCount).toBe(DEFAULT_GRID_LEVELS);
    const r2 = suggestGridRange({ currentPrice: 100, levelCount: 1 });
    expect(r2.levelCount).toBe(2);
  });

  it("buildGridLevels — 等分档位(不越界)", () => {
    const r = suggestGridRange({ currentPrice: 100, volatilityPct: 0.05, levelCount: 4 });
    const levels = buildGridLevels(r);
    expect(levels).toHaveLength(4);
    expect(levels[0]).toBeCloseTo(r.lower, 2);
    expect(levels[3]).toBeCloseTo(r.upper, 2);
    for (let i = 1; i < levels.length; i++) {
      expect(levels[i]).toBeGreaterThan(levels[i - 1]);
    }
  });
});

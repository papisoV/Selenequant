import { describe, it, expect } from "vitest";
import {
  formatChartValue,
  computeMonthlyReturns,
  generateBuyHoldCurve,
  heatBackground,
  bucketPnls,
  generateXAxisLabels,
} from "./shared-charts";

// 行为锁: shared-charts 纯函数契约。重写必须保持; 撤掉实现改动 → 必红。

describe("formatChartValue", () => {
  it("非有限值 → 破折号", () => {
    expect(formatChartValue(NaN)).toBe("—");
    expect(formatChartValue(Infinity)).toBe("—");
  });
  it("negative=true → 固定 3 位", () => {
    expect(formatChartValue(1.23456, true)).toBe("1.235");
    expect(formatChartValue(0, true)).toBe("0.000");
  });
  it("按量级选精度: <100→2, <1000→1, >=1000→0", () => {
    expect(formatChartValue(12.345)).toBe("12.35");
    expect(formatChartValue(123.456)).toBe("123.5");
    expect(formatChartValue(1234.56)).toBe("1235");
  });
  it("负数按绝对值选量级", () => {
    expect(formatChartValue(-1234.56)).toBe("-1235");
    expect(formatChartValue(-12.345)).toBe("-12.35");
  });
});

describe("generateBuyHoldCurve", () => {
  it("<2 点 → 空", () => {
    expect(generateBuyHoldCurve([])).toEqual([]);
    expect(generateBuyHoldCurve([5])).toEqual([]);
  });
  it("线性插值: 首=起值, 末=终值, 中点=均", () => {
    const out = generateBuyHoldCurve([100, 0, 200]);
    expect(out[0]).toBe(100);
    expect(out[2]).toBe(200);
    expect(out[1]).toBe(150); // 100*(1+(2-1)*0.5)=150
  });
  it("等比涨: 首尾保持", () => {
    const out = generateBuyHoldCurve([100, 150]);
    expect(out).toEqual([100, 150]);
  });
});

describe("computeMonthlyReturns", () => {
  it("<2 点 → 空", () => expect(computeMonthlyReturns([5])).toEqual([]));
  it("无日期 → 单项 Total = 全期收益", () => {
    expect(computeMonthlyReturns([100, 110])).toEqual([
      { label: "Total", value: expect.closeTo(0.1, 6) },
    ]);
  });
  it("非法日期 → Total", () => {
    const out = computeMonthlyReturns([100, 120], "not-a-date", "also-bad");
    expect(out).toHaveLength(1);
    expect(out[0].label).toBe("Total");
    expect(out[0].value).toBeCloseTo(0.2, 6);
  });
  it("起止相等(总时长为0) → Total", () => {
    const out = computeMonthlyReturns([100, 130], "2024-01-01", "2024-01-01");
    expect(out).toHaveLength(1);
    expect(out[0].label).toBe("Total");
  });
  it("有效跨月日期 → 按月分桶(有 label)", () => {
    const out = computeMonthlyReturns([100, 110, 120, 130], "2024-01-01", "2024-03-01");
    expect(out.length).toBeGreaterThan(0);
    expect(out[0]).toHaveProperty("label");
    expect(out[0]).toHaveProperty("value");
  });
});

describe("heatBackground", () => {
  it("正值用 pos token, 负值用 neg", () => {
    expect(heatBackground(0.5)).toContain("var(--color-pos)");
    expect(heatBackground(-0.5)).toContain("var(--color-neg)");
    expect(heatBackground(0)).toContain("var(--color-pos)");
  });
  it("强度封顶 100%", () => {
    expect(heatBackground(1)).toBe("color-mix(in srgb, var(--color-pos) 58%, transparent)");
  });
});

describe("bucketPnls", () => {
  it("桶数在 4..8 之间", () => {
    expect(bucketPnls([1, 2, 3]).length).toBe(4);
    expect(bucketPnls(Array.from({ length: 20 }, (_, i) => i)).length).toBe(8);
  });
  it("计数总和 = 输入数(末桶含右端点)", () => {
    const pnls = [-3, -1, 0, 1, 2, 5];
    const total = bucketPnls(pnls).reduce((a, b) => a + b.count, 0);
    expect(total).toBe(pnls.length);
  });
  it("桶 start/end 覆盖 min..max", () => {
    const b = bucketPnls([-5, 0, 5]);
    expect(b[0].start).toBe(-5);
    expect(b[b.length - 1].end).toBe(5);
  });
});

describe("generateXAxisLabels", () => {
  it("无时间戳 → 百分比刻度(5 个)", () => {
    const out = generateXAxisLabels(undefined, 300, 5);
    expect(out).toHaveLength(5);
    expect(out[0]).toEqual({ x: 5, label: "0%" });
    expect(out[4].label).toBe("100%");
  });
  it("时间戳 <2 → 百分比刻度", () => {
    expect(generateXAxisLabels([1], 300, 5)).toHaveLength(5);
  });
  it("有时间戳 → 最多 5 个, x 单调递增", () => {
    const ts = Array.from({ length: 20 }, (_, i) => 1700000000000 + i * 86400000);
    const out = generateXAxisLabels(ts, 300, 5);
    expect(out.length).toBeLessThanOrEqual(5);
    for (let i = 1; i < out.length; i++) expect(out[i].x).toBeGreaterThan(out[i - 1].x);
    expect(typeof out[0].label).toBe("string");
  });
});

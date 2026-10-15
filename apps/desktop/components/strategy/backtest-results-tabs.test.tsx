import { describe, it, expect } from "vitest";
import {
  calculateYearlyReturns,
  pickCurve,
  monthlyChartRows,
  returnColorClass,
  clampChartScale,
  riskReturnMetricRows,
  correlationRows,
} from "./backtest-results-tabs";

// 行为锁: backtest-results-tabs 的年度/月度收益计算契约。重写必须保持; 撤实现 → 必红。

describe("calculateYearlyReturns", () => {
  it("曲线 <2 点 → 空", () => {
    expect(calculateYearlyReturns([])).toEqual([]);
    expect(calculateYearlyReturns([100])).toEqual([]);
  });
  it("无起止日期 → 单项全期收益(month=1)", () => {
    const out = calculateYearlyReturns([100, 110]);
    expect(out).toHaveLength(1);
    expect(out[0].month).toBe(1);
    expect(out[0].return).toBeCloseTo(0.1, 6);
  });
  it("非法日期 → 单项全期收益", () => {
    const out = calculateYearlyReturns([100, 120], "bad", "worse");
    expect(out).toHaveLength(1);
    expect(out[0].return).toBeCloseTo(0.2, 6);
  });
  it("首值为 0 → 收益记 0(不除零)", () => {
    const out = calculateYearlyReturns([0, 100]);
    expect(out[0].return).toBe(0);
  });
  it("有效跨月日期 → 按月分桶, year=起始年, month 从起始月递增", () => {
    const curve = [100, 105, 110, 115, 120, 125];
    const out = calculateYearlyReturns(curve, "2024-01-01", "2024-06-01");
    expect(out.length).toBeGreaterThan(0);
    expect(out.every((r) => r.year === 2024)).toBe(true);
    // 起始月 1 月 → 首个 month=2 (getMonth()=0, +0+1)
    expect(out[0].month).toBe(2);
    // 逐月递增
    for (let i = 1; i < out.length; i++) {
      expect(out[i].month).toBe(out[i - 1].month + 1);
    }
  });
  it("收益为正值(单调上涨)", () => {
    const curve = [100, 105, 110, 115, 120, 125];
    const out = calculateYearlyReturns(curve, "2024-01-01", "2024-06-01");
    expect(out.every((r) => r.return > 0)).toBe(true);
  });
  it("最多 12 个月", () => {
    const curve = Array.from({ length: 24 }, (_, i) => 100 + i);
    const out = calculateYearlyReturns(curve, "2024-01-01", "2026-01-01");
    expect(out.length).toBeLessThanOrEqual(12);
  });
});

describe("pickCurve", () => {
  it("顶层优先", () => expect(pickCurve([1, 2], [9])).toEqual([1, 2]));
  it("顶层空 → 嵌套", () => expect(pickCurve([], [9])).toEqual([9]));
  it("都空 → []", () => {
    expect(pickCurve(undefined, undefined)).toEqual([]);
    expect(pickCurve(null, [])).toEqual([]);
  });
});

describe("monthlyChartRows", () => {
  it("展开成 12 列, 缺失为 0, 百分比×100", () => {
    const rows = monthlyChartRows([{ year: 2024, month: 1, return: 0.05 }]);
    expect(rows).toHaveLength(1);
    expect(rows[0].year).toBe(2024);
    expect(rows[0].month_1).toBeCloseTo(5, 6);
    expect(rows[0].month_2).toBe(0);
    expect(rows[0].month_12).toBe(0);
  });
  it("多年按年升序", () => {
    const rows = monthlyChartRows([
      { year: 2024, month: 1, return: 0 },
      { year: 2023, month: 1, return: 0 },
    ]);
    expect(rows.map((r) => r.year)).toEqual([2023, 2024]);
  });
});

describe("returnColorClass", () => {
  it("按分档", () => {
    expect(returnColorClass(6)).toBe("bg-emerald-500 text-white");
    expect(returnColorClass(1)).toBe("bg-emerald-200 text-emerald-900");
    expect(returnColorClass(-1)).toBe("bg-red-200 text-red-900");
    expect(returnColorClass(-6)).toBe("bg-red-500 text-white");
  });
});

describe("clampChartScale", () => {
  it("夹到 [0.5, 2]", () => {
    expect(clampChartScale(3)).toBe(2);
    expect(clampChartScale(0)).toBe(0.5);
    expect(clampChartScale(1.25)).toBe(1.25);
  });
});

describe("riskReturnMetricRows", () => {
  it("无 metrics → []", () => expect(riskReturnMetricRows(null, {})).toEqual([]));
  it("百分比字段乘 100 且带 % 后缀", () => {
    const rows = riskReturnMetricRows({ total_return: 0.123, sharpe_ratio: 1.5 }, {});
    const byName = Object.fromEntries(rows.map((r) => [r.name, r.value]));
    expect(byName["总收益"]).toBe("12.30%");
    expect(byName["夏普比率"]).toBe("1.500");
  });
  it("顶层回落", () => {
    const rows = riskReturnMetricRows({}, { total_return: 0.1 });
    expect(rows.find((r) => r.name === "总收益")!.value).toBe("10.00%");
  });
});

describe("correlationRows", () => {
  it("无 metrics → []", () => expect(correlationRows(null)).toEqual([]));
  it("三者, Sharpe 用 metrics 值", () => {
    expect(correlationRows({ sharpe_ratio: 2 }).length).toBe(3);
    expect(correlationRows({ sharpe_ratio: 2 })[2].correlation).toBe(2);
  });
});

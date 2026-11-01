import { describe, it, expect } from "vitest";
import { buildDcaSchedule, estimateAvgCost, DEMO_DCA_PRICES } from "./dca";

describe("dca 定投计划表纯函数", () => {
  it("buildDcaSchedule — 逐期追加,累计均价收敛(用固定价格序列)", () => {
    const rows = buildDcaSchedule(
      { amountPerPeriod: 100, periodCount: 3, periodLabel: "周" },
      [100, 50, 100],
    );
    expect(rows).toHaveLength(3);
    // 第1期:100/100=1 单位,均价 100
    expect(rows[0]).toMatchObject({ period: 1, invested: 100, units: 1, totalUnits: 1, avgCost: 100 });
    // 第2期:50 → 2 单位,累计 3 单位,均价 200/3≈66.67
    expect(rows[1]).toMatchObject({ period: 2, invested: 200, units: 2, totalUnits: 3 });
    expect(rows[1].avgCost).toBeCloseTo(66.6667, 2);
    // 第3期:100 → 1 单位,累计 4 单位,均价 300/4=75
    expect(rows[2]).toMatchObject({ period: 3, invested: 300, units: 1, totalUnits: 4, avgCost: 75 });
  });

  it("无价格序列 → 价格恒 1,均价=1(每期得 100 单位)", () => {
    const rows = buildDcaSchedule({ amountPerPeriod: 100, periodCount: 2, periodLabel: "日" });
    // 100/1=100 单位/期,累计 200 单位,均价 200/200=1
    expect(rows[1]).toMatchObject({ avgCost: 1, totalUnits: 200, invested: 200 });
  });

  it("estimateAvgCost — 累计均价", () => {
    expect(estimateAvgCost(300, 4)).toBe(75);
    expect(estimateAvgCost(0, 0)).toBe(0);
  });

  it("DEMO_DCA_PRICES — 演示行情够期数且为正", () => {
    expect(DEMO_DCA_PRICES.length).toBeGreaterThanOrEqual(10);
    expect(DEMO_DCA_PRICES.every((p) => p > 0)).toBe(true);
  });
});

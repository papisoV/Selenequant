import { describe, expect, it } from "vitest";
import { buildBacktestDetailOption } from "./backtest-detail-chart";

const COLORS = {
  gold: "#e3b650",
  ink3: "#767a7e",
  destructive: "#f3615c",
  borderSoft: "#14181c",
};

describe("buildBacktestDetailOption", () => {
  it("grid 恒为三层(敞口缺席时空层,同 drawdown 缺席先例);dataZoom 联动三轴", () => {
    const opt = buildBacktestDetailOption({
      timestamps: [1, 2, 3],
      equityCurve: [100, 101, 99],
      colors: COLORS,
    });
    expect(opt.grid).toHaveLength(3);
    expect(opt.xAxis).toHaveLength(3);
    expect(opt.yAxis).toHaveLength(3);
    const zooms = opt.dataZoom as { xAxisIndex: number[] }[];
    expect(zooms.every((z) => z.xAxisIndex.length === 3)).toBe(true);
  });

  it("只有 equity 时 series 为 1 条;补 buy_hold/drawdown 后为 3 条", () => {
    const base = { timestamps: [1, 2], equityCurve: [100, 102], colors: COLORS };
    expect((buildBacktestDetailOption(base).series as unknown[]).length).toBe(1);
    const full = buildBacktestDetailOption({
      ...base,
      buyHoldCurve: [100, 101],
      drawdownCurve: [0, -0.01],
    });
    expect((full.series as unknown[]).length).toBe(3);
  });

  it("长度不齐的 buy_hold/drawdown/exposure 不画(不造假对齐)", () => {
    const opt = buildBacktestDetailOption({
      timestamps: [1, 2],
      equityCurve: [100, 102],
      buyHoldCurve: [100],
      drawdownCurve: [0, -0.01, -0.02],
      exposureCurve: [0],
      colors: COLORS,
    });
    expect((opt.series as unknown[]).length).toBe(1);
  });

  it("敞口层:ink3 细实线 + 百分数 formatter", () => {
    const opt = buildBacktestDetailOption({
      timestamps: [1, 2, 3],
      equityCurve: [100, 101, 99],
      exposureCurve: [0, 0.5, 0.9],
      colors: COLORS,
    });
    const [, exposure] = opt.series as {
      name: string;
      lineStyle: { color: string; width: number };
      yAxisIndex: number;
    }[];
    expect(exposure.name).toBe("敞口");
    expect(exposure.lineStyle.color).toBe("#767a7e");
    expect(exposure.lineStyle.width).toBe(1);
    expect(exposure.yAxisIndex).toBe(2);
    const [, yDd, yExp] = opt.yAxis as {
      axisLabel: { formatter: (v: number) => string };
    }[];
    // 回撤保留 1 位小数:0.0094 显示成 "9.4%",否则四舍五入成 "9%" 抹掉可读精度
    expect(yDd.axisLabel.formatter(0.15)).toBe("15.0%");
    expect(yDd.axisLabel.formatter(0.00938)).toBe("0.9%");
    expect(yExp.axisLabel.formatter(-1.25)).toBe("-125%");
  });

  it("每条轴都显式绑定 gridIndex(否则回撤/敞口会叠进主图)", () => {
    const opt = buildBacktestDetailOption({
      timestamps: [1, 2, 3],
      equityCurve: [100, 101, 99],
      buyHoldCurve: [100, 100, 99],
      drawdownCurve: [0, -0.01, -0.02],
      exposureCurve: [0, 1, 1],
      colors: COLORS,
    });
    const xs = opt.xAxis as { gridIndex?: number }[];
    const ys = opt.yAxis as { gridIndex?: number }[];
    // 缺省 = 全部落 grid 0,这是"红色回撤盖住权益线"的真因,必须逐轴钉死
    expect(xs.map((x) => x.gridIndex)).toEqual([0, 1, 2]);
    expect(ys.map((y) => y.gridIndex)).toEqual([0, 1, 2]);
  });

  it("回撤 series 的 yAxisIndex 与绘制格一致(2 号也无错位)", () => {
    const opt = buildBacktestDetailOption({
      timestamps: [1, 2, 3],
      equityCurve: [100, 101, 99],
      drawdownCurve: [0, -0.01, -0.02],
      exposureCurve: [0, 1, 1],
      colors: COLORS,
    });
    const byName = Object.fromEntries(
      (opt.series as { name: string; yAxisIndex: number }[]).map((s) => [s.name, s.yAxisIndex]),
    );
    expect(byName["回撤"]).toBe(1);
    expect(byName["敞口"]).toBe(2);
  });

  it("时间戳长度与 equity 不齐 → 回退 category 轴", () => {
    const opt = buildBacktestDetailOption({
      timestamps: [1, 2, 3],
      equityCurve: [100, 102],
      colors: COLORS,
    });
    const [x] = opt.xAxis as { type: string; data?: unknown[] }[];
    expect(x.type).toBe("category");
    expect(x.data).toHaveLength(2);
  });

  it("空输入不炸:0 点 equity 走 category 回退", () => {
    const opt = buildBacktestDetailOption({
      timestamps: [],
      equityCurve: [],
      colors: COLORS,
    });
    const [x] = opt.xAxis as { type: string }[];
    expect(x.type).toBe("category");
  });

  it("颜色由参数注入,不硬编码(主题翻转时换参即可)", () => {
    const flipped = { ...COLORS, gold: "#61bc66" };
    const opt = buildBacktestDetailOption({
      timestamps: [1, 2],
      equityCurve: [100, 102],
      colors: flipped,
    });
    const [equity] = opt.series as { lineStyle: { color: string } }[];
    expect(equity.lineStyle.color).toBe("#61bc66");
  });
});

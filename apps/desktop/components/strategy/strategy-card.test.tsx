import { describe, it, expect } from "vitest";
import {
  directionLabel,
  extractMetrics,
  extractEquityCurve,
  formatDuration,
  strategyStatusLabel,
  buildLiveEquityCurve,
} from "./strategy-card";

// 行为锁: strategy-card 纯函数契约。重写必须保持; 撤掉实现改动 → 必红。

describe("directionLabel", () => {
  it("short_only → 只做空", () => expect(directionLabel("short_only")).toBe("只做空"));
  it("neutral → 中性", () => expect(directionLabel("neutral")).toBe("中性"));
  it("其它(含 long_only/undefined) → 只做多", () => {
    expect(directionLabel("long_only")).toBe("只做多");
    expect(directionLabel(undefined as never)).toBe("只做多");
  });
});

describe("extractMetrics", () => {
  it("优先 result.metrics", () => {
    const bt = { result: { metrics: { sharpe_ratio: 1.5, total_return: 0.2, max_drawdown: -0.1 } }, sharpe_ratio: 9 } as never;
    expect(extractMetrics(bt)).toEqual({ sharpe: 1.5, ret: 0.2, dd: -0.1 });
  });
  it("回落顶层字段", () => {
    const bt = { sharpe_ratio: 2, total_return: 0.3, max_drawdown: -0.05 } as never;
    expect(extractMetrics(bt)).toEqual({ sharpe: 2, ret: 0.3, dd: -0.05 });
  });
  it("全缺 → 0", () => {
    expect(extractMetrics({} as never)).toEqual({ sharpe: 0, ret: 0, dd: 0 });
  });
});

describe("extractEquityCurve", () => {
  it("优先 result.equity_curve", () => {
    expect(extractEquityCurve({ result: { equity_curve: [1, 2] }, equity_curve: [9] } as never)).toEqual([1, 2]);
  });
  it("回落顶层 equity_curve", () => {
    expect(extractEquityCurve({ equity_curve: [3, 4] } as never)).toEqual([3, 4]);
  });
  it("无 → 空数组", () => expect(extractEquityCurve({} as never)).toEqual([]));
});

describe("formatDuration", () => {
  it("秒级", () => expect(formatDuration(45_000)).toBe("45s"));
  it("分钟级", () => expect(formatDuration(5 * 60_000)).toBe("5m"));
  it("小时级(带余分)", () => {
    expect(formatDuration(2 * 3_600_000)).toBe("2h");
    expect(formatDuration(2 * 3_600_000 + 30 * 60_000)).toBe("2h 30m");
  });
  it("天级(带余时)", () => {
    expect(formatDuration(3 * 86_400_000)).toBe("3d");
    expect(formatDuration(3 * 86_400_000 + 5 * 3_600_000)).toBe("3d 5h");
  });
});

describe("strategyStatusLabel", () => {
  it("优先级: 运行中 > 已停止 > 已回测 > 草稿", () => {
    expect(strategyStatusLabel(true, true, true)).toBe("运行中");
    expect(strategyStatusLabel(false, true, true)).toBe("已停止");
    expect(strategyStatusLabel(false, false, true)).toBe("已回测");
    expect(strategyStatusLabel(false, false, false)).toBe("草稿");
  });
});

describe("buildLiveEquityCurve", () => {
  it("无点 → 空", () => {
    expect(buildLiveEquityCurve(undefined, undefined)).toEqual([]);
    expect(buildLiveEquityCurve([], 100)).toEqual([]);
  });
  it("1 点 + 正账户权益 → 两点", () => {
    expect(buildLiveEquityCurve([{ equity: 50 }], 80)).toEqual([50, 80]);
  });
  it("1 点 + 无账户权益 → 空", () => {
    expect(buildLiveEquityCurve([{ equity: 50 }], undefined)).toEqual([]);
  });
  it("≥2 点 → 序列; 有账户权益则补末尾", () => {
    expect(buildLiveEquityCurve([{ equity: 1 }, { equity: 2 }], undefined)).toEqual([1, 2]);
    expect(buildLiveEquityCurve([{ equity: 1 }, { equity: 2 }], 3)).toEqual([1, 2, 3]);
  });
  it("账户权益为 0 → 不补", () => {
    expect(buildLiveEquityCurve([{ equity: 1 }, { equity: 2 }], 0)).toEqual([1, 2]);
  });
});

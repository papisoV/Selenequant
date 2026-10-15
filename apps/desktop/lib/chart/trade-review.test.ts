import { describe, expect, it } from "vitest";
import {
  barEstimateTone,
  buildFocusMarkers,
  buildOverviewMarkers,
  clipToWindow,
  estimateBars,
  fitsOnePage,
  overviewPlan,
  paginationPlan,
  pickReviewInterval,
  reviewLadder,
  windowBarCount,
  BAR_COUNT_HEAVY,
  BAR_COUNT_WARN,
  LOSS_COLOR,
  WIN_COLOR,
} from "./trade-review";
import type { Kline } from "@/lib/types/backend";
import type { BacktestTrade } from "@/lib/api/backtests";

const H = 3_600_000;

function mkTrade(over: Partial<BacktestTrade> = {}): BacktestTrade {
  return {
    entry_time: 1_700_000_000_000,
    exit_time: 1_700_000_000_000 + 48 * H, // 2 天窗口
    side: "buy",
    pnl: 12.5,
    ...over,
  };
}

function mkKlines(fromMs: number, count: number, stepMs: number): Kline[] {
  return Array.from({ length: count }, (_, i) => ({
    timestamp: fromMs + i * stepMs,
    open: 100, high: 101, low: 99, close: 100, volume: 1,
  }));
}

describe("reviewLadder(降档阶梯)", () => {
  it("回测 1d → 从自身降到 1m", () => {
    expect(reviewLadder("1d")).toEqual(["1d", "4h", "1h", "15m", "5m", "1m"]);
  });
  it("回测 4h → 4h 打头", () => {
    expect(reviewLadder("4h")[0]).toBe("4h");
  });
  it("未知周期 → 默认从 1h 起", () => {
    expect(reviewLadder("3h")[0]).toBe("1h");
  });
});

describe("windowBarCount / fitsOnePage", () => {
  it("2 天窗口 @1d = 3 根 + 20 边距", () => {
    const t = mkTrade();
    expect(windowBarCount(t.entry_time!, t.exit_time!, "1d")).toBe(23);
  });
  it("2 天窗口 @1h 单页装得下", () => {
    const t = mkTrade();
    expect(fitsOnePage(t.entry_time!, t.exit_time!, "1h")).toBe(true);
  });
  it("无间隔(0ms)也给最少 1+边距 根", () => {
    expect(windowBarCount(1000, 1000, "1h")).toBe(21);
  });
});

describe("pickReviewInterval(自动降档)", () => {
  it("2 天窗口 @1d 只 23 根 <10 有形 → 降到 4h(≥10)", () => {
    const t = mkTrade();
    expect(pickReviewInterval(t.entry_time!, t.exit_time!, "1d")).toBe("4h");
  });
  it("10 天窗口 @1d 已 ≥10 根 → 保持 1d", () => {
    const t = mkTrade({ exit_time: 1_700_000_000_000 + 10 * 24 * H });
    expect(pickReviewInterval(t.entry_time!, t.exit_time!, "1d")).toBe("1d");
  });
  it("5 分钟超短窗口 → 降到 1m", () => {
    const t = mkTrade({ exit_time: 1_700_000_000_000 + 5 * 60_000 });
    expect(pickReviewInterval(t.entry_time!, t.exit_time!, "1d")).toBe("1m");
  });
});

describe("paginationPlan / overviewPlan(翻页计划)", () => {
  it("1m×2天窗口 ≈ 2880+21 根 → 2 页", () => {
    const t = mkTrade({ exit_time: 1_700_000_000_000 + 2 * 24 * H });
    const plan = paginationPlan(t.entry_time!, t.exit_time!, "1m", 5);
    expect(plan.pages).toBe(2);
    expect(plan.endTime).toBe(t.exit_time);
  });
  it("封顶 maxPages", () => {
    const plan = paginationPlan(0, 30 * 24 * H, "1m", 5);
    expect(plan.pages).toBe(5);
  });
  it("全景 1d×30 天 ≈ 31 根 → 1 页", () => {
    const plan = overviewPlan(0, 30 * 24 * H, "1d");
    expect(plan.pages).toBe(1);
  });
});

describe("clipToWindow(裁剪即聚焦)", () => {
  it("裁掉窗口±10根边距之外的 K 线(两端含)", () => {
    const t = mkTrade();
    const raw = mkKlines(t.entry_time! - 100 * H, 300, H); // 窗口前后各 100 根
    const clipped = clipToWindow(raw, t.entry_time!, t.exit_time!, "1h");
    expect(clipped.length).toBe(windowBarCount(t.entry_time!, t.exit_time!, "1h")); // 48+1+20=69,两端含
    expect(clipped.every((k) => k.timestamp! >= t.entry_time! - 10 * H && k.timestamp! <= t.exit_time! + 10 * H)).toBe(true);
  });
});

describe("buildFocusMarkers(盈亏着色 + 方向形状)", () => {
  it("盈利多头: entry 箭头↑ + exit 圆点,全 WIN 绿,exit 带 PnL 文本", () => {
    const markers = buildFocusMarkers(mkTrade({ side: "buy", pnl: 12.5 }));
    expect(markers).toHaveLength(2);
    expect(markers[0].shape).toBe("arrowUp");
    expect(markers[0].color).toBe(WIN_COLOR);
    expect(markers[1].shape).toBe("circle");
    expect(markers[1].text).toBe("+12.50");
    expect(markers[0].time).toBe(Math.floor(1_700_000_000_000 / 1000));
  });
  it("亏损空头: entry 箭头↓ + LOSS 红", () => {
    const markers = buildFocusMarkers(mkTrade({ side: "sell", pnl: -3 }));
    expect(markers[0].shape).toBe("arrowDown");
    expect(markers[0].color).toBe(LOSS_COLOR);
  });
  it("缺 entry_time → 空数组(不造标记)", () => {
    expect(buildFocusMarkers(mkTrade({ entry_time: undefined }))).toEqual([]);
  });
});

describe("buildOverviewMarkers(同 bar ×N 聚合)", () => {
  it("同 bar 两笔 → 合并为一条带 ×N", () => {
    const t1 = mkTrade();
    const t2 = mkTrade(); // 完全同窗
    const markers = buildOverviewMarkers([t1, t2], "1d");
    const withN = markers.filter((m) => m.text?.includes("×2"));
    expect(withN.length).toBeGreaterThan(0);
  });
  it("不同 bar 各自独立", () => {
    const markers = buildOverviewMarkers(
      [mkTrade(), mkTrade({ exit_time: 1_700_000_000_000 + 48 * H + 30 * 24 * H })],
      "1d",
    );
    expect(markers.length).toBeGreaterThanOrEqual(3); // 2×entry + 远端 exit
  });
});

describe("estimateBars(区间预估根数)", () => {
  const DAY = 86_400_000;

  it("365 天 @1d = 366 根", () => {
    expect(estimateBars(0, 365 * DAY, "1d")).toBe(366);
  });
  it("7 天 @1h = 169 根", () => {
    expect(estimateBars(0, 7 * DAY, "1h")).toBe(169);
  });
  it("1m × 2 年 ≈ 105 万根(后端无守卫,这就是唯一预警)", () => {
    expect(estimateBars(0, 730 * DAY, "1m")).toBe(730 * 1440 + 1);
  });
  it("区间倒置 → null(不造假)", () => {
    expect(estimateBars(1000, 500, "1h")).toBeNull();
  });
  it("等值区间 → null", () => {
    expect(estimateBars(1000, 1000, "1h")).toBeNull();
  });
  it("未知周期 → null", () => {
    expect(estimateBars(0, DAY, "3h")).toBeNull();
  });
  it("非有限时间戳 → null", () => {
    expect(estimateBars(Number.NaN, DAY, "1h")).toBeNull();
  });
});

describe("barEstimateTone(三档阈值)", () => {
  it("4999 → ok", () => {
    expect(barEstimateTone(4_999)).toBe("ok");
  });
  it("5000 恰好 → warn(边界含)", () => {
    expect(barEstimateTone(BAR_COUNT_WARN)).toBe("warn");
  });
  it("19999 → warn", () => {
    expect(barEstimateTone(19_999)).toBe("warn");
  });
  it("20000 恰好 → heavy(边界含)", () => {
    expect(barEstimateTone(BAR_COUNT_HEAVY)).toBe("heavy");
  });
  it("105 万根 → heavy", () => {
    expect(barEstimateTone(1_051_201)).toBe("heavy");
  });
});

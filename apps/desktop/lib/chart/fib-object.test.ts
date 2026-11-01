import { describe, expect, it } from "vitest";

import {
  EXTENSION_LEVELS,
  HAND_DRAWN_LEVELS,
  NEGATIVE_EXTENSION_LEVELS,
  defaultVisibleLevels,
  formatLevelLabel,
  isExtensionLevel,
  isInteriorLevel,
  levelColorSlot,
} from "@/lib/chart/fib-levels";

import {
  gridPrices,
  gridTimeRange,
  isDegenerate,
  isVisibleOnChart,
  legDirection,
  normalizeLevels,
  priceAtLevel,
  type FibGrid,
} from "@/lib/chart/fib-object";

function makeGrid(over: Partial<FibGrid> = {}): FibGrid {
  return {
    id: "g1",
    groupId: null,
    start: { time: 1_700_000_000, price: 100 },
    end: { time: 1_700_003_600, price: 200 },
    mode: "price",
    levels: [0, 0.5, 1],
    autoExtend: false,
    extendLeft: false,
    extendRight: false,
    visible: true,
    locked: false,
    timeframe: "1h",
    symbol: "SOL/USDT",
    label: "",
    ...over,
  };
}

describe("档位族", () => {
  it("11 条手绘档位,与 fib_corner.py 的 FIB_LEVELS 完全一致", () => {
    // 这串数字必须与 packages/strategy-engine/.../fib_corner.py 的
    // FIB_LEVELS 逐项相同 —— 图上画的线和策略判定的线是同一套。
    expect(HAND_DRAWN_LEVELS).toEqual([
      0, 0.2, 0.236, 0.3, 0.382, 0.5, 0.618, 0.7, 0.786, 0.886, 1,
    ]);
  });

  it("不是 TradingView 默认的 7 条 —— 多出 0.2/0.3/0.7/0.886", () => {
    const tvDefaults = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
    const extra = HAND_DRAWN_LEVELS.filter((lv) => !tvDefaults.includes(lv));
    expect(extra).toEqual([0.2, 0.3, 0.7, 0.886]);
  });

  it("延伸位不含腿内部的比例", () => {
    for (const lv of EXTENSION_LEVELS) expect(lv).toBeGreaterThan(1);
    for (const lv of NEGATIVE_EXTENSION_LEVELS) expect(lv).toBeLessThan(0);
  });

  it("默认隐藏两个端点(0 和 1 由腿自身表达)", () => {
    const vis = defaultVisibleLevels();
    expect(vis).not.toContain(0);
    expect(vis).not.toContain(1);
    expect(vis).toHaveLength(HAND_DRAWN_LEVELS.length - 2);
  });

  it("端点不算内部位,延伸不算内部位", () => {
    expect(isInteriorLevel(0)).toBe(false);
    expect(isInteriorLevel(1)).toBe(false);
    expect(isInteriorLevel(0.382)).toBe(true);
    expect(isExtensionLevel(1.618)).toBe(true);
    expect(isExtensionLevel(-0.618)).toBe(true);
    expect(isExtensionLevel(0.5)).toBe(false);
  });

  it("标签去尾零,不写死小数位", () => {
    expect(formatLevelLabel(0.2)).toBe("0.2");
    expect(formatLevelLabel(0.236)).toBe("0.236");
    expect(formatLevelLabel(0.5)).toBe("0.5");
    expect(formatLevelLabel(1)).toBe("1");
    expect(formatLevelLabel(1.272)).toBe("1.272");
  });

  it("0.5 独占主线色槽,其余档位颜色互不相同", () => {
    expect(levelColorSlot(0.5)).toBe(-1);
    const slots = HAND_DRAWN_LEVELS.filter((lv) => lv !== 0.5).map(levelColorSlot);
    expect(new Set(slots).size).toBe(slots.length);
  });
});

describe("价格换算", () => {
  it("腿部比例 0→start、1→end,中间线性", () => {
    expect(priceAtLevel(100, 200, 0)).toBe(100);
    expect(priceAtLevel(100, 200, 1)).toBe(200);
    expect(priceAtLevel(100, 200, 0.5)).toBe(150);
    expect(priceAtLevel(100, 200, 0.382)).toBeCloseTo(138.2, 6);
  });

  it("下行腿同样成立(端价低于起价)", () => {
    expect(priceAtLevel(200, 100, 0.5)).toBe(150);
    expect(priceAtLevel(200, 100, 0.618)).toBeCloseTo(138.2, 6);
  });

  it("延伸位按同一线性关系外推", () => {
    // 腿 100→200,1.618 位 = 100 + 100*1.618 = 261.8
    expect(priceAtLevel(100, 200, 1.618)).toBeCloseTo(261.8, 6);
    // 负向延伸回到腿起点之下
    expect(priceAtLevel(100, 200, -0.618)).toBeCloseTo(38.2, 6);
  });

  it("gridPrices 与 levels 同序", () => {
    const grid = makeGrid({ levels: [0, 0.5, 1] });
    expect(gridPrices(grid)).toEqual([100, 150, 200]);
  });

  it("改一个锚点价格,所有档位一起重算(不存死价格)", () => {
    const a = makeGrid({ levels: [0, 0.5, 1] });
    const b = makeGrid({ levels: [0, 0.5, 1], end: { time: 1_700_003_600, price: 400 } });
    expect(gridPrices(a)).toEqual([100, 150, 200]);
    expect(gridPrices(b)).toEqual([100, 250, 400]);
  });
});

describe("档位归一化", () => {
  it("去重并升序", () => {
    expect(normalizeLevels([1, 0.5, 0.5, 0])).toEqual([0, 0.5, 1]);
  });

  it("剔除 NaN / Infinity —— 坏数据不该画出走向无穷的线", () => {
    expect(normalizeLevels([0, Number.NaN, 0.5, Number.POSITIVE_INFINITY, 1])).toEqual([0, 0.5, 1]);
  });

  it("空输入返回空", () => {
    expect(normalizeLevels([])).toEqual([]);
  });
});

describe("退化与方向", () => {
  it("两端价格相等 → 退化", () => {
    expect(isDegenerate(makeGrid({ end: { time: 1_700_003_600, price: 100 } }))).toBe(true);
  });

  it("两端时间相等 → 退化", () => {
    expect(isDegenerate(makeGrid({ end: { time: 1_700_000_000, price: 200 } }))).toBe(true);
  });

  it("正常腿不退化", () => {
    expect(isDegenerate(makeGrid())).toBe(false);
  });

  it("方向:上行/下行/持平", () => {
    expect(legDirection(makeGrid())).toBe(1);
    expect(legDirection(makeGrid({ end: { time: 1_700_003_600, price: 50 } }))).toBe(-1);
    expect(legDirection(makeGrid({ end: { time: 1_700_003_600, price: 100 } }))).toBe(0);
  });

  it("时间区间与锚点先后顺序无关", () => {
    expect(gridTimeRange(makeGrid())).toEqual({ from: 1_700_000_000, to: 1_700_003_600 });
    expect(
      gridTimeRange(makeGrid({ start: { time: 1_700_003_600, price: 200 }, end: { time: 1_700_000_000, price: 100 } })),
    ).toEqual({ from: 1_700_000_000, to: 1_700_003_600 });
  });
});

describe("跨周期可见性", () => {
  it("同标的换周期照样显示(锚点是时间+价格,与周期无关)", () => {
    const grid = makeGrid({ timeframe: "1h" });
    // 同标的、不同周期 → 可见。这正是"高周期画的线在低周期按时间重锚"。
    expect(isVisibleOnChart(grid, "SOL/USDT")).toBe(true);
  });

  it("不同标的不显示(价位单位不同)", () => {
    expect(isVisibleOnChart(makeGrid({ symbol: "SOL/USDT" }), "BTC/USDT")).toBe(false);
  });

  it("用户隐藏后不显示", () => {
    expect(isVisibleOnChart(makeGrid({ visible: false }), "SOL/USDT")).toBe(false);
  });
});

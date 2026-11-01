import { describe, expect, it } from "vitest";

import { buildDrawingScene, hitTestScene, legSpan, type SceneOptions } from "@/lib/chart/drawing-scene";
import type { FibGrid } from "@/lib/chart/fib-object";
import { ANCHOR_HANDLE_RADIUS, LINE_HIT_TOLERANCE } from "@/lib/chart/fib-style";

const OPTIONS: SceneOptions = { precision: 2, width: 800 };

function grid(over: Partial<FibGrid> = {}): FibGrid {
  return {
    id: "g1",
    groupId: null,
    start: { time: 1000, price: 100 },
    end: { time: 2000, price: 200 },
    mode: "price",
    levels: [0.382, 0.5, 0.618],
    autoExtend: false,
    extendLeft: false,
    extendRight: false,
    visible: true,
    locked: false,
    timeframe: "1h",
    symbol: "BTCUSDT",
    label: "",
    ...over,
  };
}

// 假投影:时间直接当 x,价格 y 翻转(价格越高 y 越小)
const project = {
  xOfTime: (t: number) => t / 10,
  yOfPrice: (p: number) => 600 - p,
};

describe("buildDrawingScene", () => {
  it("每个档位一条线,横向范围就是两锚点之间(线段,不是通栏)", () => {
    const scene = buildDrawingScene([grid()], project, OPTIONS);
    expect(scene.lines).toHaveLength(3);
    // 锚点 x = 100(时间 1000)与 200(时间 2000)—— 线只覆盖这一段
    for (const line of scene.lines) {
      expect(line.x0).toBe(100);
      expect(line.x1).toBe(200);
    }
    expect(scene.lines.map((l) => l.level)).toEqual([0.382, 0.5, 0.618]);
  });

  it("两个锚点左右颠倒时,线段仍按 min/max 归一", () => {
    const scene = buildDrawingScene([grid({ start: { time: 2000, price: 100 }, end: { time: 1000, price: 200 } })], project, OPTIONS);
    for (const line of scene.lines) {
      expect(line.x0).toBe(100);
      expect(line.x1).toBe(200);
    }
  });

  it("extendLeft 把线左端拉到可视区边缘 0", () => {
    const scene = buildDrawingScene([grid({ extendLeft: true })], project, OPTIONS);
    for (const line of scene.lines) {
      expect(line.x0).toBe(0);
      expect(line.x1).toBe(200);
    }
  });

  it("extendRight 把线右端拉到画布宽度", () => {
    const scene = buildDrawingScene([grid({ extendRight: true })], project, OPTIONS);
    for (const line of scene.lines) {
      expect(line.x0).toBe(100);
      expect(line.x1).toBe(800);
    }
  });

  it("两侧都延伸 = 传统通栏", () => {
    const scene = buildDrawingScene([grid({ extendLeft: true, extendRight: true })], project, OPTIONS);
    for (const line of scene.lines) {
      expect(line.x0).toBe(0);
      expect(line.x1).toBe(800);
    }
  });

  it("档位线的 y 按价格现算,不是按锚点 y 插值", () => {
    const scene = buildDrawingScene([grid()], project, OPTIONS);
    // 0.5 价格 = 150 → y = 600 - 150 = 450
    expect(scene.lines.find((l) => l.level === 0.5)!.y).toBe(450);
    // 0.382 价格 = 100 + 100*0.382 = 138.2 → y = 461.8
    expect(scene.lines.find((l) => l.level === 0.382)!.y).toBeCloseTo(461.8, 6);
  });

  it("标签带比例与真实价格", () => {
    const scene = buildDrawingScene([grid()], project, OPTIONS);
    const label = scene.labels.find((l) => l.level === 0.618)!;
    expect(label.text).toBe("0.618  161.80");
    // 标签贴线段右端(200),不是画布右缘 —— width 含价格轴,贴 800 会压住刻度
    expect(label.x).toBe(200 - 6);
  });

  it("extendRight 时标签退回画布可视区,不跑出右缘", () => {
    const scene = buildDrawingScene([grid({ extendRight: true })], project, OPTIONS);
    const label = scene.labels.find((l) => l.level === 0.618)!;
    expect(label.x).toBe(800 - 6);
  });

  it("showLabels=false 时不产标签", () => {
    const scene = buildDrawingScene([grid()], project, { ...OPTIONS, showLabels: false });
    expect(scene.labels).toHaveLength(0);
    expect(scene.lines).toHaveLength(3);
  });

  it("visible=false 的网格整体不画", () => {
    const scene = buildDrawingScene([grid({ visible: false })], project, OPTIONS);
    expect(scene).toEqual({ lines: [], vlines: [], segments: [], labels: [], anchors: [], legs: [] });
  });

  it("锚点与腿各一处,锚点坐标来自两个端点", () => {
    const scene = buildDrawingScene([grid()], project, OPTIONS);
    expect(scene.anchors.map((a) => [a.index, a.x, a.y])).toEqual([
      [0, 100, 500],
      [1, 200, 400],
    ]);
    expect(scene.legs).toHaveLength(1);
    expect(scene.legs[0]).toMatchObject({ x0: 100, y0: 500, x1: 200, y1: 400 });
  });

  it("选中态外的网格标记 dimmed", () => {
    const scene = buildDrawingScene([grid(), grid({ id: "g2" })], project, { ...OPTIONS, selectedIds: new Set(["g1"]) });
    expect(scene.lines.filter((l) => l.gridId === "g1").every((l) => !l.dimmed)).toBe(true);
    expect(scene.lines.filter((l) => l.gridId === "g2").every((l) => l.dimmed)).toBe(true);
  });

  it("多选时集合内的都不压暗", () => {
    const scene = buildDrawingScene([grid(), grid({ id: "g2" }), grid({ id: "g3" })], project, {
      ...OPTIONS,
      selectedIds: new Set(["g1", "g3"]),
    });
    expect(scene.lines.filter((l) => l.gridId !== "g2").every((l) => !l.dimmed)).toBe(true);
    expect(scene.lines.filter((l) => l.gridId === "g2").every((l) => l.dimmed)).toBe(true);
  });

  it("未选中任何对象时全部不压暗", () => {
    const scene = buildDrawingScene([grid()], project, { ...OPTIONS, selectedIds: new Set<string>() });
    expect(scene.lines.every((l) => !l.dimmed)).toBe(true);
  });

  it("投影返回 null 时跳过该网格,不画到原点", () => {
    const broken = {
      xOfTime: () => null,
      yOfPrice: (p: number) => 600 - p,
    };
    const scene = buildDrawingScene([grid()], broken, OPTIONS);
    expect(scene.lines).toHaveLength(0);
    expect(scene.anchors).toHaveLength(0);
  });

  it("投影返回 NaN 时同样跳过", () => {
    const broken = {
      xOfTime: (t: number) => t / 10,
      yOfPrice: () => Number.NaN,
    };
    expect(buildDrawingScene([grid()], broken, OPTIONS).lines).toHaveLength(0);
  });

  it("价格相等(退化腿)仍画出线,不凭空消失", () => {
    const scene = buildDrawingScene([grid({ end: { time: 2000, price: 100 } })], project, OPTIONS);
    expect(scene.lines).toHaveLength(3);
    for (const line of scene.lines) expect(line.y).toBe(500);
  });

  it("延伸位用延伸色且为虚线", () => {
    const scene = buildDrawingScene([grid({ levels: [1.272] })], project, OPTIONS);
    const line = scene.lines[0];
    expect(line.dashed).toBe(true);
    expect(line.color).toBe("#a07ad6");
  });

  it("空 levels 只画锚点与腿", () => {
    const scene = buildDrawingScene([grid({ levels: [] })], project, OPTIONS);
    expect(scene.lines).toHaveLength(0);
    expect(scene.anchors).toHaveLength(2);
    expect(scene.legs).toHaveLength(1);
  });
});

describe("时间斐波那契(mode=time)", () => {
  // 腿:x 100 → 200,长 100px;倍数 k 落在 100 + 100k。
  // 价格差 100 在这里毫无意义 —— 时间模式只用 x。
  const timeGrid = (over: Partial<FibGrid> = {}) => grid({ mode: "time", levels: [2, 3, 5], ...over });

  it("倍数换算成竖线的 x,不出水平线", () => {
    const scene = buildDrawingScene([timeGrid()], project, OPTIONS);
    expect(scene.lines).toHaveLength(0);
    expect(scene.vlines.map((v) => [v.level, v.x])).toEqual([
      [2, 300],
      [3, 400],
      [5, 600],
    ]);
  });

  it("竖线贯穿绘图区高度,不是锚点之间那一小段", () => {
    const scene = buildDrawingScene([timeGrid()], project, { ...OPTIONS, height: 480 });
    for (const v of scene.vlines) {
      expect(v.y0).toBe(0);
      expect(v.y1).toBe(480);
    }
  });

  it("腿反向画时倍数仍向右铺,不出现负 x 的镜像线", () => {
    const scene = buildDrawingScene([timeGrid({ start: { time: 2000, price: 100 }, end: { time: 1000, price: 200 } })], project, OPTIONS);
    expect(scene.vlines.map((v) => v.x)).toEqual([300, 400, 600]);
  });

  it("价格差为 0 也照样出竖线 —— 时间模式不看 y", () => {
    const scene = buildDrawingScene([timeGrid({ end: { time: 2000, price: 100 } })], project, OPTIONS);
    expect(scene.vlines).toHaveLength(3);
  });

  it("标签带 x 倍数后缀,不带价格(价格对竖线没有意义)", () => {
    const scene = buildDrawingScene([timeGrid()], project, OPTIONS);
    const label = scene.labels.find((l) => l.level === 3)!;
    expect(label.text).toBe("3x");
    expect(label.align).toBe("vertical");
  });

  it("别名覆盖倍数文字,仍然竖排", () => {
    const scene = buildDrawingScene([timeGrid({ levelStyles: [{ coeff: 3, text: "T3", color: "#00ff00" }] })], project, OPTIONS);
    const label = scene.labels.find((l) => l.level === 3)!;
    expect(label.text).toBe("T3");
    expect(label.align).toBe("vertical");
    expect(label.color).toBe("#00ff00");
  });

  it("逐档颜色/线宽/虚线对竖线同样生效", () => {
    const scene = buildDrawingScene(
      [timeGrid({ levelStyles: [{ coeff: 5, color: "#ff0000", width: 4, dashed: true }] })],
      project,
      OPTIONS,
    );
    const v = scene.vlines.find((l) => l.level === 5)!;
    expect(v).toMatchObject({ color: "#ff0000", size: 4, dashed: true });
  });

  it("showLabels=false 时不产竖排标签,竖线仍在", () => {
    const scene = buildDrawingScene([timeGrid()], project, { ...OPTIONS, showLabels: false });
    expect(scene.labels).toHaveLength(0);
    expect(scene.vlines).toHaveLength(3);
  });

  it("同场景里的价格网格与时序网格互不串味", () => {
    const scene = buildDrawingScene([grid(), timeGrid({ id: "g2" })], project, OPTIONS);
    expect(scene.lines.every((l) => l.gridId === "g1")).toBe(true);
    expect(scene.vlines.every((v) => v.gridId === "g2")).toBe(true);
    expect(scene.lines).toHaveLength(3);
    expect(scene.vlines).toHaveLength(3);
  });

  it("命中竖线返回 kind=vline,并带上倍数", () => {
    const scene = buildDrawingScene([timeGrid()], project, { ...OPTIONS, height: 480 });
    const hit = hitTestScene(scene, 400 + 2, 240);
    expect(hit).toMatchObject({ kind: "vline", gridId: "g1", level: 3 });
  });

  it("竖线容差外落空", () => {
    const scene = buildDrawingScene([timeGrid({ levels: [3] })], project, { ...OPTIONS, height: 480 });
    expect(hitTestScene(scene, 400 + LINE_HIT_TOLERANCE + 5, 240)).toBeNull();
  });

  it("绘图区高度外不命中竖线 —— 与绘制范围同源", () => {
    const scene = buildDrawingScene([timeGrid({ levels: [3] })], project, { ...OPTIONS, height: 480 });
    expect(hitTestScene(scene, 400, 480 + 10)).toBeNull();
  });

  it("时间网格仍然保留锚点与腿,可以拖动改腿长", () => {
    const scene = buildDrawingScene([timeGrid()], project, OPTIONS);
    expect(scene.anchors).toHaveLength(2);
    expect(scene.legs).toHaveLength(1);
    expect(hitTestScene(scene, 100, 500)).toMatchObject({ kind: "anchor", index: 0 });
  });
});

describe("基础图形(mode=trend/horizontal/vertical/rect)", () => {
  // 默认 grid:start(1000,100) → 像素 (100,500);end(2000,200) → 像素 (200,400)。

  it("趋势线 = 两个锚点之间的一条线段,不出档位线/竖线/标签/腿", () => {
    const scene = buildDrawingScene([grid({ mode: "trend", levels: [] })], project, OPTIONS);
    expect(scene.segments).toHaveLength(1);
    expect(scene.segments[0]).toMatchObject({ gridId: "g1", level: 0, x0: 100, y0: 500, x1: 200, y1: 400 });
    expect(scene.lines).toHaveLength(0);
    expect(scene.vlines).toHaveLength(0);
    expect(scene.labels).toHaveLength(0);
    expect(scene.legs).toHaveLength(0);
    expect(scene.anchors).toHaveLength(2);
  });

  it("趋势线即便带着非空 levels 也不画档位 —— 几何与档位无关", () => {
    const scene = buildDrawingScene([grid({ mode: "trend", levels: [0.382, 0.5, 0.618] })], project, OPTIONS);
    expect(scene.lines).toHaveLength(0);
    expect(scene.segments).toHaveLength(1);
  });

  it("水平线通栏,价位取 end,只留一个把手", () => {
    const scene = buildDrawingScene([grid({ mode: "horizontal", levels: [] })], project, OPTIONS);
    expect(scene.lines).toHaveLength(1);
    expect(scene.lines[0]).toMatchObject({ x0: 0, x1: 800, y: 400 });
    expect(scene.labels.map((l) => [l.x, l.text])).toEqual([[800 - 6, "200.00"]]);
    expect(scene.anchors.map((a) => [a.index, a.x, a.y])).toEqual([[1, 200, 400]]);
    expect(scene.legs).toHaveLength(0);
  });

  it("竖线通栏,时间取 end,只留一个把手", () => {
    const scene = buildDrawingScene([grid({ mode: "vertical", levels: [] })], project, { ...OPTIONS, height: 480 });
    expect(scene.vlines).toHaveLength(1);
    expect(scene.vlines[0]).toMatchObject({ x: 200, y0: 0, y1: 480 });
    expect(scene.anchors.map((a) => [a.index, a.x, a.y])).toEqual([[1, 200, 400]]);
    expect(scene.lines).toHaveLength(0);
    expect(scene.legs).toHaveLength(0);
  });

  it("矩形拆成两条水平边 + 两条竖直边,锚点仍是两个对角点", () => {
    const scene = buildDrawingScene([grid({ mode: "rect", levels: [] })], project, OPTIONS);
    expect(scene.lines.map((l) => l.y).sort((a, b) => a - b)).toEqual([400, 500]);
    expect(scene.vlines.map((v) => v.x).sort((a, b) => a - b)).toEqual([100, 200]);
    for (const l of scene.lines) expect([l.x0, l.x1]).toEqual([100, 200]);
    for (const v of scene.vlines) expect([v.y0, v.y1]).toEqual([400, 500]);
    expect(scene.anchors).toHaveLength(2);
    expect(scene.segments).toHaveLength(0);
    expect(scene.legs).toHaveLength(0);
  });

  it("矩形对角点颠倒时仍按 min/max 归一", () => {
    const scene = buildDrawingScene(
      [grid({ mode: "rect", levels: [], start: { time: 2000, price: 200 }, end: { time: 1000, price: 100 } })],
      project,
      OPTIONS,
    );
    expect(scene.lines.map((l) => l.y).sort((a, b) => a - b)).toEqual([400, 500]);
    expect(scene.vlines.map((v) => v.x).sort((a, b) => a - b)).toEqual([100, 200]);
  });

  it("基础图形跟随选中集合压暗", () => {
    const scene = buildDrawingScene([grid({ mode: "trend", levels: [] })], project, {
      ...OPTIONS,
      selectedIds: new Set(["other"]),
    });
    expect(scene.segments[0].dimmed).toBe(true);
  });
});

describe("hitTestScene", () => {
  const scene = buildDrawingScene([grid()], project, OPTIONS);

  it("命中锚点时优先于同位置的线", () => {
    // 档位 0 的线正好穿过 start 锚点:两者重叠时锚点必须赢,否则永远抓不住把手
    const overlap = buildDrawingScene([grid({ levels: [0] })], project, OPTIONS);
    const anchor = overlap.anchors.find((a) => a.index === 0)!;
    expect(anchor.y).toBe(overlap.lines[0].y);
    expect(hitTestScene(overlap, anchor.x, anchor.y)).toMatchObject({ kind: "anchor", index: 0 });
  });

  it("锚点容差内也能命中", () => {
    const hit = hitTestScene(scene, 100 + ANCHOR_HANDLE_RADIUS + 2, 500);
    expect(hit).toMatchObject({ kind: "anchor", index: 0 });
  });

  it("超出容差不命中", () => {
    expect(hitTestScene(scene, 100 + ANCHOR_HANDLE_RADIUS + 20, 500)).toBeNull();
  });

  it("命中档位线", () => {
    const line = scene.lines.find((l) => l.level === 0.5)!;
    // 探针必须落在线段跨度内(100..200);线段外的 x 见下面的跨度测试
    const hit = hitTestScene(scene, 150, line.y + 2);
    expect(hit).toMatchObject({ kind: "line", level: 0.5 });
  });

  it("线容差外不命中 —— 但邻近档位线仍可能接住,这是对的", () => {
    const single = buildDrawingScene([grid({ levels: [0.5] })], project, OPTIONS);
    const line = single.lines[0];
    expect(hitTestScene(single, 150, line.y + LINE_HIT_TOLERANCE + 5)).toBeNull();
    // 三条线挨得很近时,探针落在 0.5 的容差外会命中邻居 0.382,而不是空
    const dense = scene.lines.find((l) => l.level === 0.5)!;
    const hit = hitTestScene(scene, 150, dense.y + LINE_HIT_TOLERANCE + 5);
    expect(hit).toMatchObject({ kind: "line", level: 0.382 });
  });

  it("线段跨度外不命中 —— 锚点左侧的空白不再算在线", () => {
    // 默认几何是线段(100..200):左侧 blank 处 Probe 必须落空,
    // 这正是"通栏时代"被 hitTestScene 的跨度检查挡不住的那一段。
    const line = scene.lines.find((l) => l.level === 0.5)!;
    expect(hitTestScene(scene, 50, line.y)).toBeNull();
    // 但线段内部照样命中
    expect(hitTestScene(scene, 150, line.y)).toMatchObject({ kind: "line", level: 0.5 });
  });

  it("extendLeft 后锚点左侧的线可命中 —— 绘制范围与拾取范围同源", () => {
    const extended = buildDrawingScene([grid({ extendLeft: true, levels: [0.5] })], project, OPTIONS);
    const line = extended.lines[0];
    expect(hitTestScene(extended, 50, line.y)).toMatchObject({ kind: "line", level: 0.5 });
  });

  it("命中标签", () => {
    const label = scene.labels.find((l) => l.level === 0.5)!;
    const hit = hitTestScene(scene, label.x - 10, label.y - 4);
    expect(hit).toMatchObject({ kind: "label", gridId: "g1", level: 0.5 });
  });

  it("空白处返回 null", () => {
    expect(hitTestScene(scene, 400, 100)).toBeNull();
  });

  it("多个锚点重叠时取最近", () => {
    const scene2 = buildDrawingScene(
      [grid(), grid({ id: "g2", start: { time: 1000, price: 100 }, end: { time: 2100, price: 200 } })],
      project,
      OPTIONS,
    );
    // 两个网格的 start 锚点都在 (100,500),距离相同 → 至少能稳定命中一个
    const hit = hitTestScene(scene2, 100, 500);
    expect(hit?.kind).toBe("anchor");
  });

  it("命中趋势线线段 —— 按到线段的垂距,不是纵向差", () => {
    // 线段 (100,500)→(200,400),中点 (150,450);法线方向偏移 2px。
    const trend = buildDrawingScene([grid({ mode: "trend", levels: [] })], project, OPTIONS);
    const hit = hitTestScene(trend, 150, 448);
    expect(hit).toMatchObject({ kind: "segment", gridId: "g1", level: 0 });
  });

  it("趋势线容差外落空", () => {
    const trend = buildDrawingScene([grid({ mode: "trend", levels: [] })], project, OPTIONS);
    // 纵向偏移 LINE_HIT_TOLERANCE 时,到 45° 线段的垂距是 d/√2 ≈ 2.83,
    // 仍在容差内 —— 所以这里要取更大的偏移量才算真的落空。
    const justOutside = LINE_HIT_TOLERANCE * Math.SQRT2 + 2;
    expect(hitTestScene(trend, 150, 450 + justOutside)).toBeNull();
  });

  it("趋势线延长线不命中 —— 拾取范围与绘制范围同源", () => {
    const trend = buildDrawingScene([grid({ mode: "trend", levels: [] })], project, OPTIONS);
    // (50,550) 落在同一条直线上,但在线段两端之外
    expect(hitTestScene(trend, 50, 550)).toBeNull();
    expect(hitTestScene(trend, 250, 350)).toBeNull();
  });

  it("命中水平线的竖边 —— 矩形四条边都能点中", () => {
    const rect = buildDrawingScene([grid({ mode: "rect", levels: [] })], project, OPTIONS);
    // 上边 y=400,x 取中间
    expect(hitTestScene(rect, 150, 402)).toMatchObject({ kind: "line", gridId: "g1" });
    // 左边 x=100,y 取中间(避开两个角上的锚点)
    expect(hitTestScene(rect, 102, 450)).toMatchObject({ kind: "vline", gridId: "g1" });
  });
});

describe("legSpan", () => {
  it("给出两锚点的像素差值", () => {
    const scene = buildDrawingScene([grid()], project, OPTIONS);
    expect(legSpan(scene, "g1")).toEqual({ dx: 100, dy: -100 });
  });

  it("未知网格返回 null", () => {
    const scene = buildDrawingScene([grid()], project, OPTIONS);
    expect(legSpan(scene, "nope")).toBeNull();
  });
});

describe("逐档样式覆盖在场景里的表现", () => {
  it("只改被覆盖那一档的颜色与线宽", () => {
    const scene = buildDrawingScene([grid({ levelStyles: [{ coeff: 0.618, color: "#ff0000", width: 3 }] })], project, OPTIONS);
    const hit = scene.lines.find((l) => l.level === 0.618)!;
    const rest = scene.lines.filter((l) => l.level !== 0.618);
    expect(hit.color).toBe("#ff0000");
    expect(hit.size).toBe(3);
    for (const line of rest) expect(line.color).not.toBe("#ff0000");
  });

  it("未覆盖的字段沿用默认桶,不是整体替换", () => {
    const scene = buildDrawingScene([grid({ levelStyles: [{ coeff: 0.618, color: "#ff0000" }] })], project, OPTIONS);
    const line = scene.lines.find((l) => l.level === 0.618)!;
    expect(line.color).toBe("#ff0000");
    // 0.618 的默认线宽是 1.2,颜色改了不代表宽度也要变成兜底值
    expect(line.size).toBe(1.2);
    expect(line.dashed).toBe(false);
  });

  it("别名替换标签里的比例文字,价格仍在", () => {
    const scene = buildDrawingScene([grid({ levelStyles: [{ coeff: 0.618, text: "TP1" }] })], project, OPTIONS);
    const label = scene.labels.find((l) => l.level === 0.618)!;
    expect(label.text).toBe("TP1  161.80");
  });

  it("别名首尾空白被裁掉", () => {
    const scene = buildDrawingScene([grid({ levelStyles: [{ coeff: 0.618, text: "  TP1  " }] })], project, OPTIONS);
    expect(scene.labels.find((l) => l.level === 0.618)!.text).toBe("TP1  161.80");
  });

  it("空别名回落到默认比例标签", () => {
    const scene = buildDrawingScene([grid({ levelStyles: [{ coeff: 0.618, text: "   " }] })], project, OPTIONS);
    expect(scene.labels.find((l) => l.level === 0.618)!.text).toBe("0.618  161.80");
  });

  it("覆盖别的档位不影响本档", () => {
    const scene = buildDrawingScene([grid({ levelStyles: [{ coeff: 0.786, color: "#00ff00" }] })], project, OPTIONS);
    expect(scene.lines.find((l) => l.level === 0.5)!.color).toBe("#e3b650");
  });
});

import { describe, expect, it } from "vitest";

import { DIMMED_ALPHA, SELECTED_RING_COLOR, paintScene, resizeCanvas, type Ctx2D } from "@/lib/chart/drawing-canvas";
import type { DrawingScene } from "@/lib/chart/drawing-scene";

interface Recorder extends Ctx2D {
  calls: string[];
  ops: { op: string; args: unknown[]; lineWidth: number; strokeStyle: string; fillStyle: string; dash: number[]; alpha: number; font: string }[];
  getLineWidth(): number;
  dashes(): number[];
  /** 累计平移 —— 竖排标签画在 translate 之后的局部坐标里,断言要还原到画布坐标。 */
  transformX(): number;
  transformY(): number;
  rotation(): number;
}

/** 记录型假 ctx:每次绘制调用都连同当时的画笔状态一起记下来。 */
function makeCtx(): Recorder {
  const calls: string[] = [];
  const ops: Recorder["ops"] = [];
  let lineWidth = 1;
  let strokeStyle = "#000";
  let fillStyle = "#000";
  let dash: number[] = [];
  let alpha = 1;
  let font = "10px sans-serif";
  let textAlign = "start";
  let textBaseline = "alphabetic";
  let transformX = 0;
  let transformY = 0;
  let rotation = 0;

  const record = (op: string, args: unknown[]) => {
    calls.push(op);
    ops.push({ op, args, lineWidth, strokeStyle, fillStyle, dash: [...dash], alpha, font });
  };

  const ctx = {
    calls,
    ops,
    transformX: () => transformX,
    transformY: () => transformY,
    rotation: () => rotation,
    save: () => record("save", []),
    restore: () => record("restore", []),
    translate: (x: number, y: number) => {
      transformX += x;
      transformY += y;
      record("translate", [x, y]);
    },
    rotate: (a: number) => {
      rotation += a;
      record("rotate", [a]);
    },
    clearRect: (...a: number[]) => record("clearRect", a),
    beginPath: () => record("beginPath", []),
    moveTo: (...a: number[]) => record("moveTo", a),
    lineTo: (...a: number[]) => record("lineTo", a),
    stroke: () => record("stroke", []),
    arc: (...a: number[]) => record("arc", a),
    fill: () => record("fill", []),
    fillRect: (...a: number[]) => record("fillRect", a),
    fillText: (text: string, x: number, y: number) => record("fillText", [text, x, y]),
    setLineDash: (d: number[]) => {
      dash = d;
      record("setLineDash", [d]);
    },
    measureText: (text: string) => ({ width: text.length * 6 }),
    set lineWidth(v: number) {
      lineWidth = v;
    },
    get lineWidth() {
      return lineWidth;
    },
    set strokeStyle(v: string) {
      strokeStyle = v;
    },
    get strokeStyle() {
      return strokeStyle;
    },
    set fillStyle(v: string) {
      fillStyle = v;
    },
    get fillStyle() {
      return fillStyle;
    },
    set font(v: string) {
      font = v;
    },
    get font() {
      return font;
    },
    set textAlign(v: string) {
      textAlign = v;
      record("textAlign", [v]);
    },
    get textAlign() {
      return textAlign;
    },
    set textBaseline(v: string) {
      textBaseline = v;
      record("textBaseline", [v]);
    },
    get textBaseline() {
      return textBaseline;
    },
    set globalAlpha(v: number) {
      alpha = v;
    },
    get globalAlpha() {
      return alpha;
    },
    getLineWidth: () => lineWidth,
    dashes: () => [...dash],
  } as unknown as Recorder;
  return ctx;
}

function scene(over: Partial<DrawingScene> = {}): DrawingScene {
  return {
    lines: [
      { gridId: "g1", level: 0.382, x0: 0, x1: 800, y: 462, color: "#8b93a7", dashed: true, size: 1, dimmed: false },
      { gridId: "g1", level: 0.5, x0: 0, x1: 800, y: 450, color: "#e3b650", dashed: false, size: 1.6, dimmed: false },
    ],
    vlines: [],
    segments: [],
    labels: [{ gridId: "g1", level: 0.5, x: 794, y: 447, text: "0.5  150.00", color: "#e3b650", dimmed: false }],
    anchors: [{ gridId: "g1", index: 0, x: 100, y: 500, color: "#e3b650", radius: 4, selected: false }],
    legs: [{ gridId: "g1", x0: 100, y0: 500, x1: 200, y1: 400, color: "#ffffff44", dimmed: false }],
    ...over,
  };
}

const OPTS = { width: 800, height: 600 };

describe("paintScene", () => {
  it("先清空整块画布(用 CSS 尺寸,不乘 dpr)", () => {
    const ctx = makeCtx();
    paintScene(ctx, scene(), OPTS);
    const clear = ctx.ops.find((o) => o.op === "clearRect")!;
    expect(clear.args).toEqual([0, 0, 800, 600]);
  });

  it("每条档位线一次 stroke,坐标横贯全宽", () => {
    const ctx = makeCtx();
    paintScene(ctx, scene(), OPTS);
    const strokes = ctx.ops.filter((o) => o.op === "stroke");
    // 1 条腿 + 2 条档位线 + 1 个锚点把手
    expect(strokes).toHaveLength(4);
    const moves = ctx.ops.filter((o) => o.op === "moveTo").map((o) => o.args);
    expect(moves).toContainEqual([0, 462]);
    expect(moves).toContainEqual([0, 450]);
  });

  it("虚线档位设置 dash,实线档位清空 dash", () => {
    const ctx = makeCtx();
    paintScene(ctx, scene(), OPTS);
    const dashedMoveIdx = ctx.ops.findIndex((o) => o.op === "moveTo" && o.args[1] === 462);
    const solidMoveIdx = ctx.ops.findIndex((o) => o.op === "moveTo" && o.args[1] === 450);
    const dashBefore = (idx: number) => {
      for (let i = idx; i >= 0; i--) if (ctx.ops[i].op === "setLineDash") return ctx.ops[i].args[0] as number[];
      return [];
    };
    expect(dashBefore(dashedMoveIdx)).toEqual([4, 4]);
    expect(dashBefore(solidMoveIdx)).toEqual([]);
  });

  it("每条线在 save/restore 之间,不泄漏画笔状态", () => {
    const ctx = makeCtx();
    paintScene(ctx, scene(), OPTS);
    const saves = ctx.calls.filter((c) => c === "save").length;
    const restores = ctx.calls.filter((c) => c === "restore").length;
    expect(restores).toBe(saves);
  });

  it("压暗网格以 DIMMED_ALPHA 绘制", () => {
    const ctx = makeCtx();
    paintScene(ctx, scene({ lines: [{ ...scene().lines[1], dimmed: true }], labels: [], legs: [], anchors: [] }), OPTS);
    const stroke = ctx.ops.find((o) => o.op === "stroke")!;
    expect(stroke.alpha).toBe(DIMMED_ALPHA);
  });

  it("选中网格 alpha 为 1", () => {
    const ctx = makeCtx();
    paintScene(ctx, scene({ legs: [], labels: [], anchors: [] }), OPTS);
    for (const s of ctx.ops.filter((o) => o.op === "stroke")) expect(s.alpha).toBe(1);
  });

  it("标签先铺底色再写字,右对齐", () => {
    const ctx = makeCtx();
    paintScene(ctx, scene(), OPTS);
    const idx = ctx.calls.indexOf("fillText");
    expect(idx).toBeGreaterThan(-1);
    expect(ctx.calls.slice(0, idx)).toContain("fillRect");
    expect(ctx.ops.some((o) => o.op === "textAlign" && o.args[0] === "right")).toBe(true);
  });

  it("锚点最后画,压在线的上方", () => {
    const ctx = makeCtx();
    paintScene(ctx, scene(), OPTS);
    const idx = ctx.calls.indexOf("arc");
    expect(idx).toBeGreaterThan(-1);
    const lastLineStroke = ctx.calls.lastIndexOf("stroke", idx);
    expect(lastLineStroke).toBeGreaterThan(-1);
  });

  it("showAnchors=false 时不画把手", () => {
    const ctx = makeCtx();
    paintScene(ctx, scene(), { ...OPTS, showAnchors: false });
    expect(ctx.calls).not.toContain("arc");
  });

  it("选中的锚点外圈多画一道金环,半径比把手大", () => {
    const ctx = makeCtx();
    const base = scene().anchors[0];
    paintScene(ctx, scene({ lines: [], labels: [], legs: [], anchors: [{ ...base, selected: true }] }), OPTS);
    const rings = ctx.ops.filter((o) => o.op === "stroke" && o.strokeStyle === SELECTED_RING_COLOR);
    expect(rings).toHaveLength(1);
    const arcs = ctx.ops.filter((o) => o.op === "arc").map((o) => o.args[2] as number);
    expect(Math.max(...arcs)).toBeGreaterThan(base.radius);
  });

  it("未选中的锚点画金环", () => {
    const ctx = makeCtx();
    paintScene(ctx, scene({ lines: [], labels: [], legs: [] }), OPTS);
    expect(ctx.ops.some((o) => o.op === "stroke" && o.strokeStyle === SELECTED_RING_COLOR)).toBe(false);
  });

  it("空场景只清屏,不发任何绘制指令", () => {
    const ctx = makeCtx();
    paintScene(ctx, { lines: [], vlines: [], segments: [], labels: [], anchors: [], legs: [] }, OPTS);
    expect(ctx.calls).toEqual(["save", "setLineDash", "clearRect", "restore"]);
  });

  it("自定义字号进 font 字符串", () => {
    const ctx = makeCtx();
    paintScene(ctx, scene(), { ...OPTS, fontSize: 12 });
    expect(ctx.ops.some((o) => o.op === "fillText" && o.font.startsWith("12px"))).toBe(true);
  });
});

describe("paintScene —— 时间斐波那契竖线", () => {
  const vscene = (over: Partial<DrawingScene> = {}) =>
    scene({
      lines: [],
      labels: [],
      anchors: [],
      legs: [],
      vlines: [
        { gridId: "t1", level: 2, x: 300, y0: 0, y1: 600, color: "#e3b650", dashed: false, size: 1.6, dimmed: false },
        { gridId: "t1", level: 3, x: 400, y0: 0, y1: 600, color: "#8b93a7", dashed: true, size: 1, dimmed: false },
      ],
      ...over,
    });

  it("每条竖线一次 stroke,从 y0 竖直画到 y1(x 不变)", () => {
    const ctx = makeCtx();
    paintScene(ctx, vscene(), OPTS);
    const moves = ctx.ops.filter((o) => o.op === "moveTo").map((o) => o.args);
    const lines = ctx.ops.filter((o) => o.op === "lineTo").map((o) => o.args);
    expect(moves).toEqual([
      [300, 0],
      [400, 0],
    ]);
    expect(lines).toEqual([
      [300, 600],
      [400, 600],
    ]);
  });

  it("竖线的颜色/线宽/虚线各走各的档位覆盖", () => {
    const ctx = makeCtx();
    paintScene(ctx, vscene(), OPTS);
    const strokes = ctx.ops.filter((o) => o.op === "stroke");
    expect(strokes[0]).toMatchObject({ strokeStyle: "#e3b650", lineWidth: 1.6, dash: [] });
    expect(strokes[1]).toMatchObject({ strokeStyle: "#8b93a7", lineWidth: 1, dash: [4, 4] });
  });

  it("压暗的时间网格按 DIMMED_ALPHA 画", () => {
    const ctx = makeCtx();
    paintScene(ctx, vscene({ vlines: [{ ...vscene().vlines[0], dimmed: true }] }), OPTS);
    expect(ctx.ops.filter((o) => o.op === "stroke")[0].alpha).toBe(DIMMED_ALPHA);
  });

  it("竖线画在水平线之后、锚点之前 —— 与绘制顺序注释一致", () => {
    const ctx = makeCtx();
    paintScene(
      ctx,
      vscene({
        lines: [{ gridId: "g1", level: 0.5, x0: 0, x1: 800, y: 450, color: "#e3b650", dashed: false, size: 1.6, dimmed: false }],
      }),
      OPTS,
    );
    const horizontal = ctx.ops.findIndex((o) => o.op === "moveTo" && o.args[0] === 0);
    const vertical = ctx.ops.findIndex((o) => o.op === "moveTo" && o.args[0] === 300);
    expect(horizontal).toBeGreaterThan(-1);
    expect(vertical).toBeGreaterThan(horizontal);
  });

  it("竖排标签:先平移到(x,y)再逆时针转 90°,文字在局部原点", () => {
    const ctx = makeCtx();
    paintScene(
      ctx,
      vscene({
        labels: [{ gridId: "t1", level: 2, x: 303, y: 10, text: "2x", color: "#e3b650", dimmed: false, align: "vertical" }],
      }),
      OPTS,
    );
    const i = ctx.calls.indexOf("rotate");
    expect(i).toBeGreaterThan(-1);
    expect(ctx.ops[i].args[0]).toBeCloseTo(-Math.PI / 2, 6);
    expect(ctx.transformX()).toBe(303);
    expect(ctx.transformY()).toBe(10);
    // 逐个 save/restore 配对,变换不泄漏到后续绘制
    expect(ctx.calls.filter((c) => c === "save").length).toBe(ctx.calls.filter((c) => c === "restore").length);
  });

  it("竖排标签先铺底色再写字", () => {
    const ctx = makeCtx();
    paintScene(
      ctx,
      vscene({
        labels: [{ gridId: "t1", level: 2, x: 303, y: 10, text: "2x", color: "#e3b650", dimmed: false, align: "vertical" }],
      }),
      OPTS,
    );
    const rect = ctx.calls.indexOf("fillRect");
    const text = ctx.calls.indexOf("fillText");
    expect(rect).toBeGreaterThan(-1);
    expect(text).toBeGreaterThan(rect);
  });

  it("横排标签不走 rotate —— 价格档位的老行为没被改掉", () => {
    const ctx = makeCtx();
    paintScene(ctx, scene({ vlines: [] }), OPTS);
    expect(ctx.calls).not.toContain("rotate");
    expect(ctx.calls).not.toContain("translate");
  });
});

describe("resizeCanvas", () => {
  function fakeCanvas() {
    return { width: 0, height: 0, style: { width: "", height: "" } };
  }

  it("按 dpr 放大位图,同时把 CSS 尺寸写回", () => {
    const c = fakeCanvas();
    resizeCanvas(c, 800, 600, 2);
    expect(c.width).toBe(1600);
    expect(c.height).toBe(1200);
    expect(c.style).toEqual({ width: "800px", height: "600px" });
  });

  it("尺寸没变时不重写 width/height(重写会清空位图导致闪烁)", () => {
    const c = fakeCanvas();
    resizeCanvas(c, 800, 600, 2);
    const w = c.width;
    const h = c.height;
    // 用 setter 记录是否被赋值
    let writes = 0;
    Object.defineProperty(c, "width", {
      get: () => w,
      set: () => {
        writes++;
      },
    });
    Object.defineProperty(c, "height", {
      get: () => h,
      set: () => {
        writes++;
      },
    });
    resizeCanvas(c, 800, 600, 2);
    expect(writes).toBe(0);
  });

  it("尺寸为 0 时钳到 1px,不产生非法位图", () => {
    const c = fakeCanvas();
    resizeCanvas(c, 0, 0, 1);
    expect(c.width).toBe(1);
    expect(c.height).toBe(1);
  });

  it("返回 CSS 尺寸而不是位图尺寸", () => {
    const c = fakeCanvas();
    expect(resizeCanvas(c, 800, 600, 2)).toEqual({ width: 800, height: 600, dpr: 2 });
  });
});

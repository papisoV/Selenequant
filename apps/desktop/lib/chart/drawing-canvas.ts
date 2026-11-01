// 场景 → canvas 调用 —— 唯一接触 CanvasRenderingContext2D 的地方。
//
// 只做"按场景逐条下发绘制指令",不做任何几何/数据推导(那些在 drawing-scene.ts)。
// 因此测试可以塞一个记录型假 ctx,断言"画了几条线、哪些虚线、颜色对不对",
// 不需要真 canvas、不需要浏览器。
//
// 高 DPI:canvas 的 CSS 尺寸与位图尺寸是两个数,渲染器按 dpr 放大位图并
// 预先 scale,这样线宽 1 在 Retina 上仍是 1 CSS px 而不是 0.5。

import type {
  DrawingScene,
  SceneLabel,
  SceneLine,
  SceneSegment,
  SceneVLine,
} from "@/lib/chart/drawing-scene";

/** 我们真正用到的那一小撮 ctx 能力 —— 便于注入假实现。 */
export interface Ctx2D {
  save(): void;
  restore(): void;
  translate(x: number, y: number): void;
  rotate(angle: number): void;
  clearRect(x: number, y: number, w: number, h: number): void;
  beginPath(): void;
  moveTo(x: number, y: number): void;
  lineTo(x: number, y: number): void;
  stroke(): void;
  arc(x: number, y: number, r: number, start: number, end: number): void;
  fill(): void;
  fillRect(x: number, y: number, w: number, h: number): void;
  fillText(text: string, x: number, y: number): void;
  setLineDash(segments: number[]): void;
  measureText(text: string): { width: number };
  set lineWidth(v: number);
  // 与 DOM 的宽类型对齐:画笔只写 string,但接口放宽才能把真 ctx 直接传进来。
  set strokeStyle(v: string | CanvasGradient | CanvasPattern);
  set fillStyle(v: string | CanvasGradient | CanvasPattern);
  set font(v: string);
  set textAlign(v: "left" | "right" | "center" | "start" | "end");
  set textBaseline(v: "top" | "middle" | "bottom" | "alphabetic" | "hanging" | "ideographic");
  set globalAlpha(v: number);
}

/** 压暗网格的不透明度。0.35 保留可读性又不抢主角。 */
export const DIMMED_ALPHA = 0.35;

/** 选中环的颜色 —— 用金色,与工具栏的 active 态同色系。 */
export const SELECTED_RING_COLOR = "#d4af37";

export interface PaintOptions {
  /** CSS px(不是位图 px)。 */
  width: number;
  height: number;
  /** 标签字号(px)。 */
  fontSize?: number;
  /** 标签背后的底色 —— 线从文字下面穿过会看不清。 */
  labelBackground?: string;
  /** 是否画锚点把手(导出/截图时可关)。 */
  showAnchors?: boolean;
}

function applyStyle(ctx: Ctx2D, line: SceneLine): void {
  ctx.strokeStyle = line.color;
  ctx.lineWidth = line.size;
  ctx.setLineDash(line.dashed ? [4, 4] : []);
}

function paintLine(ctx: Ctx2D, line: SceneLine): void {
  ctx.beginPath();
  ctx.moveTo(line.x0, line.y);
  ctx.lineTo(line.x1, line.y);
  ctx.stroke();
}

function paintVLine(ctx: Ctx2D, line: SceneVLine): void {
  ctx.strokeStyle = line.color;
  ctx.lineWidth = line.size;
  ctx.setLineDash(line.dashed ? [4, 4] : []);
  ctx.beginPath();
  ctx.moveTo(line.x, line.y0);
  ctx.lineTo(line.x, line.y1);
  ctx.stroke();
}

function paintSegment(ctx: Ctx2D, seg: SceneSegment): void {
  ctx.strokeStyle = seg.color;
  ctx.lineWidth = seg.size;
  ctx.setLineDash(seg.dashed ? [4, 4] : []);
  ctx.beginPath();
  ctx.moveTo(seg.x0, seg.y0);
  ctx.lineTo(seg.x1, seg.y1);
  ctx.stroke();
}

/**
 * 时间档位标签:竖排贴在竖线顶端。
 *
 * 为什么不横排:相邻两条时间线在腿短时只有几十像素,横排文字必然互相压住。
 * 竖排后占用宽度就是一个字高,密度再高也不会叠。
 */
function paintVerticalLabel(ctx: Ctx2D, label: SceneLabel, fontSize: number, background: string): void {
  ctx.save();
  ctx.translate(label.x, label.y);
  ctx.rotate(-Math.PI / 2);
  ctx.font = `${fontSize}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.textAlign = "left";
  ctx.textBaseline = "bottom";

  const w = ctx.measureText(label.text).width;
  const padX = 4;
  const padY = 2;
  const boxH = fontSize + padY * 2;
  ctx.fillStyle = background;
  ctx.fillRect(-padX, -boxH + padY, w + padX * 2, boxH);
  ctx.fillStyle = label.color;
  ctx.fillText(label.text, 0, 0);
  ctx.restore();
}

function paintLabel(ctx: Ctx2D, label: SceneLabel, fontSize: number, background: string): void {
  if (label.align === "vertical") {
    paintVerticalLabel(ctx, label, fontSize, background);
    return;
  }
  ctx.font = `${fontSize}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.textAlign = "right";
  ctx.textBaseline = "bottom";

  const w = ctx.measureText(label.text).width;
  const padX = 4;
  const padY = 2;
  const boxH = fontSize + padY * 2;
  ctx.fillStyle = background;
  ctx.fillRect(label.x - w - padX, label.y - boxH + padY, w + padX * 2, boxH);

  ctx.fillStyle = label.color;
  ctx.fillText(label.text, label.x - padX, label.y);
}

function paintAnchor(
  ctx: Ctx2D,
  x: number,
  y: number,
  radius: number,
  color: string,
  selected = false,
): void {
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fillStyle = "#0b0d10";
  ctx.fill();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.setLineDash([]);
  ctx.stroke();

  // 多选时必须能一眼看出"这条也在选中集合里"。第二圈画在半径之外,
  // 不改变把手本身的视觉尺寸,也就不会影响拖动时的命中半径。
  if (selected) {
    ctx.beginPath();
    ctx.arc(x, y, radius + 2.5, 0, Math.PI * 2);
    ctx.strokeStyle = SELECTED_RING_COLOR;
    ctx.lineWidth = 1;
    ctx.stroke();
  }
}

/**
 * 把整帧场景画到 canvas。
 *
 * 坐标一律是 **CSS px**:调用方先对 ctx 做 setTransform(dpr,0,0,dpr,0,0),
 * 位图放大与坐标缩放就都解耦了。width/height 传 CSS 尺寸,不要传位图尺寸。
 *
 * 顺序有意义:腿 → 水平线 → 竖线 → 标签 → 锚点。锚点必须最后画,压在线上才抓得住。
 */
export function paintScene(ctx: Ctx2D, scene: DrawingScene, options: PaintOptions): void {
  const { width, height } = options;
  const fontSize = options.fontSize ?? 10;
  const background = options.labelBackground ?? "rgba(11,13,16,0.82)";
  const showAnchors = options.showAnchors ?? true;

  ctx.save();
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
  ctx.clearRect(0, 0, width, height);
  ctx.restore();

  // 腿(两个锚点之间的连线):先画,永远在档位线之下
  for (const leg of scene.legs) {
    ctx.save();
    ctx.globalAlpha = leg.dimmed ? DIMMED_ALPHA : 1;
    ctx.strokeStyle = leg.color;
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 3]);
    ctx.beginPath();
    ctx.moveTo(leg.x0, leg.y0);
    ctx.lineTo(leg.x1, leg.y1);
    ctx.stroke();
    ctx.restore();
  }

  for (const line of scene.lines) {
    ctx.save();
    ctx.globalAlpha = line.dimmed ? DIMMED_ALPHA : 1;
    applyStyle(ctx, line);
    paintLine(ctx, line);
    ctx.restore();
  }

  for (const vline of scene.vlines) {
    ctx.save();
    ctx.globalAlpha = vline.dimmed ? DIMMED_ALPHA : 1;
    paintVLine(ctx, vline);
    ctx.restore();
  }

  for (const seg of scene.segments) {
    ctx.save();
    ctx.globalAlpha = seg.dimmed ? DIMMED_ALPHA : 1;
    paintSegment(ctx, seg);
    ctx.restore();
  }

  for (const label of scene.labels) {
    ctx.save();
    ctx.globalAlpha = label.dimmed ? DIMMED_ALPHA : 1;
    paintLabel(ctx, label, fontSize, background);
    ctx.restore();
  }

  if (showAnchors) {
    for (const a of scene.anchors) {
      ctx.save();
      paintAnchor(ctx, a.x, a.y, a.radius, a.color, a.selected);
      ctx.restore();
    }
  }
}

/**
 * 同步 canvas 位图尺寸与 CSS 尺寸。
 *
 * 只在尺寸真变时才写 width/height —— 给 canvas.width 赋相同值也会清空位图,
 * 每帧都写会让画面闪。
 */
export function resizeCanvas(
  canvas: { width: number; height: number; style: { width: string; height: string } },
  cssWidth: number,
  cssHeight: number,
  dpr: number,
): { width: number; height: number; dpr: number } {
  const w = Math.max(1, Math.round(cssWidth * dpr));
  const h = Math.max(1, Math.round(cssHeight * dpr));
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;
  canvas.style.width = `${cssWidth}px`;
  canvas.style.height = `${cssHeight}px`;
  return { width: cssWidth, height: cssHeight, dpr };
}

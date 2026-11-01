// 绘图对象 → 屏幕图元 —— 纯函数(零 React / 零 canvas / 零图表库)。
//
// 这一层是"画什么",不是"怎么画":它只吐坐标与样式,不碰 CanvasRenderingContext2D。
// 好处是同一份场景既能喂给 canvas,也能喂给命中检测、也能在 jsdom 里断言,
// 不会出现"画出来是 A、点中的是 B"。
//
// 坐标来源:调用方提供一个 `ViewProjector`(见 drawing-projection.ts 的契约),
// 它把 {时间, 价格} 换成像素。本层对时间怎么映射到 x 完全不知情。

import { formatLevelLabel } from "@/lib/chart/fib-levels";
import {
  ANCHOR_HANDLE_RADIUS,
  FIB_SECONDARY_COLOR,
  LINE_HIT_TOLERANCE,
  styleForLevel,
} from "@/lib/chart/fib-style";
import { type FibGrid, hasLevels, levelOverride, priceAtLevel } from "@/lib/chart/fib-object";
import { timeOffsetX } from "@/lib/chart/fib-time-levels";

/** 场景里每条线/每个标签都属于某条网格的某个档位。 */
/** 档位标签文字。用户给这一档写了别名("TP1")就用别名,否则用「比例  价格」。 */
function formatLabelText(
  level: number,
  price: number,
  precision: number,
  alias: string | undefined,
  suffix?: string,
): string {
  const name = alias?.trim();
  if (suffix) {
    // 时间档位没有价格可显示,数字本身就是倍数(如 "3x")。
    return name ? name : `${formatLevelLabel(level)}${suffix}`;
  }
  const label = name ? name : formatLevelLabel(level);
  return `${label}  ${price.toFixed(precision)}`;
}

export interface SceneLine {
  gridId: string;
  level: number;
  /** 线左端 x(通常是 0)。 */
  x0: number;
  /** 线右端 x(通常是绘图区宽度)。 */
  x1: number;
  y: number;
  color: string;
  dashed: boolean;
  size: number;
  dimmed: boolean;
}

export interface SceneLabel {
  gridId: string;
  level: number;
  x: number;
  y: number;
  text: string;
  color: string;
  dimmed: boolean;
  /**
   * 标签的锚点在哪一侧。水平档位的文字右对齐贴线段右端;
   * 时间档位的文字横排不下相邻两条竖线,改为竖直方向排在竖线顶端。
   */
  align?: "right" | "vertical";
}

export interface SceneAnchor {
  gridId: string;
  /** 0 = start,1 = end。拖拽时按这个索引回写。 */
  index: 0 | 1;
  x: number;
  y: number;
  color: string;
  radius: number;
  /** 该对象是否在选中集合里。多选时画"已选中"的描边。 */
  selected: boolean;
}

export interface SceneLeg {
  gridId: string;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  color: string;
  dimmed: boolean;
}

/**
 * 任意方向的一条线段(目前只有趋势线用)。
 *
 * 水平线有 `SceneLine`、竖线有 `SceneVLine` —— 它们各自带一个轴上的常量,
 * 所以能省掉一个坐标;趋势线两个坐标都在变,只能老老实实存四个数。
 */
export interface SceneSegment {
  gridId: string;
  /** 命中回写用。基础图形没有档位,恒为 0。 */
  level: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  color: string;
  dashed: boolean;
  size: number;
  dimmed: boolean;
}

export interface SceneVLine {
  gridId: string;
  /** 时间倍数(不是价格比例)。同一网格内用于命中回写。 */
  level: number;
  x: number;
  /** 竖线上下端(通常 0 与绘图区高度)。 */
  y0: number;
  y1: number;
  color: string;
  dashed: boolean;
  size: number;
  dimmed: boolean;
}

export interface DrawingScene {
  lines: SceneLine[];
  /** 时间斐波那契的竖线。与 `lines` 分开存,避免给水平线的字段塞一个用不上的轴。 */
  vlines: SceneVLine[];
  /** 任意方向的线段(趋势线)。矩形在构造期拆成四条水平/竖直线。 */
  segments: SceneSegment[];
  labels: SceneLabel[];
  anchors: SceneAnchor[];
  legs: SceneLeg[];
}

export interface SceneOptions {
  /** 价格由锚点现算,这里只决定小数位。 */
  precision: number;
  /**
   * 选中集合。空集合 = 无选中(所有对象同等亮度)。
   *
   * 多选时的显示规则沿用单选的直觉:**没有选中的对象才压暗**,
   * 选中的对象都保持原亮度 —— 逐个给不同亮度会让用户以为"只有最亮那条算数"。
   */
  selectedIds?: ReadonlySet<string>;
  /** 视图宽高(px),线要横贯可视区。 */
  width: number;
  /** 视图高度。时间斐波那契的竖线要上下贯通;缺省为 0(竖线不带纵向范围)。 */
  height?: number;
  showLabels?: boolean;
  /** 标签右边距,避免压住价格轴。 */
  labelRightPad?: number;
}

/** 压暗系数只影响颜色透明度,由渲染器施加;这里只标记"该压暗"。 */
function isDimmed(grid: FibGrid, selectedIds: ReadonlySet<string> | undefined): boolean {
  return selectedIds !== undefined && selectedIds.size > 0 && !selectedIds.has(grid.id);
}

/**
 * 生成一帧场景。
 *
 * 退化网格(start/end 价格相等)仍会画出一条线 —— 它是用户画出来的东西,
 * 不该凭空消失;但两个锚点重合时不做任何特殊处理,由调用方决定是否提示。
 */
export function buildDrawingScene(
  grids: readonly FibGrid[],
  project: { xOfTime(t: number): number | null; yOfPrice(p: number): number | null },
  options: SceneOptions,
): DrawingScene {
  const { precision, width, selectedIds } = options;
  const showLabels = options.showLabels ?? true;
  const labelRightPad = options.labelRightPad ?? 6;

  const lines: SceneLine[] = [];
  const vlines: SceneVLine[] = [];
  const segments: SceneSegment[] = [];
  const labels: SceneLabel[] = [];
  const anchors: SceneAnchor[] = [];
  const legs: SceneLeg[] = [];

  for (const grid of grids) {
    if (!grid.visible) continue;

    const dimmed = isDimmed(grid, selectedIds);
    const selected = selectedIds?.has(grid.id) ?? false;

    const x0 = project.xOfTime(grid.start.time);
    const y0 = project.yOfPrice(grid.start.price);
    const x1 = project.xOfTime(grid.end.time);
    const y1 = project.yOfPrice(grid.end.price);

    // 锚点换算失败(图表尚无数据)时跳过整条网格,而不是画到 (0,0)。
    if (x0 === null || y0 === null || x1 === null || y1 === null) continue;
    if (!Number.isFinite(x0) || !Number.isFinite(y0) || !Number.isFinite(x1) || !Number.isFinite(y1)) continue;

    const anchorColor = dimmed ? `${styleForLevel(0.5).color}66` : styleForLevel(0.5).color;

    // 单点工具(水平线 / 竖线)只有一个把手:第二个锚点没有几何意义,
    // 画出来只会让用户以为能把它拖开。start 在落笔时被复制成同一点,
    // 所以保留 index=1 即可,回写走 moveAnchor 的 end 分支。
    const singlePoint = grid.mode === "horizontal" || grid.mode === "vertical";
    if (!singlePoint) {
      anchors.push({ gridId: grid.id, index: 0, x: x0, y: y0, color: anchorColor, radius: ANCHOR_HANDLE_RADIUS, selected });
    }
    anchors.push({ gridId: grid.id, index: 1, x: x1, y: y1, color: anchorColor, radius: ANCHOR_HANDLE_RADIUS, selected });

    // 腿:两个锚点之间的连线。颜色比档位线淡,只用来指出"这组线是哪一段定的"。
    // 只有斐波那契家族有这个概念;趋势线自己就是那条线,矩形是四条边,
    // 单点工具两点重合 —— 都不该再叠一条虚腿。
    if (grid.mode === "price" || grid.mode === "time") {
      legs.push({ gridId: grid.id, x0, y0, x1, y1, color: dimmed ? "#ffffff22" : "#ffffff44", dimmed });
    }

    // 时间斐波那契:档位是"腿时长的倍数",画成竖线。几何全在像素空间里算,
    // 与周期无关;腿反向画时由 timeOffsetX 用 min/max 归一。
    if (grid.mode === "time") {
      for (const multiple of grid.levels) {
        const vx = timeOffsetX(x0, x1, multiple);
        if (!Number.isFinite(vx)) continue;
        const override = levelOverride(grid, multiple);
        const style = styleForLevel(multiple, override);
        vlines.push({
          gridId: grid.id,
          level: multiple,
          x: vx,
          y0: 0,
          y1: options.height ?? 0,
          color: style.color,
          dashed: style.dashed,
          size: style.size,
          dimmed,
        });

        if (showLabels) {
          labels.push({
            gridId: grid.id,
            level: multiple,
            x: vx + 3,
            y: 10,
            text: formatLabelText(multiple, 0, 0, override?.text, "x"),
            color: style.color,
            dimmed,
            align: "vertical",
          });
        }
      }
      continue;
    }

    // 趋势线:两个锚点直接连一条实线。它自己就是几何,不需要档位。
    if (grid.mode === "trend") {
      segments.push({
        gridId: grid.id,
        level: 0,
        x0,
        y0,
        x1,
        y1,
        color: FIB_SECONDARY_COLOR,
        dashed: false,
        size: 1.5,
        dimmed,
      });
      continue;
    }

    // 水平线:通栏,价位取 end(单点工具的权威点),标签贴右缘显示价格。
    if (grid.mode === "horizontal") {
      lines.push({
        gridId: grid.id,
        level: 0,
        x0: 0,
        x1: width,
        y: y1,
        color: FIB_SECONDARY_COLOR,
        dashed: false,
        size: 1.5,
        dimmed,
      });
      if (showLabels) {
        labels.push({
          gridId: grid.id,
          level: 0,
          x: width - labelRightPad,
          y: y1 - 3,
          text: grid.end.price.toFixed(precision),
          color: FIB_SECONDARY_COLOR,
          dimmed,
        });
      }
      continue;
    }

    // 竖线:通栏,时间取 end(同上)。
    if (grid.mode === "vertical") {
      vlines.push({
        gridId: grid.id,
        level: 0,
        x: x1,
        y0: 0,
        y1: options.height ?? 0,
        color: FIB_SECONDARY_COLOR,
        dashed: false,
        size: 1.5,
        dimmed,
      });
      continue;
    }

    // 矩形:两条水平边进 `lines`、两条竖直边进 `vlines`,命中检测无需新增分支。
    if (grid.mode === "rect") {
      const left = Math.min(x0, x1);
      const right = Math.max(x0, x1);
      const top = Math.min(y0, y1);
      const bottom = Math.max(y0, y1);
      for (const y of [top, bottom]) {
        lines.push({
          gridId: grid.id,
          level: 0,
          x0: left,
          x1: right,
          y,
          color: FIB_SECONDARY_COLOR,
          dashed: false,
          size: 1.5,
          dimmed,
        });
      }
      for (const x of [left, right]) {
        vlines.push({
          gridId: grid.id,
          level: 0,
          x,
          y0: top,
          y1: bottom,
          color: FIB_SECONDARY_COLOR,
          dashed: false,
          size: 1.5,
          dimmed,
        });
      }
      continue;
    }

    // 档位线的横向范围 = 两个锚点之间(线段),除非显式要求向某侧延伸。
    // TV 的默认几何就是线段,extendLeft/Right 是 opt-in —— 不是通栏。
    // 命中检测(hitTestScene)用的是同一份 x0/x1,所以绘制与拾取不会各算一套。
    const lineLeft = grid.extendLeft ? 0 : Math.min(x0, x1);
    const lineRight = grid.extendRight ? width : Math.max(x0, x1);

    for (const level of hasLevels(grid.mode) ? grid.levels : []) {
      const price = priceAtLevel(grid.start.price, grid.end.price, level);
      const y = project.yOfPrice(price);
      if (y === null || !Number.isFinite(y)) continue;

      const override = levelOverride(grid, level);
      const style = styleForLevel(level, override);
      lines.push({
        gridId: grid.id,
        level,
        x0: lineLeft,
        x1: lineRight,
        y,
        color: style.color,
        dashed: style.dashed,
        size: style.size,
        dimmed,
      });

      if (showLabels) {
        // 标签贴线段右端(右对齐),而不是画布右缘 —— width 含价格轴,
        // 贴 width 会把档位数字压到价格刻度上。线段右端出画布时夹回可视区。
        labels.push({
          gridId: grid.id,
          level,
          x: Math.min(lineRight, width) - labelRightPad,
          y: y - 3,
          text: formatLabelText(level, price, precision, override?.text),
          color: style.color,
          dimmed,
        });
      }
    }
  }

  return { lines, vlines, segments, labels, anchors, legs };
}

/** 点到线段的距离 —— 趋势线是斜的,不能再只看纵向差。 */
function distanceToSegment(px: number, py: number, seg: { x0: number; y0: number; x1: number; y1: number }): number {
  const dx = seg.x1 - seg.x0;
  const dy = seg.y1 - seg.y0;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return Math.hypot(px - seg.x0, py - seg.y0);
  // 投影参数夹回 [0,1] = 落在端点外侧时按端点算。
  const t = Math.max(0, Math.min(1, ((px - seg.x0) * dx + (py - seg.y0) * dy) / lenSq));
  return Math.hypot(px - (seg.x0 + t * dx), py - (seg.y0 + t * dy));
}

/** 命中结果。`distance` 用于同一位置多个候选时挑最近的。 */
export type Hit =
  | { kind: "anchor"; gridId: string; index: 0 | 1; distance: number }
  | { kind: "label"; gridId: string; level: number; distance: number }
  | { kind: "line"; gridId: string; level: number; distance: number }
  | { kind: "vline"; gridId: string; level: number; distance: number }
  | { kind: "segment"; gridId: string; level: number; distance: number };

/**
 * 命中检测。优先级:锚点 > 标签 > 线。
 *
 * 为什么锚点优先:它是唯一的拖拽把手,而线的命中带 4px 容差,两者重叠时
 * 若线赢,用户就永远抓不住锚点。
 */
export function hitTestScene(scene: DrawingScene, x: number, y: number): Hit | null {
  let best: Hit | null = null;

  const consider = (hit: Hit, radius: number) => {
    if (hit.distance > radius) return;
    if (best === null || hit.distance < best.distance) best = hit;
  };

  for (const a of scene.anchors) {
    const d = Math.hypot(x - a.x, y - a.y);
    consider({ kind: "anchor", gridId: a.gridId, index: a.index, distance: d }, a.radius + 3);
  }
  if (best) return best;

  for (const l of scene.lines) {
    // 水平线:只看纵向距离,但要求横向落在线的跨度内
    if (x < Math.min(l.x0, l.x1) || x > Math.max(l.x0, l.x1)) continue;
    const d = Math.abs(y - l.y);
    consider({ kind: "line", gridId: l.gridId, level: l.level, distance: d }, LINE_HIT_TOLERANCE);
  }
  if (best) return best;

  for (const v of scene.vlines) {
    // 竖线:只看横向距离,但要求纵向落在竖线的跨度内
    if (y < Math.min(v.y0, v.y1) || y > Math.max(v.y0, v.y1)) continue;
    const d = Math.abs(x - v.x);
    consider({ kind: "vline", gridId: v.gridId, level: v.level, distance: d }, LINE_HIT_TOLERANCE);
  }
  if (best) return best;

  for (const s of scene.segments) {
    const d = distanceToSegment(x, y, s);
    consider({ kind: "segment", gridId: s.gridId, level: s.level, distance: d }, LINE_HIT_TOLERANCE);
  }
  if (best) return best;

  // 标签只做"点击选中整条网格"的入口,不做精细命中;按一个矩形估计。
  for (const lb of scene.labels) {
    const halfW = lb.text.length * 3.2;
    if (x < lb.x - halfW || x > lb.x) continue;
    if (y > lb.y || y < lb.y - 12) continue;
    const d = Math.abs(y - lb.y);
    consider({ kind: "label", gridId: lb.gridId, level: lb.level, distance: d }, 12);
  }

  return best;
}

/** 两个锚点的 y 差值决定腿的涨跌;档位标签要用它判断方向(可选功能)。 */
export function legSpan(scene: DrawingScene, gridId: string): { dy: number; dx: number } | null {
  const leg = scene.legs.find((l) => l.gridId === gridId);
  if (!leg) return null;
  return { dy: leg.y1 - leg.y0, dx: leg.x1 - leg.x0 };
}

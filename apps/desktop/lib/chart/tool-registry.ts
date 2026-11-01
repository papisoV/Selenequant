// 绘图工具注册表 —— 纯数据(零 React / 零图表库)。
//
// 为什么需要它:在加"时间斐波那契"之前,新工具要同时改 store 的联合类型、
// 图层的 if 分支、工具栏的按钮 —— 三处各自写死 "fib",加第三个工具就是
// 复制第四、五、六处。这张表把"有哪些工具、各要几个点、落在哪个几何"收敛成数据。
//
// 本表只描述**交互形态**;几何怎么算在 drawing-scene / fib-object 里,
// 两层不互相引用。

import type { FibGridMode } from "@/lib/chart/fib-object";

export type DrawingToolKind =
  | "fib"
  | "fibTime"
  | "trend"
  | "horizontal"
  | "vertical"
  | "rect";

/** 工具栏下拉里的分组。基础几何不是斐波那契,混在一个列表里会让用户以为都能调档位。 */
export type ToolGroup = "fib" | "basic";

export const TOOL_GROUP_LABELS: Readonly<Record<ToolGroup, string>> = {
  fib: "斐波那契",
  basic: "基础图形",
};

export interface ToolSpec {
  /** 工具标识,同时是 store 里 `activeTool` 的取值域。 */
  kind: DrawingToolKind;
  /** 建出来的对象用哪种几何。 */
  mode: FibGridMode;
  /** i18n 源串(中文),按钮文案与 title 都从这里取。 */
  label: string;
  /** 按钮 title 的 i18n 源串。 */
  hint: string;
  /**
   * 落笔要几个点。
   * - `1`:单击即建(水平线 / 竖线),拖拽也接受 —— 松手位置就是那个点;
   * - `2`:必须拖出一条腿(斐波那契 / 趋势线 / 矩形)。
   */
  pointCount: 1 | 2;
  /** 下拉里的分组。 */
  group: ToolGroup;
}

export const TOOLS: Readonly<Record<DrawingToolKind, ToolSpec>> = {
  fib: {
    kind: "fib",
    mode: "price",
    label: "斐波那契",
    hint: "斐波那契网格：选中后在图上拖出一条腿（起点→终点）",
    pointCount: 2,
    group: "fib",
  },
  fibTime: {
    kind: "fibTime",
    mode: "time",
    label: "时间斐波那契",
    hint: "时间斐波那契：拖出一条腿，按 1/2/3/5/8… 倍腿长画出竖线",
    pointCount: 2,
    group: "fib",
  },
  trend: {
    kind: "trend",
    mode: "trend",
    label: "趋势线",
    hint: "趋势线：拖出一条线段连接两点",
    pointCount: 2,
    group: "basic",
  },
  horizontal: {
    kind: "horizontal",
    mode: "horizontal",
    label: "水平线",
    hint: "水平线：在某个价位单击，线横贯整屏（拖动可继续调整价位）",
    pointCount: 1,
    group: "basic",
  },
  vertical: {
    kind: "vertical",
    mode: "vertical",
    label: "竖线",
    hint: "竖线：在某个时间点单击，线自上而下贯通",
    pointCount: 1,
    group: "basic",
  },
  rect: {
    kind: "rect",
    mode: "rect",
    label: "矩形",
    hint: "矩形：拖出对角两点圈定一个区域",
    pointCount: 2,
    group: "basic",
  },
};

/** 工具栏里的展示顺序。 */
export const TOOL_ORDER: readonly DrawingToolKind[] = [
  "fib",
  "fibTime",
  "trend",
  "horizontal",
  "vertical",
  "rect",
];

/** 按分组切出顺序表(下拉渲染用)。 */
export function toolsInGroup(group: ToolGroup): readonly ToolSpec[] {
  return TOOL_ORDER.map((k) => TOOLS[k]).filter((spec) => spec.group === group);
}

/** 该工具是否处于"下一次拖拽会建对象"的状态。 */
export function isDrawingTool(tool: string): tool is DrawingToolKind {
  return Object.prototype.hasOwnProperty.call(TOOLS, tool);
}

/**
 * 某个几何模式对应的工具名。对象树的标题、下拉的选中态都从这里取 ——
 * 模式与工具是一对一的,但表的方向是工具 → 模式,反查一次比再维护一张表安全。
 */
export function toolLabelForMode(mode: FibGridMode): string {
  for (const spec of Object.values(TOOLS)) {
    if (spec.mode === mode) return spec.label;
  }
  return "绘图对象";
}

// 绘图工具的几何图标 —— 内联 SVG,不依赖图标库。
//
// 为什么手写而不是拿 lucide 的通用图标:TV 的绘图按钮是**工具几何本身的缩写**
// (斐波那契 = 几条水平比例线,趋势线 = 两端带把手的斜线,矩形 = 带角点的框),
// 通用图标库里没有一个对得上。参考实现(lightweight-charts-drawing)用同样思路:
// 28×28 viewBox、1.2px 描边、currentColor,按钮尺寸再缩放。
//
// 约定:
//   - 所有图标共用同一 viewBox,像素对齐;线宽不随尺寸变粗,靠 CSS 缩放。
//   - 只用 currentColor:激活态由按钮的 color 决定,图标本身不认识主题。
//   - 图形线不代表真实档位比例,只求一眼可辨(0.236/0.382/0.5 那种密度)。

import type { DrawingToolKind } from "@/lib/chart/tool-registry";

const SVG_PROPS = {
  viewBox: "0 0 28 28",
  width: 16,
  height: 16,
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.4,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
  focusable: false,
} as const;

/** 锚点小圆 —— TV 的每条线/每根腿两端都有,这是整个绘图层最认得出的特征。 */
function Anchor({ cx, cy }: { cx: number; cy: number }) {
  return <circle cx={cx} cy={cy} r={2} fill="currentColor" stroke="none" />;
}

export function SelectIcon() {
  return (
    <svg {...SVG_PROPS}>
      {/* 箭头光标:实心头部 + 细杆,比空心轮廓在 16px 下更清楚 */}
      <path d="M8 5 L19 14 L13.6 15.2 L16.4 21 L13.6 22.3 L10.8 16.5 L8 19 Z" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function FibIcon() {
  return (
    <svg {...SVG_PROPS}>
      <path d="M6 5 L19 22" strokeDasharray="2 2" opacity={0.55} />
      <path d="M6 5 H22" />
      <path d="M6 9.5 H22" opacity={0.9} />
      <path d="M6 14 H22" />
      <path d="M6 18.5 H22" opacity={0.9} />
      <path d="M6 23 H22" />
      <Anchor cx={6} cy={5} />
      <Anchor cx={6} cy={23} />
    </svg>
  );
}

export function FibTimeIcon() {
  return (
    <svg {...SVG_PROPS}>
      <path d="M6 5 V23" />
      <path d="M11 5 V23" opacity={0.9} />
      <path d="M16 5 V23" />
      <path d="M21 5 V23" opacity={0.9} />
      <path d="M6 14 H21" strokeDasharray="2 2" opacity={0.55} />
      <Anchor cx={6} cy={14} />
      <Anchor cx={21} cy={14} />
    </svg>
  );
}

export function TrendIcon() {
  return (
    <svg {...SVG_PROPS}>
      <path d="M6 21 L22 7" />
      <Anchor cx={6} cy={21} />
      <Anchor cx={22} cy={7} />
    </svg>
  );
}

export function HorizontalIcon() {
  return (
    <svg {...SVG_PROPS}>
      <path d="M4 14 H24" />
      <Anchor cx={14} cy={14} />
    </svg>
  );
}

export function VerticalIcon() {
  return (
    <svg {...SVG_PROPS}>
      <path d="M14 4 V24" />
      <Anchor cx={14} cy={14} />
    </svg>
  );
}

export function RectIcon() {
  return (
    <svg {...SVG_PROPS}>
      <rect x={6} y={8} width={16} height={12} rx={1} />
      <Anchor cx={6} cy={8} />
      <Anchor cx={22} cy={20} />
    </svg>
  );
}

const TOOL_ICONS: Readonly<Record<DrawingToolKind, () => React.JSX.Element>> = {
  fib: FibIcon,
  fibTime: FibTimeIcon,
  trend: TrendIcon,
  horizontal: HorizontalIcon,
  vertical: VerticalIcon,
  rect: RectIcon,
};

/** 工具 → 图标。加工具时只改这张表 + tool-registry,别在按钮里写 if。 */
export function ToolIcon({ kind }: { kind: DrawingToolKind }) {
  const Icon = TOOL_ICONS[kind];
  return <Icon />;
}

/** 对象树入口:三层堆叠,比文字「对象 · N」省一大截宽度。 */
export function ObjectsIcon() {
  return (
    <svg {...SVG_PROPS}>
      <path d="M6 9 H22" />
      <path d="M6 14 H22" opacity={0.85} />
      <path d="M6 19 H22" opacity={0.7} />
      <circle cx={4} cy={9} r={1} fill="currentColor" stroke="none" />
      <circle cx={4} cy={14} r={1} fill="currentColor" stroke="none" />
      <circle cx={4} cy={19} r={1} fill="currentColor" stroke="none" />
    </svg>
  );
}

/** 绘图设置入口:滑杆,和「选工具」在语义上区分开(TV 也是齿轮/滑杆)。 */
export function SettingsIcon() {
  return (
    <svg {...SVG_PROPS}>
      <path d="M4 9 H16" />
      <path d="M20 9 H24" />
      <circle cx={18} cy={9} r={2} />
      <path d="M4 19 H10" />
      <path d="M14 19 H24" />
      <circle cx={12} cy={19} r={2} />
    </svg>
  );
}

/** 图表外观入口:调色板。 */
export function PaletteIcon() {
  return (
    <svg {...SVG_PROPS}>
      <path d="M14 4.5a9.5 9.5 0 1 0 0 19c1.6 0 2.4-1 2.4-2.1 0-1.2-.9-1.7-.9-2.7 0-1 .8-1.7 1.9-1.7H19a4.9 4.9 0 0 0 4.9-4.9c0-4.2-4.4-7.6-9.9-7.6Z" />
      <circle cx={9.5} cy={10} r={1.4} fill="currentColor" stroke="none" />
      <circle cx={14} cy={7.8} r={1.4} fill="currentColor" stroke="none" />
      <circle cx={18.2} cy={10.4} r={1.4} fill="currentColor" stroke="none" />
    </svg>
  );
}

/** 删除选中 —— 只在有选中时出现在工具栏尾部,给 Delete 键一个可见的解释。 */
export function DeleteIcon() {
  return (
    <svg {...SVG_PROPS}>
      <path d="M6 8 H22" />
      <path d="M11 8 V6.5c0-.6.4-1 1-1h4c.6 0 1 .4 1 1V8" />
      <path d="M8 8l1 14h10l1-14" />
      <path d="M12.5 12v6M15.5 12v6" opacity={0.8} />
    </svg>
  );
}

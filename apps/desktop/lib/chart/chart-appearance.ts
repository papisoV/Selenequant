// 图表外观偏好 —— 背景色 / 网格 / 蜡烛颜色。
//
// 分层理由与 drawing-prefs.ts 相同:这是**用户习惯**(换标的、重开应用都要保持),
// 不是某张图的数据。单独成文件 + 单独 localStorage key,坏掉一边不拖垮另一边。
//
// 为什么不复用 globals.css 的 CSS 变量:lightweight-charts 是 canvas 渲染,
// 不消费 var() / color-mix() / oklch()(见 lib/theme/chart-colors.ts 文件头),
// 必须把具体 sRGB 色串喂进 createChart/applyOptions。所以这里是**具体色**,
// 默认值取 globals.css 里 --color-bg / --color-pos / --color-neg 的同值,
// 保证"没动过设置"时观感与改造前完全一致。
//
// 配色原则(参考 ChartWatch 的三条):涨跌两色共享饱和度与亮度、背景取同色系
// 低饱和度 —— 这样任何预设拿出来都不会出现"荧光粉配泥巴棕"那种打架。

/** 背景。TradingView 的 Canvas 页同样只给 solid / gradient 两种。 */
export interface ChartBackground {
  /**
   * "transparent" 保留改造前的行为:图表透出 `.chart-main` 的底色,
   * 切换站点主题时跟着走。选具体色则是**锁定**该色,不再随主题。
   */
  kind: "transparent" | "solid";
  /** kind === "solid" 时生效,`#rrggbb`。 */
  color: string;
}

export interface ChartCandleColors {
  up: string;
  down: string;
  borderUp: string;
  borderDown: string;
  wickUp: string;
  wickDown: string;
}

export interface ChartAppearance {
  background: ChartBackground;
  /** 网格线颜色。空串 = 用内置的半透明白(改造前的样子)。 */
  gridColor: string;
  /** 是否显示网格。TradingView 支持"只竖 / 只横 / 都有 / 都无",这里先给总开关。 */
  gridVisible: boolean;
  candles: ChartCandleColors;
}

/**
 * 默认值 = 改造前 kf-chart.tsx / kline-chart.tsx 里写死的那些字面量。
 * **改这里就等于改所有没动过设置的用户看到的样子**,非必要别动。
 */
export const DEFAULT_CHART_APPEARANCE: ChartAppearance = {
  background: { kind: "transparent", color: "#04070b" },
  gridColor: "",
  gridVisible: true,
  candles: {
    up: "#61bd67",
    down: "#f3625d",
    borderUp: "#61bd67",
    borderDown: "#f3625d",
    wickUp: "#61bd67",
    wickDown: "#f3625d",
  },
};

/** 单独的 localStorage key,与绘图对象、绘图偏好三者互不干扰。 */
export const CHART_APPEARANCE_KEY = "selenespace-chart-appearance";

const HEX_RE = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

/** 合法色串才收,否则回退。空串对 gridColor 是合法值(表示"用内置色")。 */
function parseColor(raw: unknown, fallback: string, allowEmpty = false): string {
  if (typeof raw !== "string") return fallback;
  const v = raw.trim();
  if (allowEmpty && v === "") return "";
  return HEX_RE.test(v) ? v : fallback;
}

/**
 * 解析外观。**坏一个字段只回退那一个** —— 与 parseDrawingPrefs 同一纪律:
 * 用户只把背景改坏了,不该连带把蜡烛配色也重置。
 */
export function parseChartAppearance(raw: unknown): ChartAppearance {
  if (!raw || typeof raw !== "object") return cloneAppearance(DEFAULT_CHART_APPEARANCE);
  const o = raw as Record<string, unknown>;
  const bgRaw = o.background && typeof o.background === "object" ? (o.background as Record<string, unknown>) : {};
  const cRaw = o.candles && typeof o.candles === "object" ? (o.candles as Record<string, unknown>) : {};
  const d = DEFAULT_CHART_APPEARANCE;
  return {
    background: {
      kind: bgRaw.kind === "solid" ? "solid" : "transparent",
      color: parseColor(bgRaw.color, d.background.color),
    },
    gridColor: parseColor(o.gridColor, d.gridColor, true),
    gridVisible: typeof o.gridVisible === "boolean" ? o.gridVisible : d.gridVisible,
    candles: {
      up: parseColor(cRaw.up, d.candles.up),
      down: parseColor(cRaw.down, d.candles.down),
      borderUp: parseColor(cRaw.borderUp, d.candles.borderUp),
      borderDown: parseColor(cRaw.borderDown, d.candles.borderDown),
      wickUp: parseColor(cRaw.wickUp, d.candles.wickUp),
      wickDown: parseColor(cRaw.wickDown, d.candles.wickDown),
    },
  };
}

export function serializeChartAppearance(a: ChartAppearance): string {
  return JSON.stringify(a);
}

/** 从 JSON 字符串解析;任何异常收敛为默认值,不抛。 */
export function parseChartAppearanceJson(text: string | null): ChartAppearance {
  if (!text) return cloneAppearance(DEFAULT_CHART_APPEARANCE);
  try {
    return parseChartAppearance(JSON.parse(text));
  } catch {
    return cloneAppearance(DEFAULT_CHART_APPEARANCE);
  }
}

/** 深拷贝一层(嵌套两层,浅拷贝会把 candles/background 共享出去)。 */
export function cloneAppearance(a: ChartAppearance): ChartAppearance {
  return { ...a, background: { ...a.background }, candles: { ...a.candles } };
}

/** 是否与默认值完全一致 —— UI 上用来决定"恢复默认"按钮是否可点。 */
export function isDefaultAppearance(a: ChartAppearance): boolean {
  return serializeChartAppearance(a) === serializeChartAppearance(DEFAULT_CHART_APPEARANCE);
}

/**
 * 把外观翻译成 lightweight-charts 的 options 补丁。
 *
 * 纯函数、零图表库依赖(不 import lightweight-charts 的类型,只用结构等价
 * 的普通对象),这样 jsdom 里能直接断言"背景 solid 时喂的是不是那个色"。
 *
 * `transparent` 必须走 `{ color: "transparent" }`:LWC 的类型签名要求
 * `Background` 联合里带 `type`,但实测传裸 `{ color }` 与改造前的写法一致且
 * 生效(createChart 里原本就是 `{ color: "transparent" }`),保持同一条路。
 */
export function appearanceToChartOptions(a: ChartAppearance): {
  layout: { background: { color: string } };
  grid: {
    vertLines: { color: string; visible: boolean };
    horzLines: { color: string; visible: boolean };
  };
} {
  const grid = defaultGridColor();
  return {
    layout: {
      background: {
        color: a.background.kind === "solid" ? a.background.color : "transparent",
      },
    },
    grid: {
      vertLines: { color: a.gridColor || grid, visible: a.gridVisible },
      horzLines: { color: a.gridColor || grid, visible: a.gridVisible },
    },
  };
}

/** 改造前网格的内置色(半透明白)。抽出来是为了让上面那个函数保持纯。 */
export function defaultGridColor(): string {
  return "rgba(255,255,255,0.04)";
}

/**
 * lightweight-charts 的蜡烛样式键。store 里的短键(`up` / `borderUp` …)是给人
 * 读的,这里显式映射一次 —— 键名对不上时 TS 会报"两个类型无公共属性",
 * 而不是静默忽略传入的配色(那种失败只在肉眼里表现为"设了颜色没反应")。
 */
export interface ChartCandleSeriesOptions {
  upColor: string;
  downColor: string;
  borderUpColor: string;
  borderDownColor: string;
  wickUpColor: string;
  wickDownColor: string;
}

/** 把外观翻译成蜡烛 series 的 options 补丁。 */
export function appearanceToSeriesOptions(a: ChartAppearance): ChartCandleSeriesOptions {
  return {
    upColor: a.candles.up,
    downColor: a.candles.down,
    borderUpColor: a.candles.borderUp,
    borderDownColor: a.candles.borderDown,
    wickUpColor: a.candles.wickUp,
    wickDownColor: a.candles.wickDown,
  };
}

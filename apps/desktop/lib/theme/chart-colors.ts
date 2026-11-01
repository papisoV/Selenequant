/**
 * Chart color palette — 集中管理图表库用的颜色 hex/rgba。
 *
 * 为什么存在：
 *   lightweight-charts / echarts / SVG stroke 等图表库的 color parser 不支持
 *   oklch()，会报 "Failed to parse color"。设计稿用 oklch 是给 CSS 引擎用的
 *   (globals.css)，图表库必须用 sRGB hex 或 rgba。
 *
 * 设计稿 oklch → hex 转换：用 Python colorsys 或本文件附带的 oklch→sRGB 函数。
 * 改颜色时改这里一处，所有图表组件自动跟随。
 *
 * 禁止在 components/** 里直接写 oklch() 字符串字面量 — pre-commit hook
 * (scripts/check_no_oklch.py) 会阻断。
 */

export const chartColors = {
  // 涨跌色（lightweight-charts / echarts candlestick）
  up: "#5bb661",
  upStrong: "#31aa40",
  upSoft: "rgba(97, 189, 103, 0.6)",
  down: "#ed756e",
  downStrong: "#f14d4c",
  downSoft: "rgba(243, 98, 93, 0.6)",

  // 中性轴 / 网格（echarts axis）
  axisLabel: "#6e7276",
  axisLine: "#1f2225",
  splitLine: "#15191d",

  // 强调色
  gold: "#cf9b00",
  goldSoft: "rgba(255, 194, 73, 0.15)",
  blue: "#4ba3f7",
  blueSoft: "rgba(50, 102, 154, 0.5)",

  // SVG 渐变 stop-color
  askGradTop: "#c53637",
  askGradBottom: "#c53637",
  bidGradTop: "#278733",
  bidGradBottom: "#278733",

  // AI action tracks (use-kt-tracks)
  aiCloseLong: "#58905a",
  aiCloseShort: "#b46762",

  // Severity backgrounds
  warnBg: "rgba(250, 197, 71, 0.10)",
  errorBg: "rgba(250, 104, 99, 0.10)",
} as const;

export type ChartColorKey = keyof typeof chartColors;

/**
 * 取图表颜色。用法：chartColor("up") → "#5bb661"
 */
export function chartColor(key: ChartColorKey): string {
  return chartColors[key];
}

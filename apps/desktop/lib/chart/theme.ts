// 图表 token 契约 — 纯函数层(零 React / 零 components 依赖,jsdom 可测)。
//
// ⚠️ 分层铁律:本文件**不得 import 任何 `@/components/**`**。
//   全库 lib→components 仅 2 处且都是 `import type`(编译期擦除);
//   此处值导入会构成 lib 首次运行时依赖 components = 分层倒置。
//   React 侧的 useChartTheme hook 落在 `components/chart/use-chart-theme.ts`。
//
// 背景:canvas 图表(lightweight-charts / echarts)无法消费 CSS var()/color-mix(),
// 必须解析为具体色串,因此需要一层"读 token → 具体色"的契约。

export interface ChartTheme {
  /** 方向轴:涨/正 —— 随 data-locale 翻转 */
  pos: string;
  /** 方向轴:跌/负 —— 随 data-locale 翻转 */
  neg: string;
  /** 判定轴:错误/危险/回撤 —— 恒定不翻转 */
  destructive: string;
  /** 判定轴:完成/正常 —— 恒定不翻转 */
  success: string;
  gold: string;
  ink3: string;
  borderSoft: string;
}

// fallback 与 globals.css @theme 声明值保持一致(SSR / 未挂载时用)
const FALLBACK: ChartTheme = {
  pos: "#61bc66",
  neg: "#f3615c",
  destructive: "#f3615c",
  success: "#61bc66",
  gold: "#e3b650",
  ink3: "#767a7e",
  borderSoft: "#14181c",
};

function readCssVar(name: string): string {
  if (typeof document === "undefined") return "";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**
 * 读当前 locale 下的图表 token(具体色串)。
 * 未挂载(SSR)/变量缺失 → 走 FALLBACK 同值,不抛错。
 *
 * ⚠️ 调用方须在 locale 变化后重新调用(React 侧由 useChartTheme 的 useMemo 保证)。
 */
export function readChartTokens(host?: HTMLElement | null): ChartTheme {
  if (typeof document === "undefined") return FALLBACK;
  const el = host ?? document.documentElement;
  const read = (name: string, fallback: string) => {
    const v = el === document.documentElement
      ? readCssVar(name)
      : getComputedStyle(el).getPropertyValue(name).trim();
    return v || fallback;
  };
  return {
    pos: read("--color-pos", FALLBACK.pos),
    neg: read("--color-neg", FALLBACK.neg),
    destructive: read("--color-destructive", FALLBACK.destructive),
    success: read("--color-success", FALLBACK.success),
    gold: read("--color-gold", FALLBACK.gold),
    ink3: read("--color-ink-3", FALLBACK.ink3),
    borderSoft: read("--color-border-soft", FALLBACK.borderSoft),
  };
}

/** hex/rgb → rgba 串(canvas 图表半透明填充用)。非 hex 原样返回。 */
export function toRgba(color: string, alpha: number): string {
  const m = color.trim().replace(/^#/, "");
  if (m.length === 3 || m.length === 6) {
    const full = m.length === 3 ? m.split("").map((c) => c + c).join("") : m;
    const r = parseInt(full.slice(0, 2), 16);
    const g = parseInt(full.slice(2, 4), 16);
    const b = parseInt(full.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  return color;
}

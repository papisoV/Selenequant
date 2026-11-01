// 斐波那契档位的**视觉口径** —— 画布渲染器与对象树共用一份。
//
// 颜色不写死在渲染器里:对象树的小色标、图例、画布上的线必须同色,
// 否则用户看到"图例说是蓝的、图上画成灰的"就没法用它做判断。
//
// 这些色值是深色界面下的取值,与 lib/chart/theme.ts 的 FALLBACK 同族
// (gold "#e3b650")。canvas 读不了 CSS 变量,所以这里用字面量;
// 若将来要跟随主题,由调用方把 resolved 颜色传进 scene builder。

import { isExtensionLevel } from "@/lib/chart/fib-levels";
import type { LevelOverride } from "@/lib/chart/fib-object";

export const FIB_PRIMARY_COLOR = "#e3b650"; // 0.5 —— 腿的中轴
export const FIB_SECONDARY_COLOR = "#7aa2f7"; // 0.382 / 0.618 —— 黄金分割
export const FIB_TERTIARY_COLOR = "#8b93a7"; // 其余内部位
export const FIB_EXTENSION_COLOR = "#a07ad6"; // 延伸位

/** 线宽的合法区间(px)。解析与写入两端都夹到这里,免得 0 宽或 40 宽带崩可读性。 */
export const FIB_LINE_WIDTH_MIN = 0.5;
export const FIB_LINE_WIDTH_MAX = 6;

export interface LevelStyle {
  color: string;
  dashed: boolean;
  size: number;
}

/**
 * 单个档位的线型。延伸位一律虚线,避免与腿内的实线混淆。
 *
 * 第二参数是用户的逐档覆盖(见 `FibGrid.levelStyles`):先取默认桶,
 * 再叠加用户明确设置的那几项 —— 没设置的项保持默认,不会被整体替换掉。
 */
export function styleForLevel(level: number, override?: LevelOverride): LevelStyle {
  const base = defaultStyleForLevel(level);
  if (!override) return base;
  return {
    color: override.color ?? base.color,
    dashed: override.dashed ?? base.dashed,
    size: clampLineWidth(override.width ?? base.size),
  };
}

function defaultStyleForLevel(level: number): LevelStyle {
  if (isExtensionLevel(level)) return { color: FIB_EXTENSION_COLOR, dashed: true, size: 1 };
  if (level === 0.5) return { color: FIB_PRIMARY_COLOR, dashed: false, size: 1.6 };
  if (level === 0.382 || level === 0.618) return { color: FIB_SECONDARY_COLOR, dashed: false, size: 1.2 };
  return { color: FIB_TERTIARY_COLOR, dashed: true, size: 1 };
}

/** 夹线宽。非有限数回落到 1,而不是让 NaN 流到 canvas。 */
export function clampLineWidth(width: number): number {
  if (!Number.isFinite(width)) return 1;
  return Math.min(FIB_LINE_WIDTH_MAX, Math.max(FIB_LINE_WIDTH_MIN, width));
}

/** 锚点把手的半径(px)。命中检测与绘制必须用同一个数。 */
export const ANCHOR_HANDLE_RADIUS = 4;
/** 线的命中容差(px)。线条本身只有 1px,不给容差根本点不中。 */
export const LINE_HIT_TOLERANCE = 4;

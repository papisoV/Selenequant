// 磁吸 —— 落点吸附到 K 线的 OHLC。
//
// 语义照 TradingView(及其开源复刻 deepentropy/lightweight-charts-drawing 的
// `core/interact/project.ts::magnetSnap`,常量 50px 也取自那里):
//   weak   只在光标离某根 OHLC 屏幕距离 < 50px 时吸附,否则保持自由落点;
//   strong 永远吸到最近的一根 OHLC。
//
// 为什么按**屏幕距离**排序而不是价格差:价格轴一旦非均匀(对数轴 / 自动缩放),
// 价格差 10 的两条线在屏幕上可能一个挨着、一个隔着半屏。眼睛看到的是像素。
//
// 只吸价格、不动时间。改时间会把对象挪到另一根 K 线上 —— 用户点的是"这个价位",
// 不是"换一根 K 线"。

import type { MagnetMode } from "@/lib/chart/drawing-prefs";

/** 弱磁吸的作用半径(屏幕 px)。与参考实现的 MAGNET_WEAK_RADIUS_PX 同值。 */
export const MAGNET_WEAK_RADIUS_PX = 50;

/** 吸附所需的 K 线四价。只读,不改。 */
export interface OhlcBar {
  open: number;
  high: number;
  low: number;
  close: number;
}

/**
 * 把价格吸到 `bar` 最近的一个 OHLC 上。
 *
 * @param price  光标处的价格
 * @param bar    光标所在时间槽的 K 线;没有(空白槽 / 越界)调用方就不该调这里
 * @param mode   off 时返回 null(调用方用原价格)
 * @param yOfPrice 价格 → 屏幕 y。返回 null 表示此刻投影不出来,退化为按价格差比较
 * @returns 吸附后的价格;不该吸附时返回 null
 */
export function magnetSnapPrice(
  price: number,
  bar: OhlcBar | undefined,
  mode: MagnetMode,
  yOfPrice: (p: number) => number | null,
): number | null {
  if (mode === "off" || !bar) return null;

  const candidates = [bar.open, bar.high, bar.low, bar.close].filter((v) => Number.isFinite(v));
  if (candidates.length === 0) return null;

  const cursorY = yOfPrice(price);
  let best = candidates[0];
  let bestDy = Infinity;
  for (const v of candidates) {
    const vy = yOfPrice(v);
    const dy = cursorY !== null && vy !== null ? Math.abs(vy - cursorY) : Math.abs(price - v);
    if (dy < bestDy) {
      bestDy = dy;
      best = v;
    }
  }

  // 弱磁吸靠的是像素半径。投影不出来时手里只有价格差,拿它和 50px 比是两套单位,
  // 会得出"离 50 块以内就吸"这种荒谬结论 —— 宁可这一次不吸。
  if (mode === "weak") {
    if (cursorY === null) return null;
    if (bestDy >= MAGNET_WEAK_RADIUS_PX) return null;
  }
  return best;
}

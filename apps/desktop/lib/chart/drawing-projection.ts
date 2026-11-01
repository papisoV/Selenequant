// 绘图层的"数据 → 屏幕"投影 —— 纯函数(零 React / 零图表库依赖,jsdom 可测)。
//
// 为什么需要这一层:lightweight-charts 的 canvas 不暴露绘图 API,我们用一个
// 绝对定位的 <canvas> 盖在 K 线画布上自己画。于是每次重绘都要把「时间 + 价格」
// 换算成像素。换算是两件不同的事:
//
//   价格 → y:由 series.priceToCoordinate 给,当前缩放/自动刻度都算好。
//   时间 → x:图表 x 轴**按数据点索引排布,不按时间**(同 buildSlots 的注释)。
//            所以必须先把秒换成"分数逻辑索引",再让 timescale 换像素。
//
// 本文件只负责后者那一步算术 —— 它全是整数/浮点运算,可以脱离 canvas 测试。
// 真正调 chart API 的部分留在 drawing-layer.tsx。

/** 一个绘图对象要落地需要的两个换算器。测试里用假实现即可。 */
export interface ViewProjector {
  /** epoch 秒 → 画布横坐标(px)。超出可视区也返回坐标,不返回 null(除非图表空)。 */
  xOfTime(timeSec: number): number | null;
  /** 价格 → 画布纵坐标(px)。 */
  yOfPrice(price: number): number | null;
  /** 绘图区宽度(px,不含右侧价格轴)。 */
  width: number;
  /** 绘图区高度(px,不含底部时间轴)。 */
  height: number;
}

/** K 线柱的时间序列(秒),已按"补空白槽"排布 —— 与图上实际槽位一一对应。 */
export type SlotTimes = readonly number[];

/**
 * 把 epoch 秒换算成分数逻辑索引。
 *
 * 为什么按**槽**插值而不是直接比时间:图上每个槽固定一个 bar 间隔,
 * 跨缺口时若按真实时间线性插值,缺口会被拉成等比距离,而图上缺口是
 * 按槽位"撑开"的 —— 两套算法会对不上,线会漂。
 *
 * @param timeSec  目标时刻(秒)
 * @param slotTimes 槽位时间(秒,升序)
 * @param stepSec  一个 bar 的秒数(>= 1)
 */
export function logicalIndexForTime(
  timeSec: number,
  slotTimes: SlotTimes,
  stepSec: number,
): number {
  const n = slotTimes.length;
  if (n === 0) return 0;
  if (!(stepSec > 0)) throw new Error("logicalIndexForTime: stepSec must be > 0");

  // 在首槽之前:按固定步长向左外推(负索引,lightweight-charts 接受)
  if (timeSec <= slotTimes[0]) {
    return (timeSec - slotTimes[0]) / stepSec;
  }
  // 在末槽之后:向右外推。用末槽而不是 0,避免长历史累积误差。
  if (timeSec >= slotTimes[n - 1]) {
    return n - 1 + (timeSec - slotTimes[n - 1]) / stepSec;
  }

  // 二分:找到最后一个 slotTimes[i] <= timeSec
  let lo = 0;
  let hi = n - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (slotTimes[mid] <= timeSec) lo = mid;
    else hi = mid - 1;
  }
  const t0 = slotTimes[lo];
  if (t0 === timeSec) return lo;
  const t1 = slotTimes[lo + 1];
  // 槽位上相邻两格的时间差理应等于 stepSec;不等说明该处有真缺口 ——
  // 此时仍按"一格 = 一个索引"的规则,把它当作恰好一格,与图上槽位一致。
  const span = t1 - t0;
  if (!(span > 0)) return lo;
  return lo + Math.min(1, Math.max(0, (timeSec - t0) / span));
}

/** logicalIndexForTime 的逆运算:分数逻辑索引 → 时间(秒)。 */
export function timeForLogicalIndex(
  logicalIndex: number,
  slotTimes: SlotTimes,
  stepSec: number,
): number {
  const n = slotTimes.length;
  if (n === 0) return 0;
  if (!(stepSec > 0)) throw new Error("timeForLogicalIndex: stepSec must be > 0");
  if (logicalIndex <= 0) return slotTimes[0] + logicalIndex * stepSec;
  if (logicalIndex >= n - 1) return slotTimes[n - 1] + (logicalIndex - (n - 1)) * stepSec;

  const i = Math.floor(logicalIndex);
  const frac = logicalIndex - i;
  const t0 = slotTimes[i];
  const t1 = slotTimes[i + 1];
  return t0 + (t1 - t0) * frac;
}

/** 两个逻辑索引之间跨了几根 bar(含小数)。measure 工具与"腿宽"显示用。 */
export function barsBetween(li0: number, li1: number): number {
  return li1 - li0;
}

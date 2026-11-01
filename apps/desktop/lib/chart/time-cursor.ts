// 跨图共享时间游标 — 纯函数层(不触图表库,jsdom 可测)。
// 消费方: backtests-page.tsx(ECharts 权益图) + backtest-trade-review.tsx → candlestick-chart.tsx(K线)。

/**
 * 升序数组里最接近 target 的值(二分)。空数组回 null。
 *
 * 为什么必须吸附:两张图的 bar 密度不同(权益曲线一个采样点、K 线一根蜡烛),
 * 共享时间直接传给 lightweight-charts 的 setCrosshairPosition 时,若该时间点
 * 不在本图数据上,游标会**静默不显示** —— 看起来像同步坏了,其实是取点没对齐。
 */
export function nearestSorted(sorted: number[], target: number): number | null {
  const n = sorted.length;
  if (n === 0) return null;
  if (target <= sorted[0]) return sorted[0];
  if (target >= sorted[n - 1]) return sorted[n - 1];

  let lo = 0;
  let hi = n - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const v = sorted[mid];
    if (v === target) return v;
    if (v < target) lo = mid + 1;
    else hi = mid - 1;
  }
  // 循环结束时 lo = 第一个 > target 的下标,比较它与左边那个谁更近
  const right = sorted[lo];
  const left = sorted[lo - 1];
  return target - left <= right - target ? left : right;
}

/**
 * target 是否落在 [首, 尾] 区间内(含端)。
 *
 * 用途:K 线在聚焦模式下只装一笔交易的窗口,权益图悬停到窗口之外时
 * 不该把游标"吸"到窗口边缘 —— 那会谎报时间位置。落区外就直接不画。
 */
export function withinRange(sorted: number[], target: number): boolean {
  if (sorted.length === 0) return false;
  return target >= sorted[0] && target <= sorted[sorted.length - 1];
}

/**
 * 共享游标竖线 —— ECharts markLine 配置。
 *
 * 每条 series 上挂一条,三段网格(权益/回撤/敞口)就同时标出同一时刻。
 * 走 markLine 而不是 dispatchAction("updateAxisPointer"):后者依赖 tooltip/
 * axisPointer 的内部触发链,选项里没有 tooltip 时根本不渲染;markLine 是纯声明式的,
 * 行为确定、可被 getOption 读回,也就可被机械验收。
 */
export function cursorMarkLine(ms: number | null, color: string) {
  return {
    silent: true,
    symbol: "none" as const,
    animation: false,
    lineStyle: { color, width: 1, type: "dashed" as const, opacity: 0.85 },
    label: { show: false },
    data: ms == null ? [] : [{ xAxis: ms }],
  };
}

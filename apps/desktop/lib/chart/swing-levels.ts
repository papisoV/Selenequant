import type { KlineBar } from "@/lib/api/market-analysis";

// 支撑/阻力 swing 计算（B3）。确定性技术分析：摆动高低点（fractal pivot）→
// 价位聚类 → 支撑/阻力带。通用交易术语，与后端/PA 字段无关。

export const DEFAULT_SWING_K = 2;
export const DEFAULT_TOL_RATIO = 0.003;
export const DEFAULT_MAX_PER_SIDE = 3;

export interface SwingLevel {
  price: number;
  touches: number;
}

/** fractal 摆动高点：i 是 pivot 当且仅当 values[i] 是窗口内严格最大值。 */
export function findPivotHighs(values: number[], k = DEFAULT_SWING_K): number[] {
  return findPivots(values, k, (v) => v);
}

/** fractal 摆动低点：对称，取窗口内严格最小值。 */
export function findPivotLows(values: number[], k = DEFAULT_SWING_K): number[] {
  return findPivots(values, k, (v) => -v);
}

function findPivots(values: number[], k: number, rank: (v: number) => number): number[] {
  const pivots: number[] = [];
  for (let i = 0; i < values.length; i++) {
    const lo = Math.max(0, i - k);
    const hi = Math.min(values.length - 1, i + k);
    const r = rank(values[i]);
    let isPivot = true;
    for (let j = lo; j <= hi; j++) {
      if (j !== i && rank(values[j]) >= r) {
        isPivot = false;
        break;
      }
    }
    if (isPivot) pivots.push(i);
  }
  return pivots;
}

/** 按价排序后贪心聚类：相邻价差 <= tolRatio×代表价 合并，代表价 = 加权均值（按 touches）。 */
export function groupLevels(
  prices: number[],
  tolRatio = DEFAULT_TOL_RATIO,
): SwingLevel[] {
  const sorted = [...prices].sort((a, b) => a - b);
  if (sorted.length === 0) return [];
  const levels: SwingLevel[] = [];
  let rep = sorted[0];
  let touches = 1;
  for (let i = 1; i < sorted.length; i++) {
    const p = sorted[i];
    if (p - rep <= tolRatio * rep) {
      rep = (rep * touches + p) / (touches + 1);
      touches += 1;
    } else {
      levels.push({ price: rep, touches });
      rep = p;
      touches = 1;
    }
  }
  levels.push({ price: rep, touches });
  return levels;
}

/**
 * 从 K 线计算支撑/阻力价格列表。
 * 1. highs/lows → fractal pivot（k=2）
 * 2. 各自聚类（tolRatio=0.003）
 * 3. 过滤到可见区间 [minLow, maxHigh]
 * 4. 按 touches 降序取 maxPerSide，价格降序输出
 */
export function computeSupportResistance(
  bars: KlineBar[],
  opts?: { k?: number; tolRatio?: number; maxPerSide?: number },
): { supports: number[]; resistances: number[] } {
  const k = opts?.k ?? DEFAULT_SWING_K;
  const tolRatio = opts?.tolRatio ?? DEFAULT_TOL_RATIO;
  const maxPerSide = opts?.maxPerSide ?? DEFAULT_MAX_PER_SIDE;
  if (bars.length === 0) return { supports: [], resistances: [] };

  const highs = bars.map((b) => b.high);
  const lows = bars.map((b) => b.low);
  const minLow = Math.min(...lows);
  const maxHigh = Math.max(...highs);

  const rank = (levels: SwingLevel[]) =>
    levels
      .filter((l) => l.price >= minLow && l.price <= maxHigh)
      .sort((a, b) => b.touches - a.touches || b.price - a.price)
      .slice(0, maxPerSide)
      .map((l) => l.price)
      .sort((a, b) => b - a);

  return {
    supports: rank(groupLevels(findPivotLows(lows, k).map((i) => lows[i]), tolRatio)),
    resistances: rank(groupLevels(findPivotHighs(highs, k).map((i) => highs[i]), tolRatio)),
  };
}

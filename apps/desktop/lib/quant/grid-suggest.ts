export interface GridRange {
  upper: number;
  lower: number;
  levelCount: number;
  /** 每格 % */
  stepPercent: number;
}

export interface GridSuggestInput {
  currentPrice: number;
  /** ATR 绝对值(可选;有则优先,否则用波动率近似) */
  atr?: number;
  /** 波动率 %(无 ATR 时:单边幅度 = 波动率 / 2) */
  volatilityPct?: number;
  levelCount?: number;
}

export const DEFAULT_GRID_LEVELS = 8;
export const DEFAULT_GRID_VOLATILITY_PCT = 0.05;

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
function round4(n: number): number {
  return Math.round(n * 10000) / 10000;
}

/** "AI 网格"区间启发式:以当前价为中轴,±(ATR/波动率)/2 为上下界,等分 levelCount 格 */
export function suggestGridRange(input: GridSuggestInput): GridRange {
  const { currentPrice } = input;
  if (!currentPrice || currentPrice <= 0) {
    return {
      upper: 0,
      lower: 0,
      levelCount: Math.max(2, Math.floor(input.levelCount ?? DEFAULT_GRID_LEVELS)),
      stepPercent: 0,
    };
  }
  const levelCount = Math.max(2, Math.floor(input.levelCount ?? DEFAULT_GRID_LEVELS));
  const atrRatio =
    input.atr && input.atr > 0
      ? input.atr / currentPrice
      : input.volatilityPct ?? DEFAULT_GRID_VOLATILITY_PCT;
  const half = Math.max(atrRatio, 0.005) / 2;
  const upper = currentPrice * (1 + half);
  const lower = currentPrice * (1 - half);
  const stepPercent = ((upper - lower) / levelCount / currentPrice) * 100;
  return {
    upper: round4(upper),
    lower: round4(lower),
    levelCount,
    stepPercent: round2(stepPercent),
  };
}

/** 等分网格档位(喂 /grid/start) */
export function buildGridLevels(range: GridRange): number[] {
  const levels: number[] = [];
  for (let i = 0; i < range.levelCount; i++) {
    const t = range.levelCount > 1 ? i / (range.levelCount - 1) : 0;
    levels.push(round4(range.lower + (range.upper - range.lower) * t));
  }
  return levels;
}

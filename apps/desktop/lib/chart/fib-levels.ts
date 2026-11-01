// 斐波那契档位族 —— 纯数据层(零 React / 零图表库)。
//
// ⚠️ 这套档位**不是** TradingView 默认的那 7 条(0/0.236/0.382/0.5/0.618/0.786/1)。
// 它是 trader 自己在 `packages/strategy-engine/selene_space_strategy/fib_corner.py`
// 里定为 `FIB_LEVELS` 的 11 条:多了 0.2 / 0.3 / 0.7 / 0.886 —— 这几个是加密
// 永续上反复出现的反应位。图上画的必须和策略算的是同一套,否则研究与看盘打架。
//
// 改动这里的任何数值,必须同步改 fib_corner.py 的 FIB_LEVELS —— 两处一旦漂移,
// 回测里"碰到 0.3 了"和图上看到的线就不在一处。fib-levels.test.ts 会校验两处一致。

/** 手绘网格的完整档位族(来自 fib_corner.py)。含腿两端 0 与 1。 */
export const HAND_DRAWN_LEVELS: readonly number[] = [
  0,
  0.2,
  0.236,
  0.3,
  0.382,
  0.5,
  0.618,
  0.7,
  0.786,
  0.886,
  1,
];

/**
 * 延伸档位 —— 超过腿终点之后的投影位。
 *
 * fib_corner.py 只定义到 1.0(它做的是"贴近哪条内部线"的 touch 判定,不需要
 * 延伸位)。这里多出来的是**画图用**的:trader 说"某些斐波那契图需要自动扩展",
 * 指的就是把腿按这些比例继续投影出去。策略侧暂不消费,故不在 FIB_LEVELS 里,
 * 单列一处避免两边口径混淆。
 */
export const EXTENSION_LEVELS: readonly number[] = [
  1.272, 1.414, 1.618, 2.0, 2.618,
];

/** 负向延伸(腿起点之前的"镜像"位置)。仅在显式开启时使用。 */
export const NEGATIVE_EXTENSION_LEVELS: readonly number[] = [
  -0.272, -0.414, -0.618,
];

/**
 * 默认显示哪些档位。
 *
 * 11 条全画会把图糊住(尤其低周期)。默认隐藏 0 和 1 —— 它们就是两个锚点本身,
 * 视觉上已经由腿的两端表达,再画一条线只是重复信息。
 */
export function defaultVisibleLevels(): number[] {
  return HAND_DRAWN_LEVELS.filter((lv) => lv !== 0 && lv !== 1);
}

/** 0..1 之间的内部档位(不含两端)。 */
export function isInteriorLevel(level: number): boolean {
  return level > 0 && level < 1;
}

/** 超出腿范围的比例位(延伸或负向)。 */
export function isExtensionLevel(level: number): boolean {
  return level > 1 || level < 0;
}

/**
 * 某个档位的显示标签。
 *
 * 用三位有效位而不是固定小数位:0.2 → "0.2"、0.236 → "0.236"、1.272 → "1.272"。
 * 固定小数位会把 0.2 写成 "0.200",在一屏 11 条线的图例里纯属噪声。
 */
export function formatLevelLabel(level: number): string {
  return String(Number(level.toFixed(3)));
}

/**
 * 按档位族给每条线分配一个可区分的颜色索引。
 *
 * 0.5 是全场唯一的主线(交易者默认把它当"腿的中轴"),其余按族内顺序取色。
 * 返回 `-1` 表示"用主线色"。
 */
export function levelColorSlot(level: number): number {
  if (level === 0.5) return -1;
  const idx = HAND_DRAWN_LEVELS.indexOf(level);
  if (idx >= 0) return idx;
  const extIdx = EXTENSION_LEVELS.indexOf(level);
  if (extIdx >= 0) return HAND_DRAWN_LEVELS.length + extIdx;
  return HAND_DRAWN_LEVELS.length + EXTENSION_LEVELS.length;
}

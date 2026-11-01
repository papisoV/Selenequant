// 时间斐波那契的档位序列 —— 纯数据(零 React / 零图表库)。
//
// ⚠️ 这套是 **TV 的时间口径**,与 fib-levels.ts 的价格档位族(`HAND_DRAWN_LEVELS`)
// 是两套东西,不要合并、不要互相替换:
//   · 价格档位存的是"腿的比例"(0.382 / 0.618…),画的是**水平线**;
//   · 时间档位存的是"腿时长的倍数"(1 / 2 / 3 / 5 / 8…),画的是**竖线**。
// 价格族要和 fib_corner.py 保持逐字一致(business canon),时间族只服务于画图,
// 策略侧不消费 —— 所以它单列一处,绝不进 canon 测试的比对范围。

/**
 * 斐波那契数列前段。TV 默认展示的倍数序列。
 *
 * 为什么从 1 开始而不是 0:倍数 0 就是腿的起点,画在锚点上纯属重复;
 * 1 是腿的终点,也从序列里去掉 —— 两端已由腿本身表达。剩下 2,3,5,8… 才是新信息。
 */
export const FIB_TIME_SEQUENCE: readonly number[] = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89];

/** 默认勾选的时间档位(去掉两端)。 */
export function defaultTimeLevels(): number[] {
  return FIB_TIME_SEQUENCE.filter((k) => k !== 1);
}

/**
 * 第 k 条时间线的 x(像素)。
 *
 * 几何:腿从 p0 走到 p1,时长为 p1 − p0。时间档位 k 落在 p0 + k × 腿长 ——
 * 在**像素空间**里算(而不是先算时刻再投影),这样缩放/平移后间距比例恒定,
 * 与周期无关。腿反向画(从右往左)时用 min/max 归一,线仍然向右铺。
 */
export function timeOffsetX(x0: number, x1: number, multiple: number): number {
  const left = Math.min(x0, x1);
  const span = Math.abs(x1 - x0);
  return left + span * multiple;
}

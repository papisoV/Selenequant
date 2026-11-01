import { describe, expect, it } from "vitest";

import { HAND_DRAWN_LEVELS } from "@/lib/chart/fib-levels";
import { FIB_TIME_SEQUENCE, defaultTimeLevels, timeOffsetX } from "@/lib/chart/fib-time-levels";

describe("FIB_TIME_SEQUENCE", () => {
  it("是斐波那契数列前段,从 1 起", () => {
    expect(FIB_TIME_SEQUENCE.slice(0, 5)).toEqual([1, 2, 3, 5, 8]);
    // 每一项等于前两项之和 —— 这是"斐波那契"这个名字的唯一实质约束。
    for (let i = 2; i < FIB_TIME_SEQUENCE.length; i += 1) {
      expect(FIB_TIME_SEQUENCE[i]).toBe(FIB_TIME_SEQUENCE[i - 1] + FIB_TIME_SEQUENCE[i - 2]);
    }
  });

  it("默认档位去掉腿的终点(倍数 1)", () => {
    expect(defaultTimeLevels()).not.toContain(1);
    expect(defaultTimeLevels()[0]).toBe(2);
  });

  it("与价格档位族是两套东西,没有交集可被误当比例", () => {
    // 价格族没有 2/3/5/8… 这种整数倍数;若哪天有人把它们并进同一处,
    // 0.382 这类比例会混成"时间倍数"画出跑到画布外的竖线。
    const overlap = FIB_TIME_SEQUENCE.filter((k) => HAND_DRAWN_LEVELS.includes(k));
    // 唯一重叠是 1(腿终点),两边语义不同但数值相同,是允许的。
    expect(overlap).toEqual([1]);
  });
});

describe("timeOffsetX", () => {
  it("倍数 k 落在 p0 + k × 腿长", () => {
    expect(timeOffsetX(100, 200, 1)).toBe(200);
    expect(timeOffsetX(100, 200, 2)).toBe(300);
    expect(timeOffsetX(100, 200, 3)).toBe(400);
  });

  it("腿反向画(从右往左)仍向右铺", () => {
    // 起点在右、终点在左:腿长同样是 100,倍数 2 应当落在左端 + 200。
    expect(timeOffsetX(200, 100, 2)).toBe(300);
  });

  it("退化腿(两锚点同 x)所有倍数都落回原点", () => {
    expect(timeOffsetX(150, 150, 5)).toBe(150);
  });
});

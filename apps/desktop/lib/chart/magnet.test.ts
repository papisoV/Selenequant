import { describe, expect, it } from "vitest";

import { MAGNET_WEAK_RADIUS_PX, magnetSnapPrice } from "@/lib/chart/magnet";

const BAR = { open: 100, high: 110, low: 90, close: 105 };

describe("magnetSnapPrice", () => {
  it("off 档恒不吸附", () => {
    expect(magnetSnapPrice(97, BAR, "off", (p) => 500 - p)).toBeNull();
  });

  it("没有该槽位的 K 线时不吸附(空白槽 / 越界)", () => {
    expect(magnetSnapPrice(97, undefined, "strong", (p) => 500 - p)).toBeNull();
  });

  it("强吸附:即使很远也吸到最近的一个 OHLC", () => {
    // y = 500 - p,光标价 96 → 404;OHLC 的 y 分别是 400/390/410/395,
    // 最近的是 open(400)。
    expect(magnetSnapPrice(96, BAR, "strong", (p) => 500 - p)).toBe(100);
  });

  it("弱吸附:半径内吸,半径外不吸", () => {
    const near = (p: number) => 500 - p;
    // 光标价 100 → 距离 open 为 0px,吸
    expect(magnetSnapPrice(100, BAR, "weak", near)).toBe(100);
    // 光标价 140 → y=360;最近的候选是 high(110 → y=390)差 30px < 50,吸
    expect(magnetSnapPrice(140, BAR, "weak", near)).toBe(110);
    // 再远:200 → y=300,最近 high 差 90px > 50,不吸
    expect(magnetSnapPrice(200, BAR, "weak", near)).toBeNull();
  });

  it("弱吸附的边界正好是 MAGNET_WEAK_RADIUS_PX", () => {
    // 光标价 -1(不在候选里)投到 y=100+d;open 固定在 y=100,其余候选更远。
    // 于是"光标到最近 OHLC"的距离正好是 d,可以精确卡边界。
    const at = (d: number) => (p: number) => {
      if (p === -1) return 100 + d;
      if (p === BAR.open) return 100;
      return 100 + 10_000;
    };
    // d = R-1 → 吸;d = R → 不吸(判据是 < R,不是 <=)。
    expect(magnetSnapPrice(-1, BAR, "weak", at(MAGNET_WEAK_RADIUS_PX - 1))).toBe(BAR.open);
    expect(magnetSnapPrice(-1, BAR, "weak", at(MAGNET_WEAK_RADIUS_PX))).toBeNull();
  });

  it("投影返回 null 时:强吸附退化为价格差,弱吸附放弃(单位不可比)", () => {
    expect(magnetSnapPrice(104, BAR, "strong", () => null)).toBe(105);
    // 价格差 45 小于 50,但它不是像素 —— 弱吸附不能拿它当半径用。
    expect(magnetSnapPrice(150, BAR, "weak", () => null)).toBeNull();
  });

  it("OHLC 里有非有限值时剔除,不影响其余候选", () => {
    const dirty = { open: Number.NaN, high: 110, low: 90, close: 105 };
    expect(magnetSnapPrice(104, dirty, "strong", (p) => 500 - p)).toBe(105);
  });

  it("OHLC 全坏时返回 null(由调用方保留原价格)", () => {
    const bad = { open: Number.NaN, high: Infinity, low: Number.NaN, close: Number.NaN };
    expect(magnetSnapPrice(100, bad, "strong", (p) => p)).toBeNull();
  });
});

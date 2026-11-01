import { describe, expect, it } from "vitest";
import {
  TIER_FEATURES,
  TIER_LEVELS,
  DEMO_TIER_OPTIONS,
  tierGrants,
  type UserTier,
} from "./tiers";

describe("tiers(层级/功能矩阵)", () => {
  it("TIER_LEVELS 顺序 free < pro < vip", () => {
    expect(TIER_LEVELS.free).toBeLessThan(TIER_LEVELS.pro);
    expect(TIER_LEVELS.pro).toBeLessThan(TIER_LEVELS.vip);
  });

  it("tierGrants 矩阵:free 全锁,pro 解锁 pro 级,vip 全解锁", () => {
    const keys = Object.keys(TIER_FEATURES) as (keyof typeof TIER_FEATURES)[];
    const expectFor = (tier: UserTier, granted: string[]) =>
      keys.forEach((k) => expect(tierGrants(k, tier)).toBe(granted.includes(k)));

    expectFor("free", []);
    expectFor("pro", keys.filter((k) => TIER_FEATURES[k] === "pro"));
    expectFor("vip", keys);
  });

  it("arb_pro 具体值:pro 授予,vip 授予,free 不授予", () => {
    expect(tierGrants("arb_pro", "free")).toBe(false);
    expect(tierGrants("arb_pro", "pro")).toBe(true);
    expect(tierGrants("arb_pro", "vip")).toBe(true);
  });

  it("member_group 是 vip 级:pro 不授予", () => {
    expect(TIER_FEATURES.member_group).toBe("vip");
    expect(tierGrants("member_group", "pro")).toBe(false);
    expect(tierGrants("member_group", "vip")).toBe(true);
  });

  it("DEMO_TIER_OPTIONS 含 free/pro/vip 三个候选", () => {
    expect(DEMO_TIER_OPTIONS).toEqual(["free", "pro", "vip"]);
  });
});

import { describe, it, expect, beforeEach } from "vitest";
import { useIctDisplayStore } from "@/lib/store/ict-display";
import { DEFAULT_ICT_DISPLAY, ICT_DISPLAY_KEY } from "@/lib/chart/ict-display";

const reset = () => {
  localStorage.clear();
  useIctDisplayStore.getState().reset();
  localStorage.clear(); // reset 会写一次，清掉再断言"没有副作用"
};

describe("useIctDisplayStore", () => {
  beforeEach(reset);

  it("改参数落 localStorage，且只写自己的 key", () => {
    useIctDisplayStore.getState().setSwings({ maxCount: 33 });
    const raw = localStorage.getItem(ICT_DISPLAY_KEY);
    expect(raw).toContain('"maxCount":33');
    expect(localStorage.getItem("selenespace-fib-drawings")).toBeNull();
  });

  it("hydrate 从 localStorage 恢复", () => {
    localStorage.setItem(
      ICT_DISPLAY_KEY,
      JSON.stringify({ ...DEFAULT_ICT_DISPLAY, swings: { ...DEFAULT_ICT_DISPLAY.swings, maxCount: 12 } }),
    );
    useIctDisplayStore.getState().hydrate();
    expect(useIctDisplayStore.getState().settings.swings.maxCount).toBe(12);
  });

  it("拒绝关掉最后一个时段：状态不变，也不落盘", () => {
    const store = useIctDisplayStore.getState();
    // 默认伦敦 + 纽约开、亚洲关。先关伦敦是合法的（还剩纽约）。
    store.setKillzoneSessions({ london: false });
    expect(useIctDisplayStore.getState().settings.killzone.sessions).toEqual({
      london: false,
      new_york: true,
      asia: false,
    });

    // 再关纽约就成了空集 —— 后端会 400，这次改动必须被拒。
    const before = JSON.stringify(useIctDisplayStore.getState().settings.killzone.sessions);
    store.setKillzoneSessions({ new_york: false });
    expect(JSON.stringify(useIctDisplayStore.getState().settings.killzone.sessions)).toBe(before);
    expect(localStorage.getItem(ICT_DISPLAY_KEY)).not.toContain('"new_york":false');
  });

  it("阈值写反时当场对调，不把坏组合发给后端", () => {
    useIctDisplayStore
      .getState()
      .setDealingRange({ premiumThreshold: 0.2, discountThreshold: 0.75 });
    const dr = useIctDisplayStore.getState().settings.dealingRange;
    expect(dr.premiumThreshold).toBeCloseTo(0.75);
    expect(dr.discountThreshold).toBeCloseTo(0.2);
  });

  it("改档位不动其它档位", () => {
    useIctDisplayStore.getState().setSwingTiers({ st: true });
    const tiers = useIctDisplayStore.getState().settings.swings.tiers;
    expect(tiers).toEqual({ st: true, it: true, lt: true });
  });

  it("reset 回到出厂值并落盘", () => {
    useIctDisplayStore.getState().setEvents({ maxCount: 3 });
    useIctDisplayStore.getState().reset();
    expect(useIctDisplayStore.getState().settings).toEqual(DEFAULT_ICT_DISPLAY);
    expect(localStorage.getItem(ICT_DISPLAY_KEY)).toContain('"maxCount":12');
  });
});

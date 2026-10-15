import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// 行为锁：数据预载必须**分批限速**，不能一口气打后端。
//
// 改前实测：69 个预载请求有 67 个挤在首秒内 —— 因为 schedule() 用
// requestIdleCallback，而浏览器首帧后立刻空闲，批次之间实际没有间隔。
// 预载的定位是后台优化，不该和用户真实点击抢同一份后端容量。
//
// 撤掉 scheduleSpaced（改回 schedule）→ 本测试必红。

describe("page-renderer 预载限速", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("preloadAllData 的批次之间有最小间隔（1200ms）", async () => {
    const setTimeoutSpy = vi.spyOn(window, "setTimeout");
    const { preloadAllData } = await import("./page-renderer");

    preloadAllData();

    // 首次调度就该走 setTimeout（带间隔），而非 requestIdleCallback。
    const delays = setTimeoutSpy.mock.calls.map((c) => c[1]);
    expect(delays.length).toBeGreaterThan(0);
    expect(delays[0]).toBe(1200);
    // 关键：不能出现 requestIdleCallback 路径（它会在首帧后立刻触发）
    expect(
      (window as unknown as { requestIdleCallback?: unknown }).requestIdleCallback,
    ).toBeUndefined();
  });

  it("批次推进受间隔约束，不会同一 tick 跑完", async () => {
    const setTimeoutSpy = vi.spyOn(window, "setTimeout");
    const { preloadAllData } = await import("./page-renderer");

    preloadAllData();
    const initialCalls = setTimeoutSpy.mock.calls.length;

    // 同一 tick 内不推进：只有第一次调度被登记
    expect(initialCalls).toBe(1);

    // 走完一个间隔才推进下一批
    vi.advanceTimersByTime(1200);
    expect(setTimeoutSpy.mock.calls.length).toBeGreaterThan(initialCalls);
  });
});

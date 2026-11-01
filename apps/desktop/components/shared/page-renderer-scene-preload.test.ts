import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// 行为锁：数据预载按**场景**收窄，不再全量替所有场景烤缓存。
//
// 改前实测：打开 /research/today 后 100 秒内 116 个请求 / 66 个不同端点，
// 其中属于 research 的只有 23 个请求 / 5 个端点 —— 其余全是 watch/trade/system
// 的数据。预载的定位是"用户马上要点到的页"，跨场景冷启动由侧边栏 hover
// 预载（preloadPage）兜住，不需要在挂载时替所有场景空转。
//
// 撤掉场景过滤（preloadAllData 忽略 scene）→ 第一个测试必红。

const preloaded: string[] = [];

vi.mock("swr", () => ({
  // 真 preload 返回 Promise;widget 的 preloadData 会接 .catch(),
  // 返回 undefined 会在 drain 后抛 4 个 unhandled rejection。
  preload: (key: string) => {
    preloaded.push(key);
    return Promise.resolve();
  },
}));

vi.mock("@/lib/hooks/use-api", () => ({
  fetcher: async () => null,
}));

describe("page-renderer 场景化数据预载", () => {
  beforeEach(async () => {
    preloaded.length = 0;
    vi.resetModules();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  async function drain() {
    // 每批间隔 1200ms，跑够足够轮次把所有批次走完
    await vi.advanceTimersByTimeAsync(1200 * 300);
  }

  it("research 场景只预载 research 自己的端点", async () => {
    const { preloadAllData } = await import("./page-renderer");
    preloadAllData("research");
    await drain();

    expect(preloaded.length).toBeGreaterThan(0);
    // research 侧真会打的端点（来自 DATA_PRELOAD 的 research 段）
    const researchUrls = [
      "/api/v1/decision-workflow/trees",
      "/api/v1/experiments?limit=100",
      "/api/v1/strategies/factory-templates",
      "/api/v1/research/tuning/results",
      "/api/v1/qevolve/current",
      "/api/v1/research/graph/memory",
      "/api/v1/alpha/formulas",
      "/api/v1/alpha/saved-strategies",
      "/api/v1/strategies/deployments/list",
    ];
    const foreign = preloaded.filter((u) => !researchUrls.includes(u));
    expect(foreign, `research 场景不该预载这些: ${foreign.join(", ")}`).toEqual([]);
    // 收窄是有效果的：全量是 60 个 URL，research 只有 9 个不同端点
    // （strat/alpha_mine/optimizer/scanner 共用 factory-templates，会有重复 key）
    const distinct = new Set(preloaded);
    expect(distinct.size).toBeLessThanOrEqual(9);
  });

  it("watch 场景预载 watch 自己的端点，不碰 research", async () => {
    const { preloadAllData } = await import("./page-renderer");
    preloadAllData("watch");
    await drain();

    expect(preloaded.length).toBeGreaterThan(0);
    expect(preloaded).not.toContain("/api/v1/research/graph/memory");
    expect(preloaded).not.toContain("/api/v1/qevolve/current");
    expect(preloaded).not.toContain("/api/v1/decision-workflow/trees");
    // watch 侧确实进来了
    expect(preloaded.some((u) => u.startsWith("/api/v1/derivatives/funding-rates"))).toBe(true);
  });

  it("不给 scene 时保持全量（旧行为可回退）", async () => {
    const { preloadAllData } = await import("./page-renderer");
    preloadAllData();
    await drain();

    const scenes = ["/api/v1/research/graph/memory", "/api/v1/grid/state", "/api/v1/settings"];
    for (const u of scenes) expect(preloaded).toContain(u);
    expect(preloaded.length).toBeGreaterThan(30);
  });
});

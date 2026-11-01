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

// 行为锁：预载失败必须**静默**，不得变成 unhandledRejection。
//
// 事故：SWR 的 preload 把 fetcher() 的 promise 原样塞进 PRELOAD 缓存、自己
// 不做 catch（swr/dist/_internal: `const req = fetcher(fnArg); PRELOAD[key] = req`）。
// 预载是"全量空闲预载"，页面可能一直没人挂载 —— 这个 promise 就永远没人认领，
// 后端一返回 5xx/断连就冒 unhandledRejection（实测 /api/v1/experiments?limit=100）。
//
// 这里不用 `expect(code).toContain(".catch")` 那种源码字符串断言 —— 那种断言
// 对 `.catch(() => { throw })` 之类的错误实现照样绿。
//
// 也不能靠 `process.on("unhandledRejection")` 做行为断言：实测在本仓库的
// vitest/vite-node 环境里，**mock 出来的 fetcher rejection 根本不送达进程级
// 监听器**（真实网络失败的能送达，见下一条注释）。若这么写，无论有没有修复
// 都是绿 —— 空洞的绿，比没有测试更糟。探针自证记录：
//   calls=43 afterMicro=0 afterReal=0   ← 43 次拒绝，监听器一个都没收到
//
// 故断言落到**契约本身**：firePreload 必须认领 preload() 返回的 promise
// （即挂 .catch）。spy 记录每次 preload() 返回的 promise 上有没有 .catch 过。
// 这条能分辨好坏：撤掉 `.catch(() => {})` → 必红。
describe("page-renderer 预载失败静默", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.doUnmock("swr");
  });

  it("每一处 preload() 的返回值都必须被 .catch 认领", async () => {
    const caught: string[] = [];
    const uncaught: string[] = [];
    let preloadCalls = 0;

    // 包一层 preload：记录返回值有没有被 .catch/.then 认领。
    // 注意不能用 `tracked.then` 赋值 —— 那是可写属性，赋值不会改变承诺语义；
    // 直接用内联对象更干净：返回一个带 then/catch spy 的 thenable。
    vi.doMock("swr", async () => {
      const actual = await vi.importActual<typeof import("swr")>("swr");
      return {
        ...actual,
        preload: (_key: unknown, _fn: unknown) => {
          preloadCalls++;
          let claimed = false;
          const thenable = {
            then: (onFulfilled?: (v: unknown) => unknown, onRejected?: (e: unknown) => unknown) => {
              claimed = true;
              return Promise.resolve().then(onFulfilled, onRejected);
            },
            catch: (onRejected: (e: unknown) => unknown) => {
              claimed = true;
              return Promise.resolve().catch(onRejected);
            },
            finally: (cb: () => void) => {
              claimed = true;
              return Promise.resolve().finally(cb);
            },
          };
          // 调度结束后统一判定，避免"认领晚于检查"的假红。
          queueMicrotask(() => {
            if (claimed) caught.push(`call#${preloadCalls}`);
            else uncaught.push(`call#${preloadCalls}`);
          });
          return thenable;
        },
      };
    });

    try {
      const { preloadAllData } = await import("./page-renderer");
      preloadAllData();
      // 链式调度：每批之间 1200ms，多轮推进走完全部 slug。
      for (let i = 0; i < 60; i++) {
        await vi.advanceTimersByTimeAsync(1200);
      }

      // 反向验证：确实调用了 preload（否则下面的断言是空的）。
      expect(preloadCalls).toBeGreaterThan(0);
      // 核心断言：没有任何一处 preload() 的返回值被放任不管。
      expect(uncaught).toEqual([]);
    } finally {
      vi.doUnmock("swr");
    }
  });
});

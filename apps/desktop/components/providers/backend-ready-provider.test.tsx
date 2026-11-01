import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, act } from "@testing-library/react";
import { BackendReadyProvider } from "./backend-ready-provider";

// 行为锁：后端不可达时**不能**把整个应用锁在启动屏上。
//
// 旧实现 60s 轮询失败后 setStatus("failed") 渲染一张全屏「Runtime
// Unreachable」死页，把 children 全部挡掉 —— 用户既看不到界面，也不知道
// 为什么。新实现改为降级：children 照常渲染，只在底部挂一条非阻塞提示条。
//
// 撤掉新实现（把 degraded 改回 failed 全屏）→ 本测试必红。

let mockPathname = "/trade/paper";
vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
}));

const CHILD_TEXT = "PAGE-CHILD-MARKER";

function renderProvider() {
  return render(
    <BackendReadyProvider>
      <div>{CHILD_TEXT}</div>
    </BackendReadyProvider>
  );
}

beforeEach(() => {
  mockPathname = "/trade/paper";
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("BackendReadyProvider 降级行为", () => {
  it("后端始终不可达 → 60s 后放行 children 并显示提示条（而非全屏死页）", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("ECONNREFUSED")),
    );

    renderProvider();

    // 轮询期间children 被挡（显示启动屏）
    expect(screen.queryByText(CHILD_TEXT)).toBeNull();

    // 推进超过 MAX_RETRIES(60) × POLL_INTERVAL(1000ms)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(61_000);
    });

    // 关键断言：children 可见 —— 应用没有被锁死
    expect(screen.getByText(CHILD_TEXT)).toBeTruthy();
    // 且给出了原因
    expect(screen.getByText(/后端未连接/)).toBeTruthy();
    // 不再是那张全屏死页
    expect(screen.queryByText(/Runtime Unreachable/)).toBeNull();
  });

  it("后端就绪 → 直接放行 children，无提示条", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 200 }),
    );

    renderProvider();

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText(CHILD_TEXT)).toBeTruthy();
    expect(screen.queryByText(/后端未连接/)).toBeNull();
  });

  it("landing(/) 跳过检测，无条件放行", async () => {
    mockPathname = "/";
    vi.stubGlobal("fetch", vi.fn());

    renderProvider();

    expect(screen.getByText(CHILD_TEXT)).toBeTruthy();
    expect(screen.queryByText(/后端未连接/)).toBeNull();
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});

// 2026-10-13: 冷启动期间后端"活着但答得慢"——/health 要跑 registry 全量探针,
// 还要跟启动任务抢事件循环(见 main.py:_bridge_market_events 的 active_count 守卫)。
// 旧的 5s 超时会把这种慢响应误判成"后端不在",把用户推进降级空态。
// 契约: health 请求必须带 AbortSignal.timeout(15000)。撤实现 → 必红。
describe("BackendReadyProvider 健康门超时", () => {
  const HEALTH_TIMEOUT_MS = 15_000;

  it("健康请求带 15s 超时的 AbortSignal", async () => {
    const timeoutSpy = vi.spyOn(AbortSignal, "timeout");
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal("fetch", fetchMock);

    renderProvider();

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(fetchMock).toHaveBeenCalled();
    const [, init] = fetchMock.mock.calls[0];
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(timeoutSpy).toHaveBeenCalledWith(HEALTH_TIMEOUT_MS);
    timeoutSpy.mockRestore();
  });
});

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { useRunnerMonitor, toMonitorSignals, type RunnerSlot } from "./use-runner-monitor";

// WS 建连在 jsdom 里 stub 掉:喂数增量逻辑靠 REST 形状对齐保证,这里只测 REST 初值+动作
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  send() {}
  close() {}
}

const slot: RunnerSlot = {
  deployment_id: "dep-abc",
  strategy_id: "strat-1",
  account_id: "pa-strategy-strat-1",
  symbol: "BTC/USDT",
  interval: "1h",
  position: "long",
  bar_count: 120,
  signals_emitted: 3,
  orders_placed: 2,
  errors: 0,
  last_signal_ts: 1700000000000,
  recent_signals: [
    { ts: 1, side: "buy", reason: "金叉", price: 100, symbol: "BTC/USDT" },
    { ts: 2, side: "sell", reason: "死叉", price: 110, symbol: "BTC/USDT" },
  ],
};

function jsonResponse(body: unknown, ok = true) {
  return { ok, status: ok ? 200 : 500, json: async () => body } as unknown as Response;
}

describe("toMonitorSignals", () => {
  it("平铺 recent_signals 反转为新→旧并包 signal 对象", () => {
    const out = toMonitorSignals(slot);
    expect(out).toHaveLength(2);
    expect(out[0].ts).toBe(2);
    expect(out[0].signal).toEqual({ side: "sell", reason: "死叉", strength: null });
    expect(out[1].ts).toBe(1);
    expect(out[1].price).toBe(100);
  });

  it("无 recent_signals 返回空数组", () => {
    expect(toMonitorSignals({ ...slot, recent_signals: undefined })).toEqual([]);
  });
});

describe("useRunnerMonitor", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
    FakeWebSocket.instances = [];
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("REST 初值命中本策略 slot → 注入面板数据", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ data: { running: true, deployments: [slot] } }),
    );
    const { result } = renderHook(() => useRunnerMonitor("strat-1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.loadError).toBeNull();
    expect(result.current.slot?.deployment_id).toBe("dep-abc");
    expect(result.current.signals).toHaveLength(2);
    expect(result.current.deployment?.accountId).toBe("pa-strategy-strat-1");
  });

  it("无匹配 slot → 空态(不报错)", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ data: { running: true, deployments: [] } }),
    );
    const { result } = renderHook(() => useRunnerMonitor("strat-1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.slot).toBeNull();
    expect(result.current.signals).toEqual([]);
    expect(result.current.loadError).toBeNull();
  });

  it("runner_status 广播按 strategy_id 过滤并合并计数", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ data: { running: true, deployments: [slot] } }),
    );
    const { result } = renderHook(() => useRunnerMonitor("strat-1"));
    await waitFor(() => expect(result.current.slot?.bar_count).toBe(120));

    const ws = FakeWebSocket.instances[0];
    act(() => {
      ws.onopen?.();
    });
    expect(result.current.connected).toBe(true);
    act(() => {
      ws.onmessage?.({
        data: JSON.stringify({
          type: "runner_status",
          timestamp: 1700000001000,
          data: {
            deployment_id: "dep-abc",
            strategy_id: "strat-1",
            symbol: "BTC/USDT",
            interval: "1h",
            position: "flat",
            bar_count: 121,
            signals_emitted: 4,
            orders_placed: 2,
            errors: 0,
            last_signal_ts: 1700000001000,
          },
        }),
      });
    });
    expect(result.current.slot?.bar_count).toBe(121);
    expect(result.current.slot?.position).toBe("flat");
    // WS 广播无 recent_signals,spread 保留 REST 初值的历史
    expect(result.current.signals).toHaveLength(2);
  });

  it("strategy_signal 广播入队信号日志", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ data: { running: true, deployments: [slot] } }),
    );
    const { result } = renderHook(() => useRunnerMonitor("strat-1"));
    await waitFor(() => expect(result.current.slot).not.toBeNull());
    const ws = FakeWebSocket.instances[0];
    act(() => {
      ws.onopen?.();
    });
    act(() => {
      ws.onmessage?.({
        data: JSON.stringify({
          type: "strategy_signal",
          timestamp: 1700000002000,
          data: {
            deployment_id: "dep-abc",
            strategy_id: "strat-1",
            signal: { side: "buy", reason: "突破", price: 111, symbol: "BTC/USDT" },
            order_id: "ord-1",
          },
        }),
      });
    });
    expect(result.current.signals[0].ts).toBe(1700000002000);
    expect(result.current.signals[0].signal).toMatchObject({ side: "buy", reason: "突破" });
  });

  it("deploy 失败透传 runner_error", async () => {
    fetchMock.mockImplementation((input: unknown) => {
      const url = String(input);
      if (url.includes("/runner/status")) {
        return Promise.resolve(jsonResponse({ data: { running: true, deployments: [] } }));
      }
      if (url.includes("/deploy-paper")) {
        return Promise.resolve(
          jsonResponse({
            data: { runner_status: "failed", runner_error: "strategy has no trading rules" },
          }),
        );
      }
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    const { result } = renderHook(() => useRunnerMonitor("strat-1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    let err: string | null = null;
    await act(async () => {
      err = await result.current.deployPaper();
    });
    expect(err).toBe("strategy has no trading rules");
  });

  it("stop 成功清空 slot 与信号", async () => {
    fetchMock.mockImplementation((input: unknown) => {
      const url = String(input);
      if (url.includes("/runner/status")) {
        return Promise.resolve(jsonResponse({ data: { running: true, deployments: [slot] } }));
      }
      if (url.includes("/stop-deployment")) {
        return Promise.resolve(jsonResponse({ data: { stopped_deployments: ["dep-abc"] } }));
      }
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    const { result } = renderHook(() => useRunnerMonitor("strat-1"));
    await waitFor(() => expect(result.current.slot?.deployment_id).toBe("dep-abc"));
    let err: string | null = "pending";
    await act(async () => {
      err = await result.current.stopPaper();
    });
    expect(err).toBeNull();
    expect(result.current.slot).toBeNull();
    expect(result.current.signals).toEqual([]);
  });
});

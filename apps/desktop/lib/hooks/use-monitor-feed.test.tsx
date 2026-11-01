import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useMonitorFeed } from "./use-monitor-feed";
import { FEED_CAP, FEED_SUBSCRIPTIONS } from "@/lib/monitor-feed";

// 极简 WS 桩:捕获实例、记录订阅、手动触发 open/message
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  sent: string[] = [];
  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }
  send(data: string): void {
    this.sent.push(data);
  }
  close(): void {
    this.onclose?.({ code: 1000 });
  }
  open(): void {
    this.onopen?.();
  }
  emit(type: string, data: unknown): void {
    this.onmessage?.({ data: JSON.stringify({ type, data }) });
  }
}

function stubFetch(urlToBody: (url: string) => { ok: boolean; body: unknown }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const { ok, body } = urlToBody(url);
      return { ok, status: ok ? 200 : 500, json: async () => body } as Response;
    }),
  );
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", FakeWebSocket);
  // REST 初值全部返回空,避免干扰 batching 断言
  stubFetch((url) =>
    url.includes("/signals/dashboard") ? { ok: true, body: [] } : { ok: true, body: { data: { events: [] } } },
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useMonitorFeed", () => {
  it("WS 洪泛逐条只入缓冲,FLUSH_MS 单次合并进 setItems,FEED_CAP 截断、最新在前", () => {
    const { result } = renderHook(() => useMonitorFeed());
    const ws = FakeWebSocket.instances[0];
    expect(ws).toBeTruthy();

    act(() => {
      ws.open();
    });
    // onopen 订阅每个 (exchange, market_type) 组合各一条
    expect(ws.sent.length).toBe(FEED_SUBSCRIPTIONS.length);

    // 100 条 large_order 洪泛
    act(() => {
      for (let i = 1; i <= 100; i += 1) {
        ws.emit("large_order", {
          exchange: "binance",
          symbol: `BTC/USDT-${i}`,
          side: "buy",
          notional_usd: 500_000,
        });
      }
    });

    // 尚未 flush:items 仍为空(全部在缓冲),但首条已标记 live 防 demo
    expect(result.current.items).toHaveLength(0);
    expect(result.current.source).toBe("live");

    // 推进一个 flush 周期
    act(() => {
      vi.advanceTimersByTime(200);
    });

    expect(result.current.connected).toBe(true);
    expect(result.current.items).toHaveLength(FEED_CAP);
    // 最新(第 100 条)在前
    expect(result.current.items[0].id).toBe("large_trade-100");
    expect(result.current.items[0].text).toContain("BTC");
  });

  it("anomaly 帧走 adaptAnomaly(涨停文案),不被 signal 分支吞掉", () => {
    const { result } = renderHook(() => useMonitorFeed());
    const ws = FakeWebSocket.instances[0];

    act(() => {
      ws.open();
      ws.emit("anomaly", {
        exchange: "astock",
        symbol: "600519.SH",
        name: "贵州茅台",
        kind: "limit_up",
        price: 1786.5,
        prev_close: 1624.09,
        market_type: "intraday",
      });
    });
    act(() => {
      vi.advanceTimersByTime(200);
    });

    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0].kind).toBe("anomaly");
    // 关键:不是 "600519 信号强度 0"(旧的 signal 分支产物)
    expect(result.current.items[0].text).toBe("涨停 600519 贵州茅台 @1786.50 +10.00%");
  });

  it("订阅包含 astock/intraday 频道(否则 A 股异动永远到不了)", () => {
    const { result } = renderHook(() => useMonitorFeed());
    const ws = FakeWebSocket.instances[0];

    act(() => {
      ws.open();
    });

    const payloads = ws.sent.map((s) => JSON.parse(s) as { exchange: string; market_type: string });
    expect(payloads).toContainEqual({ action: "subscribe", symbols: ["ALL"], exchange: "astock", market_type: "intraday" });
    expect(result.current.connected).toBe(true);
  });

  it("live 数据到达后 demo 兜底不再触发", () => {
    const { result } = renderHook(() => useMonitorFeed());
    const ws = FakeWebSocket.instances[0];

    act(() => {
      ws.open();
      ws.emit("liquidation", { symbol: "ETH", side: "long", notional_usd: 1_000_000 });
    });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(result.current.source).toBe("live");

    // 推进越过 8s demo 窗口:不注入 demo
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(result.current.source).toBe("live");
    expect(result.current.items.some((f) => f.id.startsWith("demo-"))).toBe(false);
  });

  it("健康探针网络级错误(非超时)才注入 demo", async () => {
    // 后端不可达:REST 初值与健康探针全部失败
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("fetch failed");
    }));

    const { result } = renderHook(() => useMonitorFeed());

    await act(async () => {
      await vi.advanceTimersByTimeAsync(8_000);
    });

    expect(result.current.source).toBe("demo");
    expect(result.current.items.some((f) => f.id.startsWith("demo-"))).toBe(true);
  });

  it("切换 live 后合并会剔除旧 demo 条目", async () => {
    // 先让 demo 注入
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("fetch failed");
    }));
    const { result } = renderHook(() => useMonitorFeed());
    const ws = FakeWebSocket.instances[0];

    await act(async () => {
      await vi.advanceTimersByTimeAsync(8_000);
    });
    expect(result.current.source).toBe("demo");

    // 网络恢复:WS 推入真实数据,合并后 demo 条目被清掉
    await act(async () => {
      ws.open();
      ws.emit("large_trade", { exchange: "okx", symbol: "SOL/USDT", side: "sell", notional_usd: 300_000 });
      await vi.advanceTimersByTimeAsync(200);
    });

    expect(result.current.source).toBe("live");
    expect(result.current.items.some((f) => f.id.startsWith("demo-"))).toBe(false);
    expect(result.current.items).toHaveLength(1);
  });
});

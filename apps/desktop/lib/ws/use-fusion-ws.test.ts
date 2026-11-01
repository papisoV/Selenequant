import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useFusionWs, parseFusionSignal, clampStrength } from "./use-fusion-ws";

// 极简 WS 桩:捕获实例、记录订阅、手动触发 open/message（对齐 use-monitor-feed.test.tsx）
class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: FakeWebSocket[] = [];
  url: string;
  readyState = FakeWebSocket.CONNECTING;
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
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }
  emit(parsed: unknown): void {
    this.onmessage?.({ data: JSON.stringify(parsed) });
  }
}

function fusionMsg(side: "buy" | "sell", strength: number, over: Record<string, unknown> = {}) {
  return {
    type: "fusion_signal",
    data: {
      symbol: "BTC/USDT",
      side,
      strength,
      agreeing_count: 3,
      total_count: 5,
      strategy_ids: ["bb_v3", "rsi_ma_delta", "ema_triple_cross"],
      reason: "3/5 strategies agree",
      ...over,
    },
    timestamp: 1700000000000,
  };
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("parseFusionSignal", () => {
  it("buy → direction long，sell → direction short，strength 原样", () => {
    expect(parseFusionSignal(fusionMsg("buy", 0.8))?.direction).toBe("long");
    expect(parseFusionSignal(fusionMsg("sell", 0.8))?.direction).toBe("short");
    expect(parseFusionSignal(fusionMsg("buy", 0.8))?.strength).toBe(0.8);
    expect(parseFusionSignal(fusionMsg("buy", 0.8))?.symbol).toBe("BTC/USDT");
    expect(parseFusionSignal(fusionMsg("buy", 0.8))?.agreeingCount).toBe(3);
    expect(parseFusionSignal(fusionMsg("buy", 0.8))?.totalCount).toBe(5);
  });

  it("strength clamp 到 0-1，非有限值归 0", () => {
    expect(clampStrength(1.5)).toBe(1);
    expect(clampStrength(-0.2)).toBe(0);
    expect(clampStrength(0.7)).toBe(0.7);
    expect(clampStrength(NaN)).toBe(0);
    expect(clampStrength("abc")).toBe(0);
    expect(clampStrength("0.6")).toBe(0.6);
  });

  it("非 fusion_signal 消息 / 缺 data / 非法 side → 返回 null", () => {
    expect(parseFusionSignal({ type: "ping" })).toBeNull();
    expect(parseFusionSignal({ type: "fusion_signal", data: null })).toBeNull();
    expect(parseFusionSignal({ type: "fusion_signal", data: { symbol: "BTC/USDT", side: "sideways" } })).toBeNull();
    expect(parseFusionSignal(null)).toBeNull();
  });
});

describe("useFusionWs", () => {
  it("连接后发送 subscribe_fusion(symbol/min_agreeing)", () => {
    const { result } = renderHook(() => useFusionWs({ symbol: "ETH/USDT", minAgreeing: 4 }));
    const ws = FakeWebSocket.instances[0];
    expect(ws).toBeTruthy();
    expect(ws.url).toContain("/api/v1/ws/strategy");

    act(() => ws.open());
    expect(ws.sent.length).toBe(1);
    const sub = JSON.parse(ws.sent[0]) as Record<string, unknown>;
    expect(sub.action).toBe("subscribe_fusion");
    expect(sub.symbol).toBe("ETH/USDT");
    expect(sub.min_agreeing).toBe(4);
    expect(result.current.connected).toBe(true);
  });

  it("收到 buy 信号 → latest.direction=long，onSignal 回调收到", () => {
    const onSignal = vi.fn();
    const { result } = renderHook(() => useFusionWs({ symbol: "BTC/USDT", onSignal }));
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    act(() => ws.emit(fusionMsg("buy", 0.8)));

    expect(result.current.streamState).toBe("live");
    expect(result.current.latest?.direction).toBe("long");
    expect(result.current.latest?.strength).toBe(0.8);
    expect(result.current.latest?.strategyIds).toHaveLength(3);
    expect(onSignal).toHaveBeenCalledTimes(1);
    expect(onSignal.mock.calls[0][0].direction).toBe("long");
  });

  it("收到 sell 信号 → latest.direction=short", () => {
    const { result } = renderHook(() => useFusionWs());
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    act(() => ws.emit(fusionMsg("sell", 0.6)));
    expect(result.current.latest?.direction).toBe("short");
    expect(result.current.latest?.strength).toBe(0.6);
  });

  it("ignores ping/fusion_subscribed/坏消息(不置 latest)", () => {
    const { result } = renderHook(() => useFusionWs());
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    act(() => ws.emit({ type: "ping" }));
    act(() => ws.emit({ type: "fusion_subscribed", symbol: "BTC/USDT" }));
    act(() => ws.emit({ type: "not_a_signal" }));
    expect(result.current.latest).toBeNull();
    expect(result.current.streamState).toBe("recovering");
  });

  it("unmount 清理 WS 与定时器", () => {
    const { unmount } = renderHook(() => useFusionWs());
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    const closeSpy = vi.spyOn(ws, "close");
    unmount();
    expect(closeSpy).toHaveBeenCalled();
  });
});

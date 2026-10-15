import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useReconnectingWs } from "./use-reconnecting-ws";

// 行为锁: 重连 WS hook 契约。cleanroom 重写必须保持; 撤实现 → 必红。

class FakeWS {
  static instances: FakeWS[] = [];
  static OPEN = 1;
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  constructor(public url: string) {
    FakeWS.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.onclose?.();
  }
  // 测试辅助
  _open() {
    this.readyState = 1;
    this.onopen?.();
  }
  _message(data: string) {
    this.onmessage?.({ data } as MessageEvent);
  }
}

beforeEach(() => {
  FakeWS.instances = [];
  vi.stubGlobal("WebSocket", FakeWS as unknown as typeof WebSocket);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useReconnectingWs", () => {
  it("初始 connected=false", () => {
    const { result } = renderHook(() => useReconnectingWs("ws://x", () => {}));
    expect(result.current.connected).toBe(false);
  });

  it("onopen → connected=true 且调用 onOpen", () => {
    const onOpen = vi.fn();
    const { result } = renderHook(() => useReconnectingWs("ws://x", () => {}, onOpen));
    act(() => FakeWS.instances[0]._open());
    expect(result.current.connected).toBe(true);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("send 在 OPEN 时发出 JSON, 未连接时静默", () => {
    const { result } = renderHook(() => useReconnectingWs("ws://x", () => {}));
    act(() => result.current.send({ a: 1 })); // 未 open
    expect(FakeWS.instances[0].sent).toEqual([]);
    act(() => FakeWS.instances[0]._open());
    act(() => result.current.send({ a: 1 }));
    expect(FakeWS.instances[0].sent).toEqual([JSON.stringify({ a: 1 })]);
  });

  it("消息转交 onMessage", () => {
    const onMessage = vi.fn();
    renderHook(() => useReconnectingWs("ws://x", onMessage));
    act(() => FakeWS.instances[0]._message("hello"));
    expect(onMessage).toHaveBeenCalledTimes(1);
  });

  it("断线后 3s 重连(新建连接)", () => {
    vi.useFakeTimers();
    renderHook(() => useReconnectingWs("ws://x", () => {}));
    expect(FakeWS.instances).toHaveLength(1);
    act(() => FakeWS.instances[0].onclose?.());
    act(() => vi.advanceTimersByTime(3000));
    expect(FakeWS.instances).toHaveLength(2);
  });

  it("卸载时关闭连接", () => {
    const { unmount } = renderHook(() => useReconnectingWs("ws://x", () => {}));
    const ws = FakeWS.instances[0];
    const spy = vi.spyOn(ws, "close");
    unmount();
    expect(spy).toHaveBeenCalled();
  });
});

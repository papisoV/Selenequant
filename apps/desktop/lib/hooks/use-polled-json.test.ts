import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { usePolledJson } from "./use-polled-json";

// 行为锁: 轮询 JSON hook 契约。cleanroom 重写必须保持; 撤实现 → 必红。

beforeEach(() => {
  vi.restoreAllMocks();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("usePolledJson", () => {
  it("url=null → 不拉, data 保持 null", async () => {
    const spy = vi.spyOn(globalThis, "fetch");
    const { result } = renderHook(() => usePolledJson(null, 1000));
    expect(result.current.data).toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });

  it("取 body.data; 失败设 error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ data: { v: 7 } }), { status: 200 }),
    );
    const { result } = renderHook(() => usePolledJson<{ v: number }>("/api/x", 100000));
    await waitFor(() => expect(result.current.data).toEqual({ v: 7 }));
    expect(result.current.error).toBeNull();
  });

  it("无 data 包装 → 用 body 本身", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ v: 9 }), { status: 200 }),
    );
    const { result } = renderHook(() => usePolledJson<{ v: number }>("/api/x", 100000));
    await waitFor(() => expect(result.current.data).toEqual({ v: 9 }));
  });

  it("HTTP 非 2xx → error 带状态码", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response("", { status: 503 }));
    const { result } = renderHook(() => usePolledJson("/api/x", 100000));
    await waitFor(() => expect(result.current.error).toBe("HTTP 503"));
  });
});

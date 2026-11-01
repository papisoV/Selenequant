import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("@/lib/auth", () => ({ getAccessToken: () => null }));

import { fetcher } from "./use-api";

describe("use-api fetcher", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("成功且非包装响应: 原样返回", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ foo: 1 }),
    });
    await expect(fetcher("/x")).resolves.toEqual({ foo: 1 });
  });

  it("HTTP 429: 抛 ApiError(SWR 默认重试接管),不再吞成 null", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 429,
      statusText: "Too Many Requests",
    });
    await expect(fetcher("/x")).rejects.toMatchObject({ status: 429 });
  });

  it("网络异常: 抛 ApiError(status=0)", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockRejectedValue(
      new TypeError("network down")
    );
    await expect(fetcher("/x")).rejects.toMatchObject({ status: 0 });
  });

  it("APIResponse 包装含 error 字段: 抛 ApiError", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ data: null, meta: {}, error: { message: "boom" } }),
    });
    await expect(fetcher("/x")).rejects.toMatchObject({ message: "boom" });
  });

  it("No X data available sentinel: 仍返回 null(真无数据,不重试)", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({ data: { message: "No klines data available" }, meta: {} }),
    });
    await expect(fetcher("/x")).resolves.toBeNull();
  });
});

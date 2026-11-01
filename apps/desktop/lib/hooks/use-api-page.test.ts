import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useApiPage } from "./use-api-page";
import * as authModule from "@/lib/auth";

// SWR 全局缓存跨测试残留,用唯一 path 隔离
let _pathSeq = 0;
function uniquePath(prefix = "/api/v1/test"): string {
  _pathSeq += 1;
  return `${prefix}?n=${_pathSeq}`;
}

function mockFetchResponse(
  status: number,
  body: unknown,
  statusText = ""
): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText,
    json: async () => body,
  } as Response;
}

// 包装器:在同一个 hook 上下文里调用 useApiPage + useApi
function renderPageApi<T>(
  pageConfig: Parameters<typeof useApiPage>[0],
  path: string | null,
  opts?: Parameters<ReturnType<typeof useApiPage>["useApi"]>[1]
) {
  return renderHook(() => {
    const { useApi } = useApiPage(pageConfig);
    return useApi<T>(path, opts);
  });
}

describe("useApiPage", () => {
  beforeEach(() => {
    vi.spyOn(authModule, "getAccessToken").mockReturnValue(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("distinguish=false (default): HTTP 500 returns null, no error", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockFetchResponse(500, {}, "Internal Server Error"));

    const { result } = renderPageApi<{ x: number }>({}, uniquePath());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(result.current.data).toBeNull();
    expect(result.current.error).toBeUndefined();
  });

  it("distinguish=true: HTTP 500 surfaces ApiError in error channel", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockFetchResponse(500, {}, "Internal Server Error"));

    const { result } = renderPageApi<{ x: number }>(
      { distinguish: true },
      uniquePath()
    );

    await waitFor(() => {
      expect(result.current.error).toBeDefined();
    });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.error?.status).toBe(500);
    expect(result.current.data).toBeNull();
  });

  it("distinguish=true: success returns data, no error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockFetchResponse(200, { data: { x: 42 }, meta: {} })
    );

    const { result } = renderPageApi<{ x: number }>(
      { distinguish: true },
      uniquePath()
    );

    await waitFor(() => {
      expect(result.current.data).toEqual({ x: 42 });
    });

    expect(result.current.error).toBeUndefined();
    expect(result.current.isLoading).toBe(false);
  });

  it("distinguish=true: 'No X data available' sentinel returns null (not error)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockFetchResponse(200, {
        data: { message: "No ticker data available" },
        meta: {},
      })
    );

    const { result } = renderPageApi<{ x: number }>(
      { distinguish: true },
      uniquePath()
    );

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.data).toBeNull();
    expect(result.current.error).toBeUndefined();
  });

  it("distinguish=true: wrapped error body throws ApiError with code/details", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockFetchResponse(200, {
        data: null,
        meta: {},
        error: {
          message: "rate limited",
          code: "RATE_LIMIT",
          details: { retry_after: 30 },
        },
      })
    );

    const { result } = renderPageApi<{ x: number }>(
      { distinguish: true },
      uniquePath()
    );

    await waitFor(() => {
      expect(result.current.error).toBeDefined();
    });

    expect(result.current.error?.message).toBe("rate limited");
    expect(result.current.error?.code).toBe("RATE_LIMIT");
    expect(result.current.error?.details).toEqual({ retry_after: 30 });
  });

  it("distinguish=false: network throw (fetch rejects) returns null, no error", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"));

    const { result } = renderPageApi<{ x: number }>({}, uniquePath());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.data).toBeNull();
    expect(result.current.error).toBeUndefined();
  });

  it("distinguish=true: network throw (fetch rejects) surfaces error", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"));

    const { result } = renderPageApi<{ x: number }>(
      { distinguish: true },
      uniquePath()
    );

    await waitFor(() => {
      expect(result.current.error).toBeDefined();
    });

    expect(result.current.error?.message).toBe("network down");
    expect(result.current.data).toBeNull();
  });

  it("null path returns null data without fetch", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockFetchResponse(200, { data: {}, meta: {} }));

    const { result } = renderPageApi<{ x: number }>(
      { distinguish: true },
      null
    );

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(result.current.data).toBeNull();
  });

  it("multiple useApi calls inherit same distinguish flag", async () => {
    const pathA = uniquePath("/api/v1/a");
    const pathB = uniquePath("/api/v1/b");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockFetchResponse(500, {}, "Internal Server Error")
    );

    const { result } = renderHook(() => {
      const { useApi } = useApiPage({ distinguish: true });
      const a = useApi<{ a: number }>(pathA);
      const b = useApi<{ b: number }>(pathB);
      return { a, b };
    });

    await waitFor(() => {
      expect(result.current.a.error).toBeDefined();
      expect(result.current.b.error).toBeDefined();
    });

    expect(result.current.a.error?.status).toBe(500);
    expect(result.current.b.error?.status).toBe(500);
  });
});

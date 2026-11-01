import { afterEach, describe, expect, it, vi } from "vitest";
import { apiDirect, ApiError } from "./api-client";
import { resolveBackendHost } from "./backend-host";

const fetchMock = vi.fn();
const EXPECTED_BASE = `http://${resolveBackendHost()}`;

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function okJson(body: unknown): Response {
  return { ok: true, status: 200, statusText: "OK", json: async () => body } as Response;
}

function errJson(status: number, body: unknown): Response {
  return { ok: false, status, statusText: "ERR", json: async () => body } as Response;
}

describe("apiDirect(直连模式)", () => {
  it("走 http://{NEXT_PUBLIC_API_HOST} 前缀并解 envelope", async () => {
    fetchMock.mockResolvedValue(okJson({ data: { value: 42 } }));
    vi.stubGlobal("fetch", fetchMock);

    const res = await apiDirect<{ value: number }>("/api/v1/test");

    expect(res).toEqual({ value: 42 });
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe(`${EXPECTED_BASE}/api/v1/test`);
  });

  it("envelope 带 error 字段 → 抛 ApiError", async () => {
    fetchMock.mockResolvedValue(okJson({ error: { message: "boom", code: "E1", details: { x: 1 } } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiDirect("/api/v1/test")).rejects.toBeInstanceOf(ApiError);
    await expect(apiDirect("/api/v1/test")).rejects.toMatchObject({ code: "E1", status: 500 });
  });

  it("HTTP 非 2xx → 抛 ApiError(status)", async () => {
    fetchMock.mockResolvedValue(errJson(500, { error: { code: "E5" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiDirect("/api/v1/test")).rejects.toMatchObject({ status: 500 });
  });

  it("同 GET 进行中去重(只发一次)", async () => {
    fetchMock.mockResolvedValue(okJson({ data: { ok: true } }));
    vi.stubGlobal("fetch", fetchMock);

    await Promise.all([apiDirect("/api/v1/dedup"), apiDirect("/api/v1/dedup")]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

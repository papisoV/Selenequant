/**
 * alpha.ts 客户端测试 —— kline-coverage 读语义回归门。
 *
 * 关键纪律:mock 的是 fetch,喂真实 envelope shape,才能测到 api-client
 * 的真实行为(而非替身)。核心锁:**getKlineCoverage 不得掐死同页在飞的 GET**。
 *
 * 背景(2026-10-09):kline-coverage 曾是 POST,面板挂载即在飞的验证报告 GET
 * 被 api-client 的 abortAllPendingGets() 一并 abort,404 被掩盖成
 * "signal is aborted without reason"。本测试反向锁住:方法必须是 GET。
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { getKlineCoverage } from "./alpha";

const ENVELOPE = (data: unknown) =>
  JSON.stringify({ data, meta: { request_id: "req-test" }, error: null });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getKlineCoverage 读语义", () => {
  it("发 GET 且参数进 query string,不是 POST body", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(ENVELOPE({ count: 3, latest_timestamp: null, latest_iso: null }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    await getKlineCoverage({
      exchange: "binance",
      market_type: "futures",
      symbol: "BTC/USDT",
      interval: "1h",
    });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init?.method ?? "GET").toBe("GET");
    expect(init?.body).toBeUndefined();
    expect(url).toContain("/api/v1/alpha/kline-coverage?");
    expect(url).toContain("symbol=BTC%2FUSDT");
    expect(url).toContain("interval=1h");
  });

  it("并发在飞的 GET 不被其掐死(曾为 POST 的回归锁)", async () => {
    const signals: AbortSignal[] = [];
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (init?.signal) signals.push(init.signal);
      if (String(url).includes("kline-coverage")) {
        return Promise.resolve(
          new Response(ENVELOPE({ count: 1, latest_timestamp: null, latest_iso: null }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      // 另一个 GET:永不 settle,模拟"同页并发在飞"
      return new Promise<Response>(() => {});
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const { api } = await import("@/lib/api-client");
    void api("/api/v1/alpha/validation-report"); // 挂起,拿到 signal
    await getKlineCoverage({
      exchange: "binance",
      market_type: "futures",
      symbol: "BTC/USDT",
      interval: "1h",
    });

    // 那个在飞的 GET 仍活着(曾为 POST 时:abortAllPendingGets 会把它 abort → true)
    expect(signals[0].aborted).toBe(false);
  });
});

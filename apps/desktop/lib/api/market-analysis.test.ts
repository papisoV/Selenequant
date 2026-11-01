import { afterEach, describe, expect, it, vi } from "vitest";
import { streamAnalyzeMarket } from "./market-analysis";

vi.mock("@/lib/auth", () => ({
  getAccessToken: () => "test-token",
  clearAccessToken: () => {},
  refreshToken: () => Promise.resolve(false),
}));

const PROBE = { exchange: "binance", market_type: "futures", symbol: "BTC/USDT", interval: "1h" };

function streamedFetchResponse(events: Record<string, unknown>[]): {
  ok: true;
  status: number;
  statusText: string;
  body: ReadableStream<Uint8Array>;
} {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const [i, ev] of events.entries()) {
        const frame = `data: ${JSON.stringify(ev)}\n\n`;
        // 首帧拆成两半，验证跨 chunk 的帧缓冲重组（\n\n 可能落在 chunk 边界）
        if (i === 0 && frame.length > 4) {
          const half = Math.ceil(frame.length / 2);
          controller.enqueue(encoder.encode(frame.slice(0, half)));
          controller.enqueue(encoder.encode(frame.slice(half)));
        } else {
          controller.enqueue(encoder.encode(frame));
        }
      }
      controller.close();
    },
  });
  return { ok: true, status: 200, statusText: "OK", body };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("US-003 streamAnalyzeMarket SSE 解析", () => {
  it("逐帧回调 onEvent，analyze_done 时 resolve record", async () => {
    const events = [
      { type: "analyze_start" },
      { type: "stage1_start", attempt: 1 },
      { type: "stage1_text", delta: "市场趋势" },
      { type: "stage1_text", delta: "向上" },
      { type: "stage1_done", attempt: 1, usage: { prompt_tokens: 1 } },
      { type: "stage2_skipped", gate: "wait" },
      { type: "analyze_done", record: { meta: { record_id: "ar-1" } } },
    ];
    const fetchMock = vi.fn().mockResolvedValue(streamedFetchResponse(events));
    vi.stubGlobal("fetch", fetchMock);

    const seen: string[] = [];
    const record = await streamAnalyzeMarket(PROBE, (ev) => seen.push(ev.type));

    expect(seen).toEqual([
      "analyze_start",
      "stage1_start",
      "stage1_text",
      "stage1_text",
      "stage1_done",
      "stage2_skipped",
      "analyze_done",
    ]);
    expect(record.meta.record_id).toBe("ar-1");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/market-analysis/analyze/stream"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("analyze_fail 先回调再抛 ApiError（UI 可保留已流式文本）", async () => {
    const events = [
      { type: "stage1_start", attempt: 1 },
      { type: "stage1_text", delta: "部分内容" },
      { type: "analyze_fail", error: { error: "cheat_detected", field: "bar_features", message: "cheat" } },
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamedFetchResponse(events)));

    const seen: string[] = [];
    await expect(streamAnalyzeMarket(PROBE, (ev) => seen.push(ev.type))).rejects.toMatchObject({
      name: "ApiError",
      code: "cheat_detected",
    });
    expect(seen).toEqual(["stage1_start", "stage1_text", "analyze_fail"]);
  });

  it("流未含 analyze_done → 抛异常（连接中断兜底）", async () => {
    const events = [
      { type: "stage1_start", attempt: 1 },
      { type: "stage1_done", attempt: 1, usage: {} },
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamedFetchResponse(events)));
    await expect(streamAnalyzeMarket(PROBE, () => {})).rejects.toThrow("未收到结果");
  });

  it("HTTP 非 2xx → 抛 ApiError 带状态码", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 502, statusText: "Bad Gateway", body: null }),
    );
    await expect(streamAnalyzeMarket(PROBE, () => {})).rejects.toMatchObject({ status: 502 });
  });
});

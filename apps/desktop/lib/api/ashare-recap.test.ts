import { afterEach, describe, expect, it, vi } from "vitest";
import { parseRecapFrame, streamAshareRecap } from "./ashare-recap";

vi.mock("@/lib/auth", () => ({
  getAccessToken: () => "test-token",
  clearAccessToken: () => {},
  refreshToken: () => Promise.resolve(false),
}));

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
        // 首帧拆成两半:验证跨 chunk 的帧缓冲重组(\n\n 可能落在 chunk 边界)。
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

describe("parseRecapFrame", () => {
  it("extracts the JSON payload from a data: frame", () => {
    expect(parseRecapFrame('data: {"type":"done"}')).toEqual({ type: "done" });
  });

  it("returns null for a frame with no data line", () => {
    expect(parseRecapFrame(": keep-alive comment")).toBeNull();
  });

  it("returns null for malformed JSON instead of throwing", () => {
    // 一帧坏数据不能把整个流打断 —— 丢一帧好过丢一次复盘。
    expect(parseRecapFrame("data: {not json")).toBeNull();
  });
});

describe("streamAshareRecap", () => {
  it("dispatches meta → delta* → done in order and resolves", async () => {
    const events = [
      { type: "meta", as_of: "20261020", emotion_score: 61, emotion_label: "偏暖", summary: "摘要", status: "ok" },
      { type: "delta", content: "今日" },
      { type: "delta", content: "普涨" },
      { type: "done" },
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamedFetchResponse(events)));

    const seen: string[] = [];
    let text = "";
    await streamAshareRecap({ date: "2026-10-20" }, (ev) => {
      seen.push(ev.type);
      if (ev.type === "delta") text += ev.content;
    });

    expect(seen).toEqual(["meta", "delta", "delta", "done"]);
    expect(text).toBe("今日普涨");
  });

  it("builds the query string from the request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      streamedFetchResponse([{ type: "done" }]),
    );
    vi.stubGlobal("fetch", fetchMock);

    await streamAshareRecap({ date: "2026-10-20", focus: "  量能  ", lhb: false }, () => {});

    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("/api/v1/ashare/recap/stream?");
    expect(url).toContain("date=2026-10-20");
    expect(url).toContain("focus=%E9%87%8F%E8%83%BD"); // 前后空白已去除
    expect(url).toContain("lhb=false");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "GET" });
  });

  it("omits lhb only when explicitly false", async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamedFetchResponse([{ type: "done" }]));
    vi.stubGlobal("fetch", fetchMock);
    await streamAshareRecap({}, () => {});
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/ashare/recap/stream");
  });

  it("emits the error frame, then rejects with the server's verbatim message", async () => {
    // 关键契约:error 是**协议内事件**——未配置 LLM 时后端照样 200。
    // 只看 res.ok 会把这种情况当成成功、渲染一段空复盘。
    const serverMessage = "未配置 LLM 密钥:请先在设置中配置模型后重试";
    const events = [
      { type: "meta", as_of: "20261020", emotion_score: null, emotion_label: null, summary: "摘要", status: "ok" },
      { type: "error", message: serverMessage, reason: "llm_not_configured" },
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamedFetchResponse(events)));

    const seen: string[] = [];
    await expect(
      streamAshareRecap({}, (ev) => seen.push(ev.type)),
    ).rejects.toMatchObject({
      name: "ApiError",
      message: serverMessage,
      code: "llm_not_configured",
    });
    // 回调先于抛出 —— UI 才能保留已到达的 meta
    expect(seen).toEqual(["meta", "error"]);
  });

  it("rejects when the stream ends without a done or error frame", async () => {
    // 连接中断 = 异常,绝不能当作一次成功的复盘。
    const events = [
      { type: "meta", as_of: null, emotion_score: null, emotion_label: null, summary: "", status: "ok" },
      { type: "delta", content: "说到一半" },
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamedFetchResponse(events)));

    await expect(streamAshareRecap({}, () => {})).rejects.toMatchObject({
      name: "ApiError",
      message: "复盘流异常终止(未收到结束标记)",
    });
  });

  it("rejects on a non-2xx response before reading any frame", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 502, statusText: "Bad Gateway", body: null }),
    );
    await expect(streamAshareRecap({}, () => {})).rejects.toMatchObject({
      name: "ApiError",
      status: 502,
    });
  });

  it("skips unparseable frames but still completes on done", async () => {
    // 一帧坏 JSON 不该让整个流失败。
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode("data: {oops\n\n"));
        controller.enqueue(encoder.encode('data: {"type":"done"}\n\n'));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, statusText: "OK", body }));

    const seen: string[] = [];
    await streamAshareRecap({}, (ev) => seen.push(ev.type));
    expect(seen).toEqual(["done"]);
  });
});

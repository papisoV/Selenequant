import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ApiError } from "@/lib/api-client";

// 把 SSE 客户端整体替掉:本文件测的是面板对事件序列的反应,不是帧解析
// (帧解析另有 lib/api/ashare-recap.test.ts)。
const streamMock = vi.fn();
vi.mock("@/lib/api/ashare-recap", () => ({
  streamAshareRecap: (...args: unknown[]) => streamMock(...args),
}));

import { AshareRecapPanel } from "./ashare-recap-panel";

type OnEvent = (ev: Record<string, unknown>) => void;

/** 让 mock 按给定事件序列回调,然后正常/异常结束。 */
function replay(events: Record<string, unknown>[], fail?: Error) {
  streamMock.mockImplementation(async (_req: unknown, onEvent: OnEvent) => {
    for (const ev of events) onEvent(ev);
    if (fail) throw fail;
  });
}

const META = {
  type: "meta",
  as_of: "20261020",
  emotion_score: 61,
  emotion_label: "偏暖",
  summary: "上证指数 +0.64% | 情绪61(偏暖)",
  status: "ok",
};

describe("AshareRecapPanel", () => {
  beforeEach(() => {
    streamMock.mockReset();
  });

  it("does not call the model until the button is clicked", () => {
    // 一次复盘是真实计费调用。进页面就开流 = 替用户花钱。
    render(<AshareRecapPanel asOf="2026-10-20" />);
    expect(streamMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "生成复盘" })).toBeTruthy();
  });

  it("streams deltas into the body and shows the meta summary", async () => {
    replay([META, { type: "delta", content: "今日普涨," }, { type: "delta", content: "量能温和。" }, { type: "done" }]);
    render(<AshareRecapPanel asOf="2026-10-20" />);

    fireEvent.click(screen.getByRole("button", { name: "生成复盘" }));

    await waitFor(() =>
      expect(screen.getByTestId("recap-text").textContent).toBe("今日普涨,量能温和。"),
    );
    expect(screen.getByTestId("recap-summary").textContent).toBe(
      "上证指数 +0.64% | 情绪61(偏暖)",
    );
    // 完成态按钮变成「重新生成」
    expect(screen.getByRole("button", { name: "重新生成" })).toBeTruthy();
    // 未配置密钥的可操作链接不得出现
    expect(screen.queryByText(/前往设置配置模型/)).toBeNull();
    // 时标来自 SSE 的 meta.as_of(不是入参 asOf)。变异实测:改成 asOf={null} 原本零红。
    expect(screen.getByText("截至 20261020")).toBeTruthy();
  });

  it("renders the server's error text verbatim and links to settings", async () => {
    // 红线:未配置密钥时服务端给的是可操作指引。改写成"AI 暂不可用"
    // 会把用户卡死在原地 —— 所以断言的是**服务端原文**,不是某个概括词。
    const serverMessage = "未配置 LLM 密钥:请先在设置中配置模型后重试";
    replay(
      [META, { type: "error", message: serverMessage, reason: "llm_not_configured" }],
      new ApiError(serverMessage, 500, { code: "llm_not_configured" }),
    );
    render(<AshareRecapPanel asOf={null} />);

    fireEvent.click(screen.getByRole("button", { name: "生成复盘" }));

    const alert = await screen.findByTestId("recap-error");
    expect(alert.textContent).toContain(serverMessage);
    expect(alert.getAttribute("role")).toBe("alert");
    const link = screen.getByRole("link", { name: /前往设置配置模型/ });
    expect(link.getAttribute("href")).toBe("/system/set");
  });

  it("keeps the partial text when the stream fails mid-way", async () => {
    // 已流出的字是真实产出。清空再报错会让用户以为"什么都没发生"。
    replay(
      [META, { type: "delta", content: "已经写到一半" }],
      new ApiError("复盘流异常终止(未收到结束标记)", 500),
    );
    render(<AshareRecapPanel asOf={null} />);

    fireEvent.click(screen.getByRole("button", { name: "生成复盘" }));

    await screen.findByTestId("recap-error");
    expect(screen.getByTestId("recap-text").textContent).toBe("已经写到一半");
    expect(screen.getByTestId("recap-error").textContent).toContain("复盘流异常终止");
  });

  it("stops on command and returns to idle without a red error", async () => {
    // 用户点「停止」不是失败。abort 后必须回到 idle 而不是 error。
    streamMock.mockImplementation(
      (_req: unknown, onEvent: OnEvent, signal: AbortSignal) =>
        new Promise<void>((resolve, reject) => {
          onEvent(META);
          signal.addEventListener("abort", () => {
            const err = new Error("aborted");
            err.name = "AbortError";
            reject(err);
          });
        }),
    );
    render(<AshareRecapPanel asOf={null} />);

    fireEvent.click(screen.getByRole("button", { name: "生成复盘" }));
    await screen.findByRole("button", { name: "停止" });
    fireEvent.click(screen.getByRole("button", { name: "停止" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "生成复盘" })).toBeTruthy(),
    );
    expect(screen.queryByTestId("recap-error")).toBeNull();
  });
});

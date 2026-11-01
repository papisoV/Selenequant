import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { WhaleTransferFeed, DEMO_FALLBACK_MS } from "./whale-transfer-feed";

vi.mock("@/lib/api/onchain", () => ({
  getWhaleTransfers: vi.fn().mockRejectedValue(new Error("backend down")),
}));

describe("WhaleTransferFeed(巨鲸转账)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("fetch 失败 → 8s(缩短)后注入 demo 条目 + 演示徽章", async () => {
    render(<WhaleTransferFeed />);
    expect(screen.getByText("等待巨鲸数据...")).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEMO_FALLBACK_MS);
    });
    expect(screen.getByText("演示")).toBeTruthy();
    expect(screen.queryByText("等待巨鲸数据...")).toBeNull();
  });

  it("过滤:切流入 → 只剩流入行(demo 数据 in×2 / out×2 / internal×1)", async () => {
    const { container } = render(<WhaleTransferFeed />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEMO_FALLBACK_MS);
    });
    expect(container.querySelectorAll('[data-direction="in"]').length).toBe(2);
    expect(container.querySelectorAll('[data-direction="out"]').length).toBe(2);

    fireEvent.click(screen.getByRole("button", { name: "流入" }));
    expect(container.querySelectorAll('[data-direction="in"]').length).toBe(2);
    expect(container.querySelectorAll('[data-direction="out"]').length).toBe(0);
  });
});

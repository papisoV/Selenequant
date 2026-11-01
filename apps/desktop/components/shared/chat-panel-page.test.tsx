import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatPanelPage } from "./chat-panel-page";
import { useApi } from "@/lib/hooks/use-api";
import type { PageMeta } from "@/lib/page-manifest";

vi.mock("@/lib/hooks/use-api", () => ({
  useApi: vi.fn(),
}));

const mockUseApi = vi.mocked(useApi);

function meta(partial: Partial<PageMeta> & { slug: string; renderMode: "chat" }): PageMeta {
  return {
    title: partial.slug,
    scene: "system",
    category: "数据",
    removable: true,
    origin: "backend-insert",
    ...partial,
  } as PageMeta;
}

const chatMeta = meta({ slug: "notifications", renderMode: "chat", rest: "/api/v1/notifications" });

const dynamicMeta = meta({
  slug: "marketplace-listings-listing-id",
  renderMode: "chat",
  rest: "/api/v1/marketplace/listings/{listing_id}",
});

beforeEach(() => {
  mockUseApi.mockReset();
});

describe("ChatPanelPage(状态卡片/时间线)", () => {
  it("status 字段 → 彩色 pill 徽章", () => {
    mockUseApi.mockReturnValue({
      data: { status: "running", message: "ok" },
      isLoading: false,
    } as never);
    render(<ChatPanelPage meta={chatMeta} />);
    const badge = screen.getByText("running");
    expect(badge.className).toContain("text-pos");
  });

  it("纯 dict → 卡片列表(键值对)", () => {
    // 用非数组键避免误触发解包:status/uptime 都是纯标量
    mockUseApi.mockReturnValue({
      data: { uptime: 3600, version: "1.0", status: "on" },
      isLoading: false,
    } as never);
    render(<ChatPanelPage meta={chatMeta} />);
    expect(screen.getByText("Uptime")).toBeTruthy();
    expect(screen.getByText("on")).toBeTruthy();
  });

  it("含 timestamp + message 字段 → 时间线布局", () => {
    mockUseApi.mockReturnValue({
      data: {
        notifications: [
          { timestamp: "2026-01-01T10:00:00", message: "系统启动", status: "running" },
          { timestamp: "2026-01-01T10:05:00", message: "同步完成", status: "done" },
        ],
      },
      isLoading: false,
    } as never);
    render(<ChatPanelPage meta={chatMeta} />);
    expect(screen.getByText("2026-01-01 10:00")).toBeTruthy();
    expect(screen.getByText("系统启动")).toBeTruthy();
    expect(screen.getByText("同步完成")).toBeTruthy();
  });

  it("动态页 → '需从列表进入'", () => {
    mockUseApi.mockReturnValue({ data: null, isLoading: true } as never);
    render(<ChatPanelPage meta={dynamicMeta} />);
    expect(screen.getByText("此页面需从列表进入")).toBeTruthy();
    expect(screen.queryByText("暂无状态数据")).toBeNull();
  });

  it("空数据 → '暂无状态数据'", () => {
    mockUseApi.mockReturnValue({ data: null, isLoading: false } as never);
    render(<ChatPanelPage meta={chatMeta} />);
    expect(screen.getByText("暂无状态数据")).toBeTruthy();
  });
});

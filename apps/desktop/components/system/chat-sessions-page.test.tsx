import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const h = vi.hoisted(() => ({
  urls: [] as string[],
  payload: null as unknown,
}));

vi.mock("@/lib/hooks/use-api", () => ({
  useApi: (url: string | null) => {
    if (url) h.urls.push(url);
    return { data: h.payload, isLoading: false, error: null };
  },
  fetcher: () => Promise.resolve(null),
}));

vi.mock("@/components/shared/page-filter-bar", () => ({
  V17Card: ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div data-testid={`card-${title}`}>{children}</div>
  ),
  StatBlock: ({ label, value }: { label: string; value: string | number }) => (
    <div data-testid={`stat-${label}`}>{value}</div>
  ),
}));

vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock("@/lib/api/chat", () => ({
  getChatSession: vi.fn(),
  getChatSessionMessages: vi.fn(),
}));

import { ChatSessionsPage } from "./chat-sessions-page";

// chat_sessions 表(v003)只有 id/strategy_id/title/created_at/updated_at
// 旧代码读 s.messages / s.last_active(均不存在) → 消息数恒 0、最后活跃恒空
const ROWS = [
  { id: "chat-abc", title: "会话一", created_at: "2026-10-01T10:00:00", updated_at: "2026-10-02T11:30:00" },
  { id: "chat-def", title: "会话二", created_at: "2026-10-03T09:00:00", updated_at: "2026-10-03T12:00:00" },
];

describe("ChatSessionsPage — 死亡字段 messages/last_active 已移除", () => {
  beforeEach(() => {
    h.urls.length = 0;
    h.payload = null;
  });

  it("概览只显示总会话(去掉恒 0 的总消息),列表显示 updated_at", () => {
    h.payload = ROWS;
    const { container } = render(<ChatSessionsPage />);

    expect(screen.getByTestId("stat-总会话").textContent).toBe("2");
    // 旧代码的"总消息"卡(恒 0)已删
    expect(screen.queryByTestId("stat-总消息")).toBeNull();
    // updated_at 渲染为可读时间
    expect(container.textContent).toContain("2026-10-02 11:30");
    // 不再出现旧死字段的 "undefined 条"
    expect(container.textContent).not.toContain("undefined");
  });

  it("空列表 → 空态", () => {
    h.payload = [];
    render(<ChatSessionsPage />);
    expect(screen.getByText("暂无数据")).toBeTruthy();
  });

  it("请求打到 /api/v1/chat/sessions", () => {
    render(<ChatSessionsPage />);
    expect(h.urls.some((u) => u.startsWith("/api/v1/chat/sessions"))).toBe(true);
  });
});

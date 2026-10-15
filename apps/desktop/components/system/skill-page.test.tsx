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
}));

import { SkillPage } from "./skill-page";

// 后端 /api/v1/skills 返回 { skills: [...], count }
// (旧代码对 wrapper 调 Array.isArray → 恒空页)
const ENVELOPE = {
  skills: [
    { name: "tdd", description: "测试驱动开发", source: "repo", tags: ["test", "quality"], enabled: true },
    { name: "review", description: "代码评审", source: "user", tags: [], enabled: false },
  ],
  count: 2,
};

describe("SkillPage — 解 {skills:[...]} 包装(旧代码恒空)", () => {
  beforeEach(() => {
    h.urls.length = 0;
    h.payload = null;
  });

  it("从 envelope.skills 渲染,读真实 name/tags/description", () => {
    h.payload = ENVELOPE;
    render(<SkillPage />);

    expect(screen.getByText("tdd")).toBeTruthy();
    expect(screen.getByText("review")).toBeTruthy();
    expect(screen.getByText("测试驱动开发")).toBeTruthy();
    expect(screen.getByText("test · quality")).toBeTruthy();
    // 不再有旧的 "N 安装" 死字段
    expect(screen.queryByText(/安装/)).toBeNull();
    // 启用状态真实
    expect(screen.getByText("已启用")).toBeTruthy();
    expect(screen.getByText("未启用")).toBeTruthy();
  });

  it("空 envelope → 空态", () => {
    h.payload = { skills: [], count: 0 };
    render(<SkillPage />);
    expect(screen.getByText("暂无数据")).toBeTruthy();
  });

  it("请求打到 /api/v1/skills", () => {
    render(<SkillPage />);
    expect(h.urls.some((u) => u === "/api/v1/skills")).toBe(true);
  });
});

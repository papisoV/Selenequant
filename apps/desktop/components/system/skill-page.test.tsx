import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const h = vi.hoisted(() => ({
  urls: [] as string[],
  payload: null as unknown,
  mutate: vi.fn(),
  fetchBare: vi.fn(),
}));

vi.mock("@/lib/hooks/use-api", () => ({
  useApi: (url: string | null) => {
    if (url) h.urls.push(url);
    return { data: h.payload, isLoading: false, error: null, mutate: h.mutate };
  },
  fetcher: () => Promise.resolve(null),
}));

vi.mock("@/lib/api-client", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api-client")>("@/lib/api-client");
  return { ...actual, fetchBare: h.fetchBare };
});

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
    h.mutate.mockReset();
    h.fetchBare.mockReset();
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

describe("SkillPage — 启用开关(此前只读)", () => {
  beforeEach(() => {
    h.payload = ENVELOPE;
    h.mutate.mockReset();
    h.fetchBare.mockReset();
  });

  it("点未启用技能 → PUT enable=true,状态翻为已启用,重拉列表", async () => {
    render(<SkillPage />);
    fireEvent.click(screen.getByRole("button", { name: "启用技能 review" }));
    await waitFor(() => expect(h.fetchBare).toHaveBeenCalledTimes(1));
    expect(h.fetchBare).toHaveBeenCalledWith("/api/v1/skills/review/enable", {
      method: "PUT",
      body: JSON.stringify({ enabled: true }),
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "停用技能 review" })).toBeTruthy());
    expect(h.mutate).toHaveBeenCalled();
  });

  it("点已启用技能 → PUT enable=false", async () => {
    render(<SkillPage />);
    fireEvent.click(screen.getByRole("button", { name: "停用技能 tdd" }));
    await waitFor(() => expect(h.fetchBare).toHaveBeenCalledTimes(1));
    expect(h.fetchBare).toHaveBeenCalledWith("/api/v1/skills/tdd/enable", {
      method: "PUT",
      body: JSON.stringify({ enabled: false }),
    });
  });

  it("PUT 失败 → 状态回滚 + 错误横幅", async () => {
    h.fetchBare.mockRejectedValue(new Error("HTTP 500"));
    render(<SkillPage />);
    fireEvent.click(screen.getByRole("button", { name: "启用技能 review" }));
    await screen.findByText(/启用状态保存失败/);
    expect(screen.getByRole("button", { name: "启用技能 review" })).toBeTruthy();
  });
});

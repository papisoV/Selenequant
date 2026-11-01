import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ResearchPanelPage } from "./research-panel-page";
import { useApi } from "@/lib/hooks/use-api";
import type { PageMeta } from "@/lib/page-manifest";

vi.mock("@/lib/hooks/use-api", () => ({
  useApi: vi.fn(),
}));

const mockUseApi = vi.mocked(useApi);

function meta(partial: Partial<PageMeta> & { slug: string; renderMode: "research" }): PageMeta {
  return {
    title: partial.slug,
    scene: "research",
    category: "数据",
    removable: true,
    origin: "backend-insert",
    ...partial,
  } as PageMeta;
}

const researchMeta = meta({ slug: "alpha-formulas", renderMode: "research", rest: "/api/v1/alpha/formulas" });

const dynamicMeta = meta({
  slug: "research-tuning-tasks-task-id",
  renderMode: "research",
  rest: "/api/v1/research/tuning/tasks/{task_id}",
});

// 卡片 = 带 p-2 圆角的网格子元素(搜索/字段选择工具条不在此容器内)
function cards(): HTMLElement[] {
  return Array.from(document.querySelectorAll("div.grid > div.border"));
}

function cardText(card: HTMLElement, text: string): boolean {
  return within(card).queryByText(text) !== null;
}

beforeEach(() => {
  mockUseApi.mockReset();
});

describe("ResearchPanelPage(卡片网格)", () => {
  it("卡片渲染:标题优先 name/id/title", () => {
    mockUseApi.mockReturnValue({
      data: [
        { id: "f1", name: "动量因子", score: 0.8 },
        { title: "波动率", score: 0.6 },
      ],
      isLoading: false,
    } as never);
    render(<ResearchPanelPage meta={researchMeta} />);
    const cs = cards();
    expect(cs.length).toBe(2);
    expect(within(cs[0]).getByText("动量因子")).toBeTruthy();
    expect(within(cs[1]).getByText("波动率")).toBeTruthy();
  });

  it("默认显示前 5 个键(标题源键除外,第 6 键不上卡片)", () => {
    mockUseApi.mockReturnValue({
      data: [{ name: "动量", a: 1, b: 2, c: 3, d: 4, e: 5, f: 6 }],
      isLoading: false,
    } as never);
    render(<ResearchPanelPage meta={researchMeta} />);
    const c = cards()[0];
    // name 做标题;默认 5 键中 name 被标题消费,字段行显示 a..d
    for (const t of ["A", "B", "C", "D"]) expect(cardText(c, t)).toBe(true);
    expect(cardText(c, "Name")).toBe(false);
    // 第 5/6 键 E/F 不上卡片(但作为字段选项存在于工具条)
    expect(cardText(c, "E")).toBe(false);
    expect(cardText(c, "F")).toBe(false);
  });

  it("搜索:文本过滤卡片", () => {
    mockUseApi.mockReturnValue({
      data: [{ id: "btc" }, { id: "eth" }, { id: "sol" }],
      isLoading: false,
    } as never);
    render(<ResearchPanelPage meta={researchMeta} />);
    fireEvent.change(screen.getByPlaceholderText("搜索卡片…"), { target: { value: "eth" } });
    const cs = cards();
    expect(cs.length).toBe(1);
    expect(within(cs[0]).getByText("eth")).toBeTruthy();
  });

  it("搜索无匹配 → '无匹配卡片'", () => {
    mockUseApi.mockReturnValue({ data: [{ id: "btc" }], isLoading: false } as never);
    render(<ResearchPanelPage meta={researchMeta} />);
    fireEvent.change(screen.getByPlaceholderText("搜索卡片…"), { target: { value: "zzz" } });
    expect(screen.getByText("无匹配卡片")).toBeTruthy();
  });

  it("字段选择:取消勾选某键 → 卡片不再显示该键", () => {
    mockUseApi.mockReturnValue({
      data: [{ id: "f1", name: "动量", extra: 1 }],
      isLoading: false,
    } as never);
    render(<ResearchPanelPage meta={researchMeta} />);
    const c = cards()[0];
    expect(cardText(c, "Extra")).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "Extra" }));
    expect(cardText(c, "Extra")).toBe(false);
  });

  it("动态页 → '需从列表进入'", () => {
    mockUseApi.mockReturnValue({ data: null, isLoading: true } as never);
    render(<ResearchPanelPage meta={dynamicMeta} />);
    expect(screen.getByText("此页面需从列表进入")).toBeTruthy();
    expect(screen.queryByText("暂无研究数据")).toBeNull();
  });
});

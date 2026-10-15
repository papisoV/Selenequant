import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { RschPage } from "@/components/research/rsch-page";
import { CHAINS } from "@/lib/navigation-chains";

// rsch-page 直接 import useApi（不经 useApiPage），故直接 mock 该模块
const { useApiMock } = vi.hoisted(() => ({ useApiMock: vi.fn() }));
vi.mock("@/lib/hooks/use-api", () => ({ useApi: useApiMock }));

describe("RschPage A股研究入口卡 (P0-2)", () => {
  beforeEach(() => {
    useApiMock.mockReturnValue({ data: [], error: undefined, isLoading: false });
  });

  it("渲染 A股研究链全部 5 页的入口链接，href 指到 research/<slug>", () => {
    render(<RschPage />);
    const steps = CHAINS.research.find((c) => c.id === "ashare")!.steps;
    expect(steps.length).toBeGreaterThan(0);
    for (const slug of steps) {
      const link = screen.getByTestId(`ashare-entry-${slug}`);
      expect(link.getAttribute("href")).toBe(`/research/${slug}`);
    }
  });

  it("入口列表与导航链同源：链增删页时卡片自动跟随（非手抄）", () => {
    render(<RschPage />);
    const steps = CHAINS.research.find((c) => c.id === "ashare")!.steps;
    // 卡片项数 === 链 steps 数，无第 6 项、无缺失
    const rendered = screen
      .getAllByTestId(/^ashare-entry-/)
      .map((el) => el.getAttribute("data-testid")!.replace("ashare-entry-", ""));
    expect(rendered.sort()).toEqual([...steps].sort());
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { LLMBudgetPanel } from "@/components/system/llm-budget-panel";

// === mock useApiPage =================================
const mockApiData: Record<string, unknown> = {};
vi.mock("@/lib/hooks/use-api-page", () => ({
  useApiPage: () => ({
    useApi: (url: string) => ({
      data: mockApiData[url] ?? null,
      error: null,
      isLoading: false,
      mutate: vi.fn(),
    }),
  }),
}));

const mockPutLLMBudgetSettings = vi.fn(
  async (_cfg: Record<string, unknown>) => undefined,
);
const mockGetLLMUsage = vi.fn(async () => usageState());
vi.mock("@/lib/api/settings", () => ({
  putLLMBudgetSettings: (cfg: Record<string, unknown>) =>
    mockPutLLMBudgetSettings(cfg),
  getLLMUsage: () => mockGetLLMUsage(),
}));

function usageState(over: Record<string, unknown> = {}) {
  return {
    day: "2026-10-21",
    tokens_today: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    calls_today: 0,
    daily_tokens: 0,
    exceeded: false,
    remaining: null,
    by_source: {},
    ...over,
  };
}

function setBudget(dailyTokens: number) {
  mockApiData["/api/v1/settings"] = { llm_budget: { daily_tokens: dailyTokens } };
}

function setUsage(over: Record<string, unknown> = {}) {
  mockApiData["/api/v1/settings/llm-usage"] = usageState(over);
}

describe("LLMBudgetPanel", () => {
  beforeEach(() => {
    mockApiData["/api/v1/settings"] = null;
    mockApiData["/api/v1/settings/llm-usage"] = null;
    mockPutLLMBudgetSettings.mockClear();
    mockGetLLMUsage.mockClear();
  });

  it("shows the saved ceiling and states 0 = 不设上限", async () => {
    setBudget(0);
    setUsage();

    render(<LLMBudgetPanel />);
    // 0 必须原样显示 —— 被兜底成别的数,用户就看不到自己设的"无上限"
    expect(
      (screen.getByLabelText("每日上限 (token)") as HTMLInputElement).value,
    ).toBe("0");
    expect(screen.getByText("0 = 不设上限")).toBeTruthy();
    // 未设上限时不显示 "/ 上限" 尾巴,也不显示超限横幅
    expect(screen.queryByText(/^\//)).toBeNull();
    expect(screen.queryByText(/已达当日上限/)).toBeNull();
  });

  it("saves exactly { llm_budget: { daily_tokens: N } } and applies next tick", async () => {
    setBudget(0);
    setUsage();

    render(<LLMBudgetPanel />);
    const input = screen.getByLabelText("每日上限 (token)") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "250000" } });
    screen.getByRole("button", { name: "保存" }).click();

    await waitFor(() => expect(mockPutLLMBudgetSettings).toHaveBeenCalled());
    // 载荷形状即契约:后端 PUT 只认 llm_budget 这一节
    expect(mockPutLLMBudgetSettings.mock.calls[0][0]).toEqual({
      daily_tokens: 250000,
    });
    expect(await screen.findByText("已保存,下一 tick 起生效")).toBeTruthy();
  });

  it("clamps a negative ceiling to 0 rather than sending a negative cap", async () => {
    setBudget(1000);
    setUsage({ tokens_today: 400, daily_tokens: 1000, remaining: 600 });

    render(<LLMBudgetPanel />);
    const input = screen.getByLabelText("每日上限 (token)") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "-3" } });
    screen.getByRole("button", { name: "保存" }).click();

    await waitFor(() => expect(mockPutLLMBudgetSettings).toHaveBeenCalled());
    expect(mockPutLLMBudgetSettings.mock.calls[0][0]).toEqual({ daily_tokens: 0 });
  });

  it("shows the pause banner only once the ceiling is reached", async () => {
    setBudget(1000);
    setUsage({
      tokens_today: 1000,
      daily_tokens: 1000,
      exceeded: true,
      remaining: 0,
      calls_today: 12,
      by_source: { agent_chat: { calls: 8, total_tokens: 600 } },
    });

    render(<LLMBudgetPanel />);
    const banner = await screen.findByText(/已达当日上限/);
    // 横幅必须说清边界:拦的是无人值守,对话仍可用
    expect(banner.textContent).toContain("对话仍可用");
    expect(screen.getByText("1.0k token")).toBeTruthy();
    expect(screen.getByText("/ 1.0k")).toBeTruthy();
  });

  it("the refresh button reads usage from the API instead of recomputing locally", async () => {
    setBudget(0);
    setUsage();

    render(<LLMBudgetPanel />);
    screen.getByRole("button", { name: "刷新" }).click();

    await waitFor(() => expect(mockGetLLMUsage).toHaveBeenCalledTimes(1));
  });

  it("refuses to save until the saved settings have loaded", async () => {
    // data === null:按下保存不得发 PUT,否则会用兜底 0 覆盖用户已有的上限
    setUsage();

    render(<LLMBudgetPanel />);
    expect(
      (screen.getByRole("button", { name: "保存" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});

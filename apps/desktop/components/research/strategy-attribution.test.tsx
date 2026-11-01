import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import {
  AttributionBlock,
  RuleBatchAttributionButton,
  loadSuggestion,
} from "@/components/research/strategy-attribution";

const { fetchBareMock } = vi.hoisted(() => ({ fetchBareMock: vi.fn() }));
const { ApiErrorMock } = vi.hoisted(() => ({
  ApiErrorMock: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

vi.mock("@/lib/api-client", () => ({
  ApiError: ApiErrorMock,
  fetchBare: fetchBareMock,
}));

const CATEGORIES = {
  categories: [
    { id: "1", code: "data", content: "数据问题", first_seen_at: "2026-09-25T00:00:00+00:00", occurrence_count: 1 },
    { id: "2", code: "assumption", content: "假设失效", first_seen_at: "2026-09-25T00:00:00+00:00", occurrence_count: 1 },
    { id: "3", code: "strategy", content: "策略逻辑失效", first_seen_at: "2026-09-25T00:00:00+00:00", occurrence_count: 1 },
    { id: "4", code: "regime_shift", content: "市场状态切换", first_seen_at: "2026-09-25T00:00:00+00:00", occurrence_count: 1 },
    { id: "5", code: "execution", content: "执行/配置问题", first_seen_at: "2026-09-25T00:00:00+00:00", occurrence_count: 1 },
  ],
};

beforeEach(() => {
  fetchBareMock.mockReset();
});

describe("AttributionBlock — replay 学到行二态", () => {
  it("drift=true → bundle 级 rule 徽章,不查单笔行", () => {
    render(<AttributionBlock strategyId="st-1" episodeN={1} drift={true} />);
    expect(screen.getByText(/已归因: execution\(rule\)/)).toBeInTheDocument();
    expect(fetchBareMock).not.toHaveBeenCalled();
  });

  it("已有归因行 → 徽章态显示分类文案 + suggested_by,不再调 suggest", async () => {
    fetchBareMock
      .mockResolvedValueOnce(CATEGORIES)
      .mockResolvedValueOnce({ attribution: { category_code: "strategy", suggested_by: "director", rationale: "逻辑失效" } });
    render(<AttributionBlock strategyId="st-1" episodeN={2} drift={false} />);
    await waitFor(() => expect(screen.getByText(/已归因: 策略逻辑失效\(director\)/)).toBeInTheDocument());
    expect(fetchBareMock).toHaveBeenCalledTimes(2); // categories + attribution 行,无 suggest
  });

  it("无归因 + LLM 建议可用 → 预选建议分类 + suggested_by=llm 标注", async () => {
    // categories 是模块级 session 缓存(前测已填充),Once 队列会错位 — 按 path 分流
    fetchBareMock.mockImplementation((path: string) => {
      if (path.endsWith("/categories")) return Promise.resolve(CATEGORIES);
      if (path.endsWith("/suggest"))
        return Promise.resolve({ suggestion: { category_code: "data", rationale: "K线缺口", confidence: 0.7 } });
      return Promise.reject(new ApiErrorMock("404", 404)); // attribution 行 404 = 无归因
    });
    render(<AttributionBlock strategyId="st-1" episodeN={3} drift={false} />);
    await waitFor(() => expect(screen.getByText(/✓ 数据问题/)).toBeInTheDocument());
    expect(screen.getByText(/已采纳 LLM 建议/)).toBeInTheDocument();

    fetchBareMock.mockResolvedValueOnce({ attribution: { category_code: "data", suggested_by: "llm", rationale: "K线缺口" } });
    fireEvent.click(screen.getByRole("button", { name: /确认归因/ }));
    await waitFor(() => expect(screen.getByText(/已归因: 数据问题\(llm\)/)).toBeInTheDocument());
    const call = fetchBareMock.mock.calls.filter((c) => c[0].endsWith("/attribution") && !c[0].endsWith("/suggest")).pop();
    expect(call?.[1]?.method).toBe("POST");
  });

  it("LLM 不可用(suggestion=null) → 按钮组仍可手选(suggested_by=director)", async () => {
    fetchBareMock
      .mockResolvedValueOnce(CATEGORIES)
      .mockRejectedValueOnce(new ApiErrorMock("404", 404))
      .mockResolvedValueOnce({ suggestion: null, reason: "llm_unavailable" });
    render(<AttributionBlock strategyId="st-1" episodeN={4} drift={false} />);
    await waitFor(() => expect(screen.getByRole("button", { name: /确认归因/ })).toBeInTheDocument());
    // 无预选 ✓ 标记,标注为手选
    expect(screen.queryByText(/已采纳 LLM 建议/)).not.toBeInTheDocument();
    expect(screen.getByText(/手选归因/)).toBeInTheDocument();
  });

  it("暂不归因 → 收起面板,不落行", async () => {
    fetchBareMock
      .mockResolvedValueOnce(CATEGORIES)
      .mockRejectedValueOnce(new ApiErrorMock("404", 404))
      .mockResolvedValueOnce({ suggestion: null, reason: "llm_unavailable" });
        render(<AttributionBlock strategyId="st-1" episodeN={5} drift={false} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "暂不归因" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "暂不归因" }));
    expect(screen.getByText(/暂不归因 — 该笔不计入归因统计/)).toBeInTheDocument();
  });
});

describe("RuleBatchAttributionButton — workspace rule 批量面", () => {
  it("点击后显示 execution(rule) 徽章 + inserted 笔数", async () => {
    fetchBareMock.mockResolvedValueOnce({ inserted: [1, 2], rule_episode_ns: [1, 2] });
        render(<RuleBatchAttributionButton strategyId="st-1" />);
    fireEvent.click(screen.getByRole("button", { name: /批量归因 · rule/ }));
    await waitFor(() => expect(screen.getByText(/已归因: execution\(rule\) · 2 笔/)).toBeInTheDocument());
    const call = fetchBareMock.mock.calls[0];
    expect(call[0]).toContain("/attribution/rules");
    expect(call[1]?.method).toBe("POST");
  });

  it("失败 → 错误提示,不进徽章态", async () => {
    fetchBareMock.mockRejectedValueOnce(new ApiErrorMock("422", 422));
        render(<RuleBatchAttributionButton strategyId="st-1" />);
    fireEvent.click(screen.getByRole("button", { name: /批量归因 · rule/ }));
    await waitFor(() => expect(screen.getByText(/批量归因失败/)).toBeInTheDocument());
  });
});

describe("loadSuggestion — session 缓存(plan §2.4 OQ-2)", () => {
  it("同 (strategyId, episodeN) 二次调用命中缓存,不重复调 suggest", async () => {
    fetchBareMock.mockResolvedValue({ suggestion: { category_code: "data", rationale: "x", confidence: 0.5 } });
    await loadSuggestion("st-cache", 7);
    await loadSuggestion("st-cache", 7);
    expect(fetchBareMock).toHaveBeenCalledTimes(1);
    expect(fetchBareMock.mock.calls[0][0]).toContain("/state/7/attribution/suggest");
  });

  it("建议为 null(LLM 不可用)也缓存,二次调用不再打端点", async () => {
    fetchBareMock.mockResolvedValue({ suggestion: null, reason: "llm_unavailable" });
    await loadSuggestion("st-cache-null", 8);
    await loadSuggestion("st-cache-null", 8);
    expect(fetchBareMock).toHaveBeenCalledTimes(1);
  });
});

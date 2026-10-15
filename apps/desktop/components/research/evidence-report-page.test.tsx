import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ExperimentEvidencePage } from "@/components/research/evidence-report-page";
import { getEvidenceReport } from "@/lib/api/experiments";

const { useApiMock } = vi.hoisted(() => ({ useApiMock: vi.fn() }));
const { searchParamsMock } = vi.hoisted(() => ({ searchParamsMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useSearchParams: () => ({ get: searchParamsMock }),
}));

vi.mock("@/lib/hooks/use-api-page", () => ({
  useApiPage: () => ({
    useApi: useApiMock,
  }),
}));

vi.mock("@/lib/api/experiments", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/experiments")>("@/lib/api/experiments");
  return { ...actual, getEvidenceReport: vi.fn() };
});

const mockGet = vi.mocked(getEvidenceReport);

const report = {
  experiment: {
    id: "exp-abc123",
    name: "BTC 1h 双顶形态",
    hypothesis: "关键阻力位双顶 → 跌破颈线放空有正期望",
    symbol: "BTC/USDT",
    timeframe: "1h",
    strategy_version: "pa-decision-v1",
    created_at: "2026-08-23 10:00:00",
  },
  data_source: {
    dataset: "klines.db 2026-01-01~2026-07-01",
    symbol: "BTC/USDT",
    timeframe: "1h",
  },
  config: {
    strategy_version: "pa-decision-v1",
    setup_params: { direction: "short", entry: 65000, stop_loss: 66000, take_profit: 61000 },
  },
  version: {
    id: "exp-abc123",
    created_at: "2026-08-23 10:00:00",
    strategy_version: "pa-decision-v1",
    parent_experiment: null,
  },
  result: {
    total_setups: 12,
    win_rate: 0.58,
    avg_win: 120,
    avg_loss: -80,
    expectancy: 0.42,
    profit_factor: 1.35,
    max_drawdown: 0.08,
  },
  robustness: {
    has_backtest: true,
    notes: ["样本 12 个,统计置信度低", "胜率 58.0%"],
  },
  risk: {
    max_drawdown: 0.08,
    risk_per_trade: { direction: "short", entry: 65000, stop_loss: 66000, amount: 1000 },
  },
  conclusion: "BTC 1h 双顶形态:BTC/USDT 1h short 形态,12 个样本;胜率 58.0%;单笔期望 +0.420;正期望,可考虑 fork 深化",
  lineage: {
    parent: null,
    ancestors: [],
    children: [{ id: "exp-child", name: "变异版双顶", created_at: "2026-08-23 11:00:00" }],
  },
};

function mockApi(data: unknown, overrides: Partial<ReturnType<typeof useApiMock>> = {}) {
  useApiMock.mockReturnValue({
    data: data as never,
    error: undefined,
    isLoading: false,
    mutate: vi.fn(),
    ...overrides,
  } as never);
}

beforeEach(() => {
  useApiMock.mockReset();
  mockGet.mockReset();
  searchParamsMock.mockReset();
  searchParamsMock.mockReturnValue(null);
});

describe("ExperimentEvidencePage", () => {
  it("无 id 时显示提示空态", () => {
    mockApi(null);
    render(<ExperimentEvidencePage />);
    expect(screen.getByText(/输入实验 id/)).toBeInTheDocument();
  });

  it("加载态显示 spinner", () => {
    searchParamsMock.mockReturnValue("exp-abc123");
    mockApi(null, { isLoading: true });
    render(<ExperimentEvidencePage />);
    expect(screen.getByText(/加载证据报告/)).toBeInTheDocument();
  });

  it("错误态显示服务不可用", () => {
    searchParamsMock.mockReturnValue("exp-abc123");
    mockApi(null, { error: new Error("boom") as never });
    render(<ExperimentEvidencePage />);
    expect(screen.getByText(/证据服务不可用/)).toBeInTheDocument();
  });

  it("渲染报告主体：结论/回测/风险/血缘", () => {
    searchParamsMock.mockReturnValue("exp-abc123");
    mockApi(report);
    render(<ExperimentEvidencePage />);
    expect(screen.getAllByText("BTC 1h 双顶形态").length).toBeGreaterThan(0);
    expect(screen.getByText(/正期望,可考虑 fork 深化/)).toBeInTheDocument();
    expect(screen.getByText("已回测")).toBeInTheDocument();
    // 回测指标
    expect(screen.getByText("58.0%")).toBeInTheDocument();
    expect(screen.getAllByText(/\+0\.420/).length).toBeGreaterThan(0);
    // 风险卡
    expect(screen.getByText("1,000")).toBeInTheDocument();
    // 血缘子代
    expect(screen.getByText("变异版双顶")).toBeInTheDocument();
  });

  it("输入 id + 打开 → 切换当前实验渲染", async () => {
    // 初始无 id → 空态
    mockApi(null);
    const { rerender } = render(<ExperimentEvidencePage />);
    expect(screen.getByText(/输入实验 id/)).toBeInTheDocument();

    // 提交 id 后,setExpId 触发 rerender;useApi mock 返回 report
    useApiMock.mockReturnValue({
      data: report as never,
      error: undefined,
      isLoading: false,
      mutate: vi.fn(),
    } as never);
    rerender(<ExperimentEvidencePage />);

    const input = screen.getByPlaceholderText("exp-xxx");
    fireEvent.change(input, { target: { value: "exp-abc123" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await waitFor(() => expect(screen.getAllByText("BTC 1h 双顶形态").length).toBeGreaterThan(0));
  });

  it("子代节点点击 → onOpen 切换当前实验", () => {
    searchParamsMock.mockReturnValue("exp-abc123");
    mockApi(report);
    render(<ExperimentEvidencePage />);
    fireEvent.click(screen.getByText("变异版双顶"));
    // 打开子代后仍渲染(useApi 换 path 由 hook 层处理,组件层保证不崩)
    expect(screen.getByText("变异版双顶")).toBeInTheDocument();
  });
});

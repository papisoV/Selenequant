import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ExperimentsPage } from "@/components/research/experiments-page";
import { forkExperiment } from "@/lib/api/experiments";
import { deployExperimentToPaper } from "@/lib/api/paper-plans";

const { useApiMock } = vi.hoisted(() => ({ useApiMock: vi.fn() }));

vi.mock("@/lib/hooks/use-api-page", () => ({
  useApiPage: () => ({
    useApi: useApiMock,
  }),
}));

vi.mock("@/lib/api/experiments", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/experiments")>("@/lib/api/experiments");
  return { ...actual, forkExperiment: vi.fn() };
});

vi.mock("@/lib/api/paper-plans", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/paper-plans")>("@/lib/api/paper-plans");
  return { ...actual, deployExperimentToPaper: vi.fn() };
});

const mockFork = vi.mocked(forkExperiment);
const mockDeploy = vi.mocked(deployExperimentToPaper);

const exp = {
  id: "exp-abc123",
  name: "BTC 1h 双顶形态",
  hypothesis: "关键阻力位双顶 → 跌破颈线放空有正期望",
  symbol: "BTC/USDT",
  timeframe: "1h",
  setup_params: { direction: "short", entry: 65000, stop_loss: 66000, take_profit: 61000 },
  backtest_result: { total_setups: 12, win_rate: 0.58 },
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
  mockFork.mockReset();
  mockDeploy.mockReset();
});

describe("ExperimentsPage", () => {
  it("加载态显示 spinner", () => {
    mockApi(null, { isLoading: true });
    render(<ExperimentsPage />);
    expect(screen.getByText(/加载实验/)).toBeInTheDocument();
  });

  it("错误态显示服务不可用", () => {
    mockApi(null, { error: new Error("boom") as never });
    render(<ExperimentsPage />);
    expect(screen.getByText(/实验服务不可用/)).toBeInTheDocument();
  });

  it("空数据 → 空状态提示", () => {
    mockApi([]);
    render(<ExperimentsPage />);
    expect(screen.getByText(/还没有实验/)).toBeInTheDocument();
  });

  it("渲染实验卡片：名称/品种/周期/入场 SL TP", () => {
    mockApi([exp]);
    render(<ExperimentsPage />);
    expect(screen.getByText("BTC 1h 双顶形态")).toBeInTheDocument();
    expect(screen.getByText(/BTC\/USDT/)).toBeInTheDocument();
    expect(screen.getByText(/65,000/)).toBeInTheDocument();
    expect(screen.getByText(/66,000/)).toBeInTheDocument();
    expect(screen.getByText(/61,000/)).toBeInTheDocument();
    expect(screen.getByText("short")).toBeInTheDocument();
  });

  it("卡片含'证据'入口链接到 evidence 页", () => {
    mockApi([exp]);
    render(<ExperimentsPage />);
    const link = screen.getByRole("link", { name: /证据/ }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/research/experiment-evidence?id=exp-abc123");
  });

  it("fork 点击 → 调 forkExperiment 并刷新列表", async () => {
    const mutate = vi.fn().mockResolvedValue(undefined);
    mockApi([exp], { mutate: mutate as never });
    mockFork.mockResolvedValue({ ...exp, id: "exp-child", name: "BTC 1h 双顶形态 (fork)" } as never);

    render(<ExperimentsPage />);
    fireEvent.click(screen.getByRole("button", { name: /fork/i }));
    await waitFor(() => expect(mockFork).toHaveBeenCalledWith("exp-abc123", { name: "BTC 1h 双顶形态 (fork)" }));
    await waitFor(() => expect(mutate).toHaveBeenCalled());
  });

  it("fork 失败 → 显示错误", async () => {
    mockApi([exp]);
    mockFork.mockRejectedValue(new Error("parent missing"));

    render(<ExperimentsPage />);
    fireEvent.click(screen.getByRole("button", { name: /fork/i }));
    await waitFor(() => expect(screen.getByText(/parent missing/)).toBeInTheDocument());
  });

  it("部署到 paper → 调 deployExperimentToPaper 并显示已部署状态", async () => {
    mockApi([exp]);
    mockDeploy.mockResolvedValue({
      id: "plan-xyz",
      experiment_id: exp.id,
      name: "BTC 1h 双顶形态 · paper 跟踪",
      symbol: "BTC/USDT",
      timeframe: "1h",
      direction: "short",
      entry: 65000,
      stop_loss: 66000,
      take_profit: 61000,
      quantity: 0.001,
      account_id: "paper-default",
      status: "planned",
    } as never);

    render(<ExperimentsPage />);
    fireEvent.click(screen.getByRole("button", { name: /部署到 paper/i }));
    await waitFor(() => expect(mockDeploy).toHaveBeenCalledWith("exp-abc123"));
    await waitFor(() => expect(screen.getByText(/已登记 paper 计划/)).toBeInTheDocument());
    expect(screen.getByText(/plan-xyz/)).toBeInTheDocument();
  });

  it("部署失败 → 显示错误", async () => {
    mockApi([exp]);
    mockDeploy.mockRejectedValue(new Error("no complete setup"));

    render(<ExperimentsPage />);
    fireEvent.click(screen.getByRole("button", { name: /部署到 paper/i }));
    await waitFor(() => expect(screen.getByText(/no complete setup/)).toBeInTheDocument());
  });
});

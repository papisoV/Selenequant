import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";

// === mock useApi =================================
const mockApiData: Record<string, unknown> = {};
vi.mock("@/lib/hooks/use-api", () => ({
  useApi: (url: string) => ({ data: mockApiData[url] ?? null, isLoading: false, error: null, mutate: vi.fn() }),
}));

// === mock useStrategyStore =================================
vi.mock("@/lib/store/strategy", () => ({
  useStrategyStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ connected: false, signals: [] }),
}));

// === mock api-client (apiPut) =================================
vi.mock("@/lib/api-client", () => ({ apiPut: vi.fn() }));

// === mock next/navigation =================================
const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(mockSearchParams),
}));

// === mock child components (单元聚焦 workbench 接线) =================================
vi.mock("@/components/strategy/param-panel", () => ({
  ParamPanel: () => <div data-testid="param-panel" />,
}));
vi.mock("@/components/strategy/backtest-results-tabs", () => ({
  BacktestResultsTabs: () => <div data-testid="backtest-tabs" />,
}));
vi.mock("@/components/strategy/runner-monitor", () => ({
  RunnerMonitorPanel: () => <div data-testid="runner-monitor" />,
}));
vi.mock("@/components/strategy/version-timeline", () => ({
  VersionTimeline: () => <div data-testid="version-timeline" />,
}));
// AiSidebar 是 next/dynamic 懒加载,不 mock 会拉 1170 行依赖链
vi.mock("next/dynamic", () => ({
  default: () => () => <div data-testid="ai-sidebar" />,
}));

// === import AFTER mocks =================================
import { StrategyWorkbenchPage } from "@/components/strategy/strategy-workbench";
import type { StrategyData } from "@/components/strategy/types";

let mockSearchParams = "";

const STRATEGY: StrategyData = {
  id: "strat-abc",
  name: "布林带策略",
  type: "custom",
  symbols: ["BTC/USDT"],
  interval: "1d",
  version: 3,
  parameters: {},
};

beforeEach(() => {
  mockSearchParams = "";
  Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("StrategyWorkbenchPage — US-0 详情页实体化", () => {
  it("无 ?id → 可选单空态(列出可进入策略,不再是死文案)", () => {
    mockApiData["/api/v1/strategies"] = {
      strategies: [
        { id: "strat-abc", name: "布林带策略" },
        { id: "strat-def", name: "RSI 策略" },
      ],
    };
    render(<StrategyWorkbenchPage />);
    expect(screen.getByText("选择要打开的策略")).toBeTruthy();
    expect(screen.getByText("布林带策略")).toBeTruthy();
    expect(screen.getByText("RSI 策略")).toBeTruthy();
    // 空态给出的是可点链接,而不是让用户自己拼 URL
    const link = screen.getByText("布林带策略").closest("a");
    expect(link?.getAttribute("href")).toBe(
      "/research/strategies-strategy-id?id=strat-abc",
    );
  });

  it("无 ?id 且无策略 → 引导到策略工厂", () => {
    mockApiData["/api/v1/strategies"] = { strategies: [] };
    render(<StrategyWorkbenchPage />);
    expect(screen.getByText(/暂无策略/)).toBeTruthy();
  });

  it("有 ?id → 渲染策略名 + 四核心件全部挂载", () => {
    mockSearchParams = "?id=strat-abc";
    mockApiData["/api/v1/strategies/strat-abc"] = STRATEGY;
    render(<StrategyWorkbenchPage />);
    expect(screen.getByText("布林带策略")).toBeTruthy();
    expect(screen.getByTestId("param-panel")).toBeTruthy();
    expect(screen.getByTestId("runner-monitor")).toBeTruthy();
    // version-timeline 返回 null(后端 0 版本时不渲染),mock 后恒渲染
    expect(screen.getByTestId("version-timeline")).toBeTruthy();
    expect(screen.getByText("AI 助手")).toBeTruthy();
  });

  it("策略未找到 → 未找到态 + 返回按钮", () => {
    mockSearchParams = "?id=strat-missing";
    render(<StrategyWorkbenchPage />);
    expect(screen.getByText(/未找到策略/)).toBeTruthy();
    expect(screen.getByText("返回")).toBeTruthy();
  });

  it("slot 匹配策略 → 头部显示「运行中」徽章", () => {
    mockSearchParams = "?id=strat-abc";
    mockApiData["/api/v1/strategies/strat-abc"] = STRATEGY;
    mockApiData["/api/v1/strategies/runner/status"] = {
      running: true,
      deployments: [{ deployment_id: "dep-1", strategy_id: "strat-abc" }],
    };
    render(<StrategyWorkbenchPage />);
    expect(screen.getByText("运行中")).toBeTruthy();
  });
});

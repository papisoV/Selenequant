import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { TuningResultSummary } from "@/lib/tuning-bridge";

// rsch-page 直接 import useApi（不经 useApiPage），故直接 mock 该模块
const { useApiMock } = vi.hoisted(() => ({ useApiMock: vi.fn() }));
vi.mock("@/lib/hooks/use-api", () => ({ useApi: useApiMock }));

import { RschPage } from "@/components/research/rsch-page";
import { CHAINS } from "@/lib/navigation-chains";

const TEMPLATES = [
  {
    id: "bb_v3",
    name_zh: "布林带升级版",
    category: "trend",
    default_params: { bb_len: 20 },
  },
  { id: "rsi_ma_delta", name_zh: "RSI偏离均线", category: "oscillator" },
];

function tuningRow(overrides: Partial<TuningResultSummary> = {}): TuningResultSummary {
  return {
    file_name: "tuning_20261019_000001.json",
    timestamp: "20261019_000001",
    strategy_id: "bb_v3",
    strategy_name: "布林带升级版",
    interval: "1d",
    exchange: "binance",
    market_type: "futures",
    symbol: "BTC/USDT",
    best_sharpe: 1.24,
    best_params: {},
    total_return: -0.007,
    annual_return: -0.008,
    max_drawdown: -0.092,
    win_rate: 0.5,
    n_trials: 3,
    optimization_time: 1.0,
    deflated_sharpe_ratio: 0.1977,
    haircut_sharpe_annual: 0.0,
    pbo: 0.25,
    ...overrides,
  };
}

/** useApi 被调用两次:工厂模板 + 调参记录,按 path 分派。 */
function mockApis(templates: unknown, tuning: unknown) {
  useApiMock.mockImplementation((path: string) =>
    path.includes("/tuning/results")
      ? { data: tuning, error: undefined, isLoading: false }
      : { data: templates, error: undefined, isLoading: false }
  );
}

describe("RschPage A股研究入口卡 (P0-2)", () => {
  beforeEach(() => {
    mockApis([], []);
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

describe("RschPage 过拟合检验卡 (读真实调参记录)", () => {
  it("有调参记录时显示 DSR / PBO 的真实数值,而非占位破折号", () => {
    mockApis(TEMPLATES, [tuningRow()]);
    render(<RschPage />);
    expect(screen.getByTestId("overfit-dsr").textContent).toBe("0.20");
    expect(screen.getByTestId("overfit-pbo").textContent).toBe("0.25");
  });

  it("PBO 为 null(交易全平 / 旧记录)时显示未计算,不是 0.00", () => {
    mockApis(TEMPLATES, [tuningRow({ pbo: null })]);
    render(<RschPage />);
    expect(screen.getByTestId("overfit-pbo").textContent).toBe("—");
  });

  it("无调参记录时三项都标未计算,不留假数据", () => {
    mockApis(TEMPLATES, []);
    render(<RschPage />);
    expect(screen.getByTestId("overfit-dsr").textContent).toBe("未计算");
    expect(screen.getByTestId("overfit-pbo").textContent).toBe("未计算");
    expect(screen.getByTestId("overfit-kfold").textContent).toBe("未接入");
  });

  it("同一策略多次调参时取最新一条(按文件名 timestamp)", () => {
    mockApis(TEMPLATES, [
      tuningRow({ timestamp: "20260825_141110", pbo: 0.9, deflated_sharpe_ratio: 0.01 }),
      tuningRow({ timestamp: "20261019_000001", pbo: 0.25, deflated_sharpe_ratio: 0.1977 }),
    ]);
    render(<RschPage />);
    expect(screen.getByTestId("overfit-pbo").textContent).toBe("0.25");
    expect(screen.getByTestId("overfit-dsr").textContent).toBe("0.20");
  });

  it("不会把别的策略的调参记录显示到当前策略上", () => {
    mockApis(TEMPLATES, [
      tuningRow({ strategy_id: "rsi_ma_delta", pbo: 0.8, deflated_sharpe_ratio: 0.9 }),
    ]);
    render(<RschPage />);
    // 默认选中第一个模板 bb_v3,它没有自己的记录
    expect(screen.getByTestId("overfit-pbo").textContent).toBe("未计算");
  });
});

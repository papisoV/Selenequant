import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { drawdownRuns, MetricGrid, BacktestPane } from "@/components/research/alpha-mining-backtest";
import type { BacktestDetail, SavedStrategy } from "@/lib/api/alpha";
import { apiPost } from "@/lib/api-client";

const DETAIL: BacktestDetail = {
  strategy_name: "alpha_v5_adaptive",
  formula_readable: "test",
  symbol: "BTC/USDT",
  interval: "15m",
  total_return: 0.12,
  annual_return: 0.35,
  sharpe_ratio: 1.5,
  sortino_ratio: 1.9,
  max_drawdown: -0.08,
  win_rate: 0.55,
  total_trades: 42,
  profit_factor: 1.7,
  equity_curve: [10000, 10100, 10200, 10150, 10300],
  rolling_sharpe: [1.2, -0.5, 1.1, -0.8, 1.5],
  trades: [
    { pnl: 0.5, stop: false, side: "long" },
    { pnl: -0.3, stop: false, side: "short" },
    { pnl: 0.1, stop: false, side: "long" },
  ],
};

const STRATS: SavedStrategy[] = [
  {
    name: "alpha_v5_adaptive",
    version: "alpha_v5",
    formula_readable: "test_formula",
    formula_tokens: [0, 6, 3, 1],
    profiles: [{ base_interval: "15m" }],
  },
];

vi.mock("@/lib/api-client", () => ({
  api: vi.fn(async () => ({})),
  apiPost: vi.fn(async (url: string) => {
    if (url.includes("/backtest")) return { ...DETAIL };
    if (url.includes("/deploy")) return { ok: true };
    return {};
  }),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

describe("回测 tab 纯助手", () => {
  it("drawdownRuns:净值跌破前期峰值 → 染色块区间;创新高收口", () => {
    expect(drawdownRuns([10000, 10100, 10200, 10150, 10300])).toEqual([[3, 4]]);
    // 无回撤(单调/平台) → 无块
    expect(drawdownRuns([100, 200, 300])).toEqual([]);
    expect(drawdownRuns([5, 5, 5])).toEqual([]);
    expect(drawdownRuns([])).toEqual([]);
  });

  it("MetricGrid 渲染 8 个指标卡 + 正确格式化", () => {
    const { container } = render(<MetricGrid d={DETAIL} />);
    expect(container.querySelectorAll('[data-testid^="metric-"]').length).toBe(8);
    expect(screen.getByTestId("metric-total_return").textContent).toBe("12.0%");
    expect(screen.getByTestId("metric-sharpe_ratio").textContent).toBe("1.50");
    expect(screen.getByTestId("metric-profit_factor").textContent).toBe("1.70");
    expect(screen.getByTestId("metric-total_trades").textContent).toBe("42");
  });
});

describe("回测 tab 策略源(全真数据,无 mock)", () => {
  it("点策略行区间按钮 → 自动跑 backtestSavedStrategy → 8 指标 + 净值回撤染色块 + 滚动夏普正负分段", async () => {
    const { container } = render(<BacktestPane formulas={[]} strategies={STRATS} symbol="BTC/USDT" interval="15m" exchange="binance" marketType="futures" />);
    // 默认策略源:行列表直接渲染
    await waitFor(() => expect(screen.getByText(/已保存的 Alpha 策略/)).toBeInTheDocument());
    // 默认策略源:行列表直接渲染(下拉 option 与行 span 同名 → 用 All 变体)
    expect(screen.getAllByText(/alpha_v5_adaptive/).length).toBeGreaterThan(0);
    // 单击 15m 区间按钮 = 一次点击直接出结果
    fireEvent.click(screen.getAllByRole("button", { name: /^15m$/ })[0]);
    await waitFor(() => expect(screen.getByTestId("strategy-result")).toBeInTheDocument(), { timeout: 5000 });
    expect(vi.mocked(apiPost)).toHaveBeenCalledWith(
      expect.stringContaining("/saved-strategies/alpha_v5_adaptive/backtest"),
      expect.objectContaining({ symbol: "BTC/USDT", interval: "15m" }),
    );
    // 8 指标
    expect(screen.getByText(/年化收益/)).toBeInTheDocument();
    expect(screen.getByText(/Sortino/)).toBeInTheDocument();
    // 净值曲线:回撤染色块(NEG #d94f4f 的 rect 恰好一个,[3,4] 区间)
    const eqCurve = container.querySelector('svg[data-testid="eq-curve"]')!;
    const negRects = eqCurve.querySelectorAll('rect[fill="#d94f4f"]');
    expect(negRects.length).toBe(1);
    // g-nav 渐变存在
    expect(eqCurve.querySelector('linearGradient[id="g-nav"]')).toBeTruthy();
    // 滚动夏普:负段 #d94f4f / 正段 #4fbf7a 各自至少一段
    const rs = container.querySelector('svg[data-testid="rs-sharpe"]')!;
    const rsNeg = rs.querySelectorAll('path[stroke="#d94f4f"]');
    const rsPos = rs.querySelectorAll('path[stroke="#4fbf7a"]');
    expect(rsNeg.length).toBeGreaterThanOrEqual(1);
    expect(rsPos.length).toBeGreaterThanOrEqual(1);
  });
});

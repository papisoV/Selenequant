import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import {
  TOP_FORMULAS_ENDPOINT,
  VALIDATION_ENDPOINT,
  type TopFormulasResult,
  type ValidationReport,
} from "@/lib/api/alpha-mining";
import { AlphaMiningResultsPanel } from "@/components/research/alpha-mining-results-panel";

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

// === mock recharts(jsdom 无布局,ResponsiveContainer 拿不到尺寸,用空壳) ==========
vi.mock("recharts", () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const React = require("react") as typeof import("react");
  const PassThrough = ({ children }: { children?: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children);
  return {
    ResponsiveContainer: PassThrough,
    LineChart: PassThrough,
    Line: PassThrough,
    XAxis: PassThrough,
    YAxis: PassThrough,
    CartesianGrid: PassThrough,
    Tooltip: PassThrough,
    ReferenceLine: PassThrough,
  };
});

const topFixture: TopFormulasResult = {
  symbols: ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
  interval: "4h",
  bars_per_symbol: 150,
  seed: 42,
  eval_budget: 400,
  evaluations_used: 340,
  generations: 15,
  stopped_reason: "stagnation",
  top_formulas: [
    {
      rank: 1,
      readable: "RANK(RANK(FIB2_BREAKOUT_CONFIRM))",
      tokens: [31, 94, 94],
      canonical_hash: "h1",
      valid: true,
      score: 3.605225089332542,
      rank_ic: 0.1238,
      ls_net_sharpe: 3.4814,
      turnover: 0.0833,
      ic_stability: 0.7298,
      subperiod_ic_consistency: 0.75,
      oos_rank_ic: 0.1705,
      oos_ls_net_sharpe: 12.679,
    },
  ],
};

const validationFixture: ValidationReport = {
  generated_at: "2026-08-16T00:14:46.985051+08:00",
  config: {
    n_trials: 340,
    fold_config: { min_train: 30, fold_size: 15, n_folds: 3 },
    thresholds: { oos_ic_min: 0.02, subinterval_agreement_min: 0.7, multi_symbol_agreement_min: 0.6667, dsr_threshold: 0.95 },
    periods_per_year: 252,
    datasets: { "4h": { n_symbols: 3, symbols: ["BTC/USDT", "ETH/USDT", "SOL/USDT"] } },
    data_source: "klines.db",
  },
  formulas: [
    {
      readable: "RANK(RANK(FIB2_BREAKOUT_CONFIRM))",
      tokens: [31, 94, 94],
      canonical_hash: "h1",
      verdict: "SUSPICIOUS",
      decision: "hold",
      reason: "subinterval agreement 0.67 < 0.70; DSR 0.0219 <= 0.95",
      mean_ic: 0.2096,
      rolling_ic: [-0.0089, 0.2667, 0.3712],
      train_mean_ic: -0.0212,
      subinterval_agreement: 0.6667,
      multi_symbol_agreement: 0.6667,
      dsr: 0.0219,
      pbo: null,
      n_oos_obs: 44,
      datasets: {
        "4h": {
          dataset_key: "4h",
          n_dates: 75,
          n_symbols: 3,
          effective_folds: 3,
          train_rank_ic: -0.0212,
          oos_rank_ic: 0.2096,
          subinterval_agreement: 0.6667,
          multi_symbol_agreement: 0.6667,
          oos_ls_sharpe: 14.2755,
          n_oos_obs: 44,
          overfit: {
            verdict: "FAIL - likely overfit / selection-biased: DSR 0.02 < 0.95",
            passed: false,
            observed_sharpe_annual: 2.1414,
            skew: 0.6569,
            kurtosis: 4.3517,
            n_obs: 44,
            n_trials: 340,
            deflated_sharpe_ratio: 0.0219,
            deflation_benchmark_sr0_annual: 6.8387,
            psr_vs_zero: 0.8209,
            haircut_sharpe_annual: 0.0,
            haircut_pct: 1.0,
            minimum_track_record_length: 138.8,
            pbo: null,
          },
          folds: [
            {
              fold_index: 0,
              train_start: 0,
              train_end: 30,
              test_start: 30,
              test_end: 45,
              train_rank_ic: -0.0577,
              train_ls_sharpe: -4.8667,
              train_turnover: 0.25,
              oos_rank_ic: -0.0089,
              oos_ls_sharpe: 25.8559,
              per_symbol_oos_ic: { "BTC/USDT": -0.3712, "ETH/USDT": null, "SOL/USDT": null },
              oos_n_obs: 15,
            },
          ],
        },
      },
    },
  ],
  summary: { SUSPICIOUS: 1, INVALID: 0 },
};

beforeEach(() => {
  Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
});

describe("AlphaMiningResultsPanel", () => {
  it("#1 无产物 → 未发现搜索运行 空状态", async () => {
    render(<AlphaMiningResultsPanel />);
    await waitFor(() => expect(screen.getByText(/未发现搜索运行/)).toBeInTheDocument());
  });

  it("#2 top-formulas + validation → 配置/公式表/判定表/审计卡", async () => {
    mockApiData[TOP_FORMULAS_ENDPOINT] = topFixture;
    mockApiData[VALIDATION_ENDPOINT] = validationFixture;
    render(<AlphaMiningResultsPanel />);
    await waitFor(() => expect(screen.getByText(/Top-N 公式/)).toBeInTheDocument());
    // 搜索任务配置
    expect(screen.getByText(/搜索任务/)).toBeInTheDocument();
    // 公式表:公式可读表达式 + fitness + 判定(表达式同时出现在表格/详情/判定表,用 getAllByText)
    expect(screen.getAllByText("RANK(RANK(FIB2_BREAKOUT_CONFIRM))").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/SUSPICIOUS/).length).toBeGreaterThan(0);
    // 判定表:决策 + 原因
    expect(screen.getByText(/判定表/)).toBeInTheDocument();
    expect(screen.getByText("hold")).toBeInTheDocument();
    expect(screen.getByText(/subinterval agreement 0\.67 < 0\.70/)).toBeInTheDocument();
    // DSR/PBO 审计卡:未通过也展示数值
    expect(screen.getByText(/DSR \/ PBO 审计/)).toBeInTheDocument();
    expect(screen.getByText("FAIL")).toBeInTheDocument();
    expect(screen.getByText("0.022")).toBeInTheDocument();
    expect(screen.getByText("100.0%")).toBeInTheDocument();
  });

  it("#3 仅 top-formulas(验证未生成)→ 公式表渲染 + 未判定 + 验证提示", async () => {
    mockApiData[TOP_FORMULAS_ENDPOINT] = topFixture;
    render(<AlphaMiningResultsPanel />);
    await waitFor(() =>
      expect(screen.getAllByText("RANK(RANK(FIB2_BREAKOUT_CONFIRM))").length).toBeGreaterThan(0)
    );
    expect(screen.getByText("未判定")).toBeInTheDocument();
    expect(screen.getAllByText(/验证报告未生成/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/DSR \/ PBO 审计/)).not.toBeInTheDocument();
  });
});

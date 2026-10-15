import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { ApiError } from "@/lib/api-client";
import { ValidationPane } from "@/components/research/alpha-mining-validation";
import { deployFormula } from "@/lib/api/alpha";
import type {
  DatasetValidation,
  FoldMetrics,
  FormulaValidation,
  IcDecayData,
  MinedFormulaInfo,
  ValidationReportData,
} from "@/lib/api/alpha";

// === mock lib/api/alpha(4 个本 pane 消费的异步函数,均走 hoisted 覆盖对象可逐测切换)===
const { reportOverride, liveOverride, icOverride, deployOverride } = vi.hoisted(() => ({
  reportOverride: { value: undefined as unknown },
  liveOverride: { value: undefined as unknown },
  icOverride: { value: undefined as unknown },
  deployOverride: { value: undefined as { error?: Error } | undefined },
}));

vi.mock("@/lib/api/alpha", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/alpha")>();
  return {
    ...actual,
    getValidationReport: vi.fn(async () => reportOverride.value),
    listFormulas: vi.fn(async () => liveOverride.value ?? []),
    getIcDecay: vi.fn(async () => icOverride.value),
    deployFormula: vi.fn(async () => {
      if (deployOverride.value?.error) throw deployOverride.value.error;
    }),
  };
});

// === fixtures ================================================================
const fold = (i: number): FoldMetrics => ({
  fold_index: i,
  train_start: 0,
  train_end: 100 + i * 50,
  test_start: 100 + i * 50,
  test_end: 150 + i * 50,
  train_rank_ic: 0.031,
  train_ls_sharpe: 1.2,
  train_turnover: 1.8,
  oos_rank_ic: i % 2 === 0 ? 0.022 : -0.008,
  oos_ls_sharpe: 0.62,
  oos_turnover: 2.1,
  per_symbol_oos_ic: { "BTC/USDT": i % 2 === 0 ? 0.03 : -0.01 },
  oos_n_obs: 240,
});

const dataset = (costStress: Record<string, number> | null): DatasetValidation => ({
  dataset_key: "BTC/USDT-1h",
  n_dates: 500,
  n_symbols: 2,
  effective_folds: 5,
  train_rank_ic: 0.031,
  oos_rank_ic: 0.02,
  subinterval_agreement: null,
  multi_symbol_agreement: null,
  oos_ls_sharpe: 0.62,
  oos_turnover: 2.1,
  cost_stress: costStress,
  n_oos_obs: 1200,
  overfit: null,
  solo: {
    "BTC/USDT": {
      symbol: "BTC/USDT",
      ann_ret: 0.14,
      sharpe: 0.9,
      max_drawdown: -0.21,
      verdict: "PASS",
      reason: "",
    },
    "ETH/USDT": {
      symbol: "ETH/USDT",
      ann_ret: -0.06,
      sharpe: -0.1,
      max_drawdown: -0.34,
      verdict: "SUSPICIOUS",
      reason: "换手集中在少数日期,OOS 收益靠 3 天前移",
    },
  },
  folds: [fold(0), fold(1), fold(2), fold(3), fold(4)],
});

const formulaA: FormulaValidation = {
  readable: "rsi_delta",
  tokens: [5, 3, 1, 4],
  canonical_hash: "abc1234def567890",
  verdict: "VALID",
  decision: "accept",
  reason: "去偏后显著且 OOS 稳定",
  mean_ic: 0.02,
  rolling_ic: [0.025, 0.022, 0.019, 0.021, 0.018],
  train_mean_ic: 0.031,
  subinterval_agreement: null,
  multi_symbol_agreement: null,
  dsr: 0.08,
  pbo: null,
  cost_stress: null,
  n_oos_obs: 1200,
  datasets: { "BTC/USDT-1h": dataset({ 2: 0.31, 5: -0.04 }) },
};

const formulaB: FormulaValidation = {
  readable: "fade_peak",
  tokens: [9, 9, 9],
  canonical_hash: "deadbeefcafe1234",
  verdict: "INVALID",
  decision: "reject",
  reason: "OOS 负收益",
  mean_ic: -0.012,
  rolling_ic: [-0.005, -0.01, -0.015, -0.012, -0.018],
  train_mean_ic: 0.005,
  subinterval_agreement: null,
  multi_symbol_agreement: null,
  dsr: 0.02,
  pbo: 0.4,
  cost_stress: null,
  n_oos_obs: 600,
  datasets: { "BTC/USDT-1h": dataset(null) },
};

const makeReport = (): ValidationReportData => ({
  generated_at: "2026-09-12T08:00:00Z",
  config: {
    n_trials: 200,
    fold_config: { min_train: 500, fold_size: 250, n_folds: 5 },
    thresholds: { dsr_threshold: 0.05 },
    periods_per_year: 365,
    datasets: {
      "BTC/USDT-1h": { n_symbols: 2, symbols: ["BTC/USDT", "ETH/USDT"] },
    },
  },
  formulas: [formulaA, formulaB],
});

const live: MinedFormulaInfo[] = [
  {
    formula_id: "f-001",
    formula_tokens: [5, 3, 1, 4],
    formula_readable: "rsi_delta",
    best_score: 0.12,
    created_at: 1700000000,
  },
];

const makeIcDecay = (): IcDecayData => ({
  formula_id: "f-001",
  formula_readable: "rsi_delta",
  symbol: "BTC/USDT",
  interval: "1h",
  source: "real",
  max_lag: 20,
  ic_by_lag: { "1": 0.04, "2": 0.031, "3": 0.022, "5": 0.012, "10": 0.008, "20": 0 },
  half_life: 6,
  peak_ic: 0.04,
  peak_lag: 1,
  turnover: 2.1,
  turnover_adjusted_ic: 0.031,
  is_decayed: false,
});

const makeEnvelope = (): { report: ValidationReportData; generated_at: string | null } => ({
  report: makeReport(),
  generated_at: "2026-09-12T08:00:00Z",
});

beforeEach(() => {
  reportOverride.value = makeEnvelope();
  liveOverride.value = live;
  icOverride.value = makeIcDecay();
  deployOverride.value = undefined;
});

describe("ValidationPane(US-005)", () => {
  it("fold 明细表渲染:训练窗/OOS 窗 + oos_rank_ic(5 折)", async () => {
    render(<ValidationPane />);
    const ready = await screen.findByTestId("vp-ready");
    const tbl = ready.querySelector(".fold-table");
    expect(tbl).toBeTruthy();
    expect(within(tbl as HTMLElement).getByText("训练窗")).toBeTruthy();
    expect(within(tbl as HTMLElement).getByText("OOS 窗")).toBeTruthy();
    // fold 0:挂 train [0, 100),OOS [100, 150),oos_rank_ic +0.022
    expect(within(tbl as HTMLElement).getByText("[100, 150)")).toBeTruthy();
    // oos_rank_ic 偶折 +0.022(0/2/4),奇折 -0.008(1/3)
    expect(within(tbl as HTMLElement).getAllByText("0.022").length).toBe(3);
    expect(within(tbl as HTMLElement).getAllByText("-0.008").length).toBe(2);
    expect(within(tbl as HTMLElement).getAllByText(/^\[0, /).length).toBe(5);
  });

  it("solo 分解:SUSPICIOUS 徽章 + 降级文案(stack 行内 snote)", async () => {
    render(<ValidationPane />);
    const ready = await screen.findByTestId("vp-ready");
    const gov = ready.querySelector(".gov-split");
    const rows = gov!.querySelectorAll(".solo-row");
    expect(rows.length).toBe(2);
    const eth = Array.from(rows).find((r) => r.querySelector(".ss")?.textContent === "ETH/USDT")!;
    expect(eth.querySelector(".verdict")?.className).toContain("susp");
    expect(eth.querySelector(".verdict")?.textContent).toBe("SUSPICIOUS");
    expect(eth.querySelector(".snote")?.textContent).toContain("换手集中在少数日期");
    expect(eth.className).toContain("stack");
  });

  it("治理语义:决策行呈现 decision + reason;deploy 按钮 accept 可点且经 tokens 联结调 deployFormula", async () => {
    render(<ValidationPane />);
    const ready = await screen.findByTestId("vp-ready");
    const dr = ready.querySelector(".decision-row");
    expect(dr!.textContent).toContain("accept");
    expect(dr!.textContent).toContain("去偏后显著且 OOS 稳定");

    const btn = screen.getByTestId("deploy-formula") as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    await waitFor(() => expect(deployFormula).toHaveBeenCalledWith("f-001"));
    expect(await screen.findByText(/已部署/)).toBeTruthy();
  });

  it("deploy 按钮:reject 禁用 + title 含 T4 门控裁定原因(与后端 403 同语义)", async () => {
    render(<ValidationPane />);
    await screen.findByTestId("vp-ready");
    fireEvent.click(screen.getByTestId("formula-row-1"));
    await waitFor(() => {
      const btn = screen.getByTestId("deploy-formula") as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
      expect(btn.title).toContain("reject");
      expect(btn.title).toContain("禁止部署");
      expect(btn.title).toContain("OOS 负收益");
    });
  });

  it("404 引导态:先跑验证管线 + 管线命令,不显示空表", async () => {
    reportOverride.value = Promise.reject(
      new ApiError(
        "No validation report at internal-archive/validation_report.json. Run the T4 validation pipeline first: python -m selene_space_factor_research.mining.report",
        404,
      ),
    );
    render(<ValidationPane />);
    const missing = await screen.findByTestId("vp-missing");
    expect(missing.querySelector(".vp-state")?.textContent).toContain("先跑验证管线");
    expect(missing.querySelector(".vp-cmd")?.textContent).toContain("python -m selene_space_factor_research.mining.report");
    expect(missing.querySelector(".wt-table")).toBeNull();
  });

  it("IC 衰减加载态:报告公式匹配 live formula_id 时显示计算中(不假数据)", async () => {
    icOverride.value = new Promise(() => {});
    render(<ValidationPane />);
    await screen.findByTestId("vp-ready");
    expect(await screen.findByText(/正在计算/)).toBeTruthy();
  });

  it("cost_stress ≥3 档:基线 1× + 2×/5× 档,5× 转负给 honest 脚注", async () => {
    render(<ValidationPane />);
    const ready = await screen.findByTestId("vp-ready");
    const gov = ready.querySelector(".gov-split");
    const rows = gov!.querySelectorAll(".stress-row");
    expect(rows.length).toBe(3);
    expect(Array.from(rows).map((r) => r.querySelector(".xk")?.textContent)).toEqual(["1×", "2×", "5×"]);
    expect(Array.from(rows).map((r) => r.querySelector(".xv")?.textContent)).toEqual(["0.62", "0.31", "-0.04"]);
    const foot = gov!.querySelector(".gov-foot");
    expect(foot?.textContent).toContain("转负");
    expect(foot?.textContent).toContain("5.0×");
  });

  it("cost_stress 未开启:honest 脚注 + 仅基线档", async () => {
    const noStress = makeReport();
    noStress.formulas = [formulaB];
    reportOverride.value = { report: noStress, generated_at: "2026-09-12T08:00:00Z" };
    render(<ValidationPane />);
    const ready = await screen.findByTestId("vp-ready");
    const gov = ready.querySelector(".gov-split");
    expect(gov!.querySelectorAll(".stress-row").length).toBe(1);
    expect(gov!.querySelector(".gov-foot")?.textContent).toContain("未开启成本压力档");
  });
});

import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AshareFactorReportPage } from "./ashare-factor-report-page";

vi.mock("@/lib/api-client", () => ({
  apiDirectRaw: vi.fn(),
  ApiError: class ApiError extends Error {
    constructor(
      message: string,
      readonly status: number,
    ) {
      super(message);
    }
  },
}));

import { apiDirectRaw } from "@/lib/api-client";
const mockApiDirect = vi.mocked(apiDirectRaw);

// factor-report 成功返回裸 dict（IC/分位/相关/时序，无 data/meta envelope）
const REPORT_PAYLOAD = {
  forward_col: "fwd_5d",
  n_days: 120,
  n_symbols: 20,
  ic_report: [
    {
      factor: "RSI_14D",
      IC_mean: 0.0412,
      IR: 0.312,
      IC_pos_rate: 0.58,
      t_stat: 2.11,
      IC_mean_neutral: 0.0288,
      IR_neutral: 0.204,
    },
    {
      factor: "MOMENTUM_1M",
      IC_mean: -0.0153,
      IR: -0.121,
      IC_pos_rate: 0.47,
      t_stat: -1.02,
      IC_mean_neutral: -0.0091,
      IR_neutral: -0.07,
    },
  ],
  quantile_returns: {
    RSI_14D: [
      { quantile: 1, mean: -0.004, std: 0.02, count: 120 },
      { quantile: 2, mean: -0.001, std: 0.02, count: 120 },
      { quantile: 3, mean: 0.001, std: 0.02, count: 120 },
      { quantile: 4, mean: 0.003, std: 0.02, count: 120 },
      { quantile: 5, mean: 0.008, std: 0.02, count: 120 },
    ],
  },
  factor_correlation: {
    RSI_14D: { RSI_14D: 1, MOMENTUM_1M: -0.23 },
    MOMENTUM_1M: { RSI_14D: -0.23, MOMENTUM_1M: 1 },
  },
  ic_series: {
    dates: ["2024-01-02", "2024-01-03", "2024-01-04"],
    series: [
      { name: "RSI_14D", values: [0.02, -0.01, 0.05] },
      { name: "MOMENTUM_1M", values: [-0.03, 0.01, -0.02] },
    ],
  },
  ic_all_nan: false,
};

// 小宇宙：IC 全 NaN（样本 < 10），时序降级为空
const EMPTY_REPORT_PAYLOAD = {
  forward_col: "fwd_5d",
  n_days: 120,
  n_symbols: 6,
  ic_report: [{ factor: "RSI_14D", IC_mean: null, IR: null }],
  quantile_returns: {},
  factor_correlation: {},
  ic_series: { dates: [], series: [] },
  ic_all_nan: true,
  ic_all_nan_reason: "每个截面的可用股票数不足 10（calc_ic 门槛），IC 无法计算；请扩大股票池或放宽 start_date",
};

describe("AshareFactorReportPage", () => {
  beforeEach(() => {
    mockApiDirect.mockReset();
    mockApiDirect.mockResolvedValue(REPORT_PAYLOAD);
  });

  it("生成报告:POST 参数拼装正确,四个分区渲染", async () => {
    render(<AshareFactorReportPage />);

    fireEvent.click(screen.getByRole("button", { name: "生成 IC 报告" }));

    await waitFor(() => {
      expect(mockApiDirect).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/ashare/factor-report?"),
        { method: "POST" },
      );
    });
    const url = mockApiDirect.mock.calls[0][0] as string;
    expect(url).toContain("symbols=600519%2C000858%2C000001");
    expect(url).toContain("forward_col=fwd_5d");
    expect(url).toContain("neutralize=true");

    // 四个分区标题：IC 时序 / IC 统计 / 分位多空 / 相关矩阵
    await waitFor(() => expect(screen.getByText("IC 时序")).toBeTruthy());
    expect(screen.getByText("IC 统计")).toBeTruthy();
    expect(screen.getByText("分位多空（五分位平均前向收益）")).toBeTruthy();
    expect(screen.getByText("因子相关矩阵")).toBeTruthy();

    // 概览四格
    expect(screen.getByText("20")).toBeTruthy(); // n_symbols
    expect(screen.getByText("120")).toBeTruthy(); // n_days
  });

  it("中性化开关:默认并排显示 *_neutral 列", async () => {
    render(<AshareFactorReportPage />);
    fireEvent.click(screen.getByRole("button", { name: "生成 IC 报告" }));

    await waitFor(() => expect(screen.getByText("IC均值(中性)")).toBeTruthy());
    expect(screen.getByText("IR(中性)")).toBeTruthy();
  });

  it("中性化关闭:请求带 neutralize=false,不渲染中性列", async () => {
    render(<AshareFactorReportPage />);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "生成 IC 报告" }));

    await waitFor(() => expect(mockApiDirect).toHaveBeenCalled());
    expect(mockApiDirect.mock.calls[0][0]).toContain("neutralize=false");
    await waitFor(() => expect(screen.getByText("IC 统计")).toBeTruthy());
    expect(screen.queryByText("IC均值(中性)")).toBeNull();
  });

  it("股票池分支:选择沪深300时以 pool 参数请求而非 symbols", async () => {
    render(<AshareFactorReportPage />);
    fireEvent.change(screen.getByDisplayValue("自定义代码"), {
      target: { value: "hs300" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成 IC 报告" }));

    await waitFor(() => expect(mockApiDirect).toHaveBeenCalled());
    const url = mockApiDirect.mock.calls[0][0] as string;
    expect(url).toContain("pool=hs300");
    expect(url).not.toContain("symbols=");
  });

  it("空数据降级:IC 全 NaN 时显示原因,时序区提示无数据而非空图", async () => {
    mockApiDirect.mockResolvedValue(EMPTY_REPORT_PAYLOAD);
    render(<AshareFactorReportPage />);

    fireEvent.click(screen.getByRole("button", { name: "生成 IC 报告" }));

    await waitFor(() => {
      expect(screen.getByTestId("ic-all-nan-note")).toBeTruthy();
    });
    expect(screen.getByText(/不足 10/)).toBeTruthy();
    expect(screen.getByText("暂无 IC 时序数据")).toBeTruthy();
    expect(screen.getByText("暂无分位数据")).toBeTruthy();
    expect(screen.getByText("暂无相关矩阵")).toBeTruthy();
  });

  it("error 分支:mock reject 时错误信息显示,不渲染分区", async () => {
    mockApiDirect.mockRejectedValue(new Error("HTTP 500 UPSTREAM_ERROR"));
    render(<AshareFactorReportPage />);

    fireEvent.click(screen.getByRole("button", { name: "生成 IC 报告" }));

    await waitFor(() => {
      expect(screen.getByText("HTTP 500 UPSTREAM_ERROR")).toBeTruthy();
    });
    expect(screen.queryByText("IC 统计")).toBeNull();
    expect(screen.queryByText("IC 时序")).toBeNull();
  });

  it("刷新缓存:运行后才出现;用上次成功运行的参数打 refresh 端点", async () => {
    render(<AshareFactorReportPage />);
    // 运行前无刷新按钮（无 lastParams，刷了也驱逐不到键）
    expect(screen.queryByTestId("factor-report-refresh")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "生成 IC 报告" }));
    await waitFor(() => expect(mockApiDirect).toHaveBeenCalledTimes(1));
    const runUrl = mockApiDirect.mock.calls[0][0] as string;
    const runQuery = runUrl.split("?")[1];

    // 运行后出现刷新按钮
    const btn = await screen.findByTestId("factor-report-refresh");
    mockApiDirect.mockResolvedValue({ rebuilt: true, report: REPORT_PAYLOAD });
    fireEvent.click(btn);

    await waitFor(() => expect(mockApiDirect).toHaveBeenCalledTimes(2));
    const refreshUrl = mockApiDirect.mock.calls[1][0] as string;
    expect(refreshUrl).toContain("/api/v1/ashare/factor-report/refresh?");
    // 关键：刷新必须重放上次运行的参数，否则驱逐的是另一个缓存键
    expect(refreshUrl.split("?")[1]).toBe(runQuery);
  });

  it("刷新用上一次的参数,不受中途表单改动影响", async () => {
    render(<AshareFactorReportPage />);
    fireEvent.click(screen.getByRole("button", { name: "生成 IC 报告" }));
    await waitFor(() => expect(mockApiDirect).toHaveBeenCalledTimes(1));
    const runQuery = (mockApiDirect.mock.calls[0][0] as string).split("?")[1];

    // 改了表单但没重跑 —— 刷新仍须用上次运行那组参数
    fireEvent.change(screen.getByDisplayValue("600519,000858,000001"), {
      target: { value: "000001,000002" },
    });
    mockApiDirect.mockResolvedValue({ rebuilt: true, report: REPORT_PAYLOAD });
    fireEvent.click(screen.getByTestId("factor-report-refresh"));

    await waitFor(() => expect(mockApiDirect).toHaveBeenCalledTimes(2));
    expect((mockApiDirect.mock.calls[1][0] as string).split("?")[1]).toBe(runQuery);
  });

  it("运行中显示已用秒数进度（60s 冷建不再是无反馈的禁用按钮）", async () => {
    let resolveIt: (v: unknown) => void = () => {};
    mockApiDirect.mockImplementation(
      () => new Promise((r) => { resolveIt = r; }),
    );
    render(<AshareFactorReportPage />);
    fireEvent.click(screen.getByRole("button", { name: "生成 IC 报告" }));

    const prog = await screen.findByTestId("factor-report-progress");
    expect(prog.textContent).toContain("约 1 分钟");
    expect(prog.textContent).toMatch(/已 \d+s/);
    // 计数器每秒推进（真实等待 ~1.2s，避开假时钟与 waitFor 的冲突）
    await new Promise((r) => setTimeout(r, 1200));
    await waitFor(() => expect(prog.textContent).toMatch(/已 [1-9]\d*s/));

    resolveIt(REPORT_PAYLOAD);
  });
});

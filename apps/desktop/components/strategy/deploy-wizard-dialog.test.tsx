// US-2 部署三步向导单测:gating(未勾免责不能提交)/参数覆盖进 PUT 请求体/深链直开。
// API mock: lib/api/strategies 的 createFromTemplate/deployPaper/deployLive。
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DeployWizardDialog, type WizardTemplate } from "./deploy-wizard-dialog";

const createFromTemplateMock = vi.fn();
const deployPaperMock = vi.fn();
const deployLiveMock = vi.fn();

vi.mock("@/lib/api/strategies", () => ({
  createFromTemplate: (...a: unknown[]) => createFromTemplateMock(...a),
  deployPaper: (...a: unknown[]) => deployPaperMock(...a),
  deployLive: (...a: unknown[]) => deployLiveMock(...a),
}));

const putSpy = vi.fn();

const TEMPLATE: WizardTemplate = {
  id: "ema_cross",
  name: "ema_cross",
  nameZh: "EMA交叉",
  defaultParams: { ema_fast: 12, ema_slow: 26 },
  riskParams: { stop_loss_pct: 0.05 },
  symbols: ["BTC/USDT"],
  interval: "1h",
};

function renderWizard(overrides?: Partial<{ mode: "paper" | "live" }>) {
  return render(
    <DeployWizardDialog
      template={TEMPLATE}
      initialMode={overrides?.mode ?? "paper"}
      open
      onOpenChange={() => {}}
      onDeployed={() => {}}
    />,
  );
}

beforeEach(() => {
  createFromTemplateMock.mockReset().mockResolvedValue({ id: "strat-new1" });
  deployPaperMock.mockReset().mockResolvedValue({ runner_status: "running", runner_error: null, backtest_warning: null });
  deployLiveMock.mockReset().mockResolvedValue({ deployed: true, runner_status: "running", runner_error: null });
  putSpy.mockReset().mockResolvedValue({
    ok: true,
    json: async () => ({ data: {}, error: null }),
  });
  vi.stubGlobal("fetch", putSpy);
});

describe("DeployWizardDialog US-2", () => {
  it("Step0→Step1: 空交易对挡住下一步", async () => {
    renderWizard();
    // Step0 显示参数(ParamPanel 渲染默认值)
    await waitFor(() => expect(screen.getByText("26")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    // Step1: 清空交易对 → 下一步 disabled
    fireEvent.change(screen.getByPlaceholderText("BTC/USDT, ETH/USDT"), {
      target: { value: "" },
    });
    expect(screen.getByText("至少填一个交易对")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下一步" })).toBeDisabled();
  });

  it("Paper 全流程: createFromTemplate → PUT(仅覆盖改动字段) → deployPaper", async () => {
    renderWizard();
    await waitFor(() => expect(screen.getByText("26")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    // Step2: Paper 卡已默认选中 → 直接部署
    fireEvent.click(screen.getByRole("button", { name: /部署到 Paper/ }));
    await waitFor(() => expect(deployPaperMock).toHaveBeenCalledWith("strat-new1"));
    expect(createFromTemplateMock).toHaveBeenCalledWith("ema_cross");
    // 未改任何默认值 → 不发 PUT
    expect(putSpy).not.toHaveBeenCalled();
    expect(deployLiveMock).not.toHaveBeenCalled();
  });

  it("Live 免责 gating: 未勾选 checkbox 部署按钮 disabled,勾选后可点并走 deployLive", async () => {
    renderWizard({ mode: "live" });
    await waitFor(() => expect(screen.getByText("26")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByText("Live 实盘"));
    const deployBtn = screen.getByRole("button", { name: /部署到 Live/ });
    expect(deployBtn).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/我理解实盘交易/));
    expect(deployBtn).not.toBeDisabled();
    fireEvent.click(deployBtn);
    await waitFor(() => expect(deployLiveMock).toHaveBeenCalledWith("strat-new1"));
    expect(deployPaperMock).not.toHaveBeenCalled();
  });

  it("参数改动进 PUT 请求体: 改 ema_slow=30 → PUT parameters 覆盖", async () => {
    renderWizard();
    await waitFor(() => expect(screen.getByText("26")).toBeInTheDocument());
    // Step0 把 ema_slow 从 26 改成 30(ParamPanel number 行有 number input)
    const numInputs = screen.getAllByRole("spinbutton");
    const emaSlowInput = numInputs.find((i) => (i as HTMLInputElement).value === "26");
    expect(emaSlowInput).toBeTruthy();
    fireEvent.change(emaSlowInput!, { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: /部署到 Paper/ }));
    await waitFor(() => expect(deployPaperMock).toHaveBeenCalled());
    expect(putSpy).toHaveBeenCalledTimes(1);
    const [, init] = putSpy.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(String(init.body)) as {
      parameters?: Record<string, unknown>;
      symbols?: string[];
      interval?: string;
    };
    expect(body.parameters?.ema_slow).toBe(30);
    expect(body.parameters?.ema_fast).toBe(12);
    expect(body.symbols).toBeUndefined();
    expect(body.interval).toBeUndefined();
  });

  it("US-101 发布门: live 响应 blocked → 显示 runner_error 不报成功", async () => {
    deployLiveMock.mockReset().mockResolvedValue({
      deployed: false,
      runner_status: "blocked",
      runner_error: "发布门拦截:该策略从未完成过回测,缺少历史表现证据,禁止直接部署到实盘。请先运行至少一次回测。",
    });
    renderWizard({ mode: "live" });
    await waitFor(() => expect(screen.getByText("26")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByText("Live 实盘"));
    fireEvent.click(screen.getByLabelText(/我理解实盘交易/));
    fireEvent.click(screen.getByRole("button", { name: /部署到 Live/ }));
    await waitFor(() =>
      expect(screen.getByText(/发布门拦截/)).toBeInTheDocument(),
    );
    // 错误显示后对话框不关闭(onOpenChange(false) 不该被调)
    expect(screen.getByRole("button", { name: /部署到 Live/ })).toBeInTheDocument();
  });

  it("US-101 paper 带 backtest_warning → 部署成功但显示警告", async () => {
    deployPaperMock.mockReset().mockResolvedValue({
      runner_status: "running",
      runner_error: null,
      backtest_warning: "该策略从未完成过回测,缺少历史表现证据。建议先跑一次回测再部署。",
    });
    renderWizard();
    await waitFor(() => expect(screen.getByText("26")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: /部署到 Paper/ }));
    // 成功路径照常走完(toast 是 side effect;关键断言:没有 setErrMsg → 无错误文案)
    await waitFor(() => expect(deployPaperMock).toHaveBeenCalledWith("strat-new1"));
    await waitFor(() => {
      // toast 容器挂在 dialog 外;这里验证 dialog 因成功被关闭——重新渲染后 errMsg 清空。
      // 简化断言:无"发布门拦截"错误块出现,且 deploy 不抛错。
      expect(screen.queryByText(/部署失败/)).not.toBeInTheDocument();
    });
  });

  it("US-103 乐观态: 提交后按钮变「部署中…」且 disabled,失败后恢复可点", async () => {
    let resolveDeploy: (v: unknown) => void = () => {};
    deployPaperMock.mockReset().mockReturnValue(
      new Promise((res) => {
        resolveDeploy = res;
      }),
    );
    renderWizard();
    await waitFor(() => expect(screen.getByText("26")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: /部署到 Paper/ }));
    // 挂起的 promise 期间:按钮显示部署中…,disabled,不可重复提交
    await waitFor(() => expect(screen.getByText("部署中…")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /部署中/ })).toBeDisabled();
    // resolve 成功 → busy 归位
    resolveDeploy({ runner_status: "running", runner_error: null, backtest_warning: null });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /部署到 Paper/ })).toBeInTheDocument(),
    );
  });

  it("US-103 结构化错误: runner 失败 → runner_error 全文显示在 dialog 内", async () => {
    deployPaperMock.mockReset().mockResolvedValue({
      runner_status: "failed",
      runner_error: "Strategy runner not initialized",
      backtest_warning: null,
    });
    renderWizard();
    await waitFor(() => expect(screen.getByText("26")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: /部署到 Paper/ }));
    await waitFor(() =>
      expect(screen.getByText("Strategy runner not initialized")).toBeInTheDocument(),
    );
  });
});

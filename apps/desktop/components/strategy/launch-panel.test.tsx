import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LaunchPanel } from "@/components/strategy/launch-panel";
import type { StratTemplate } from "@/components/research/cards/factory-template-card";
import type { DeploymentSummary } from "@/lib/api/strategies";

const templates: StratTemplate[] = [
  { id: "bb-v3", name: "布林带升级版", cat: "Oscillator", sharpe: 1.9, winrate: 62 },
  { id: "dca-grid", name: "DCA网格", cat: "Grid", sharpe: 1.2, winrate: 55 },
];

const deployments: DeploymentSummary[] = [
  { id: "d1", strategy_id: "s1", strategy_name: "布林带升级版", status: "running", mode: "paper" },
  { id: "d2", strategy_id: "s2", strategy_name: "DCA网格", status: "stopped", mode: "paper" },
];

function setup(overrides: Partial<Parameters<typeof LaunchPanel>[0]> = {}) {
  const onDeployPaper = vi.fn().mockResolvedValue(undefined);
  const onDeployLive = vi.fn().mockResolvedValue(undefined);
  render(
    <LaunchPanel
      templates={templates}
      deployments={deployments}
      onDeployPaper={onDeployPaper}
      onDeployLive={onDeployLive}
      {...overrides}
    />,
  );
  return { onDeployPaper, onDeployLive };
}

describe("LaunchPanel(策略广场启动面板)", () => {
  it("渲染标题与策略下拉,且不再渲染投入金额(P0-1)", () => {
    setup();
    expect(screen.getByText("启动机器人")).toBeTruthy();
    expect(screen.getByLabelText("选择策略")).toBeTruthy();
    // P0-1:金额无处下发(部署请求无 capital 字段)且未参与收益预估,
    // 输入框已删除。此断言锁死"不承诺不存在的影响"这一契约。
    expect(screen.queryByLabelText("投入金额")).toBeNull();
    expect(screen.queryByRole("button", { name: "100" })).toBeNull();
    expect(screen.queryByRole("button", { name: "5000" })).toBeNull();
  });

  it("显示运行中数量", () => {
    setup();
    expect(screen.getByText("运行中 1")).toBeTruthy();
  });

  it("空模板时显示空态且按钮禁用", () => {
    setup({ templates: [] });
    expect(screen.getByText("暂无策略模板")).toBeTruthy();
    expect(screen.getByRole("button", { name: "启动 Paper" }).hasAttribute("disabled")).toBe(true);
  });

  it("未选策略时按钮禁用并提示", () => {
    setup();
    expect(screen.getByRole("button", { name: "启动 Paper" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "启动 Live" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("请先选择策略以启动")).toBeTruthy();
  });

  it("选择策略后显示回测参考指标并启用按钮", () => {
    setup();
    fireEvent.change(screen.getByLabelText("选择策略"), { target: { value: "bb-v3" } });
    expect(screen.getByText("回测夏普")).toBeTruthy();
    expect(screen.getByText("1.90")).toBeTruthy();
    expect(screen.getByText("回测胜率")).toBeTruthy();
    expect(screen.getByText("62%")).toBeTruthy();
    expect(screen.getByText("Oscillator")).toBeTruthy();
    expect(screen.getByRole("button", { name: "启动 Paper" }).hasAttribute("disabled")).toBe(false);
    expect(screen.getByRole("button", { name: "启动 Live" }).hasAttribute("disabled")).toBe(false);
  });

  it("点击启动 Paper 调用 onDeployPaper(选中策略)", () => {
    const { onDeployPaper } = setup();
    fireEvent.change(screen.getByLabelText("选择策略"), { target: { value: "dca-grid" } });
    fireEvent.click(screen.getByRole("button", { name: "启动 Paper" }));
    expect(onDeployPaper).toHaveBeenCalledTimes(1);
    expect(onDeployPaper).toHaveBeenCalledWith(templates[1]);
  });

  it("点击启动 Live 调用 onDeployLive(选中策略)", () => {
    const { onDeployLive } = setup();
    fireEvent.change(screen.getByLabelText("选择策略"), { target: { value: "bb-v3" } });
    fireEvent.click(screen.getByRole("button", { name: "启动 Live" }));
    expect(onDeployLive).toHaveBeenCalledWith(templates[0]);
  });

});

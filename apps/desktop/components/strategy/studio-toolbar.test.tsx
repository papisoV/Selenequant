import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { StudioToolbar } from "./studio-toolbar";

// 行为锁: StudioToolbar 的可观察行为(按钮文案/回调/禁用/条件渲染)。
// cleanroom 重写必须保持; 撤掉实现改动 → 本文件必红。

afterEach(cleanup);

function setup(over: Partial<React.ComponentProps<typeof StudioToolbar>> = {}) {
  const props = {
    onSave: vi.fn(),
    onValidate: vi.fn(),
    onRunBacktest: vi.fn(),
    onDeploy: vi.fn(),
    onExport: vi.fn(),
    backtestRunning: false,
    validationResult: null as null | { valid: boolean; errors: string[] },
    ...over,
  };
  render(<StudioToolbar {...props} />);
  return props;
}

describe("StudioToolbar", () => {
  it("渲染全部固定按钮文案", () => {
    setup();
    for (const label of ["保存", "校验", "运行回测", "优化", "部署", "导出"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });

  it("点击触发对应回调", () => {
    const p = setup();
    fireEvent.click(screen.getByText("保存"));
    fireEvent.click(screen.getByText("校验"));
    fireEvent.click(screen.getByText("运行回测"));
    fireEvent.click(screen.getByText("部署"));
    fireEvent.click(screen.getByText("导出"));
    expect(p.onSave).toHaveBeenCalledTimes(1);
    expect(p.onValidate).toHaveBeenCalledTimes(1);
    expect(p.onRunBacktest).toHaveBeenCalledTimes(1);
    expect(p.onDeploy).toHaveBeenCalledTimes(1);
    expect(p.onExport).toHaveBeenCalledTimes(1);
  });

  it("backtestRunning → 运行回测按钮 disabled", () => {
    setup({ backtestRunning: true });
    expect((screen.getByText("运行回测").closest("button") as HTMLButtonElement).disabled).toBe(true);
  });

  it("未部署时不显示『停止部署』", () => {
    setup();
    expect(screen.queryByText("停止部署")).toBeNull();
  });

  it("isDeployed 且有 onStopDeployment → 显示并可点『停止部署』", () => {
    const onStop = vi.fn();
    setup({ isDeployed: true, onStopDeployment: onStop });
    fireEvent.click(screen.getByText("停止部署"));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("deploying → 部署按钮文案变『部署中...』且 disabled", () => {
    setup({ deploying: true });
    const btn = screen.getByText("部署中...").closest("button") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.queryByText("部署")).toBeNull();
  });

  it("isDeployed → 部署按钮 disabled", () => {
    setup({ isDeployed: true, onStopDeployment: vi.fn() });
    expect((screen.getByText("部署").closest("button") as HTMLButtonElement).disabled).toBe(true);
  });

  it("优化按钮恒 disabled(v0.2 占位)", () => {
    setup();
    expect((screen.getByText("优化").closest("button") as HTMLButtonElement).disabled).toBe(true);
  });
});

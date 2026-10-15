import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FeatureGate } from "./feature-gate";
import { useTierStore } from "@/lib/store/tier";

beforeEach(() => {
  useTierStore.setState({ tier: "free" });
});

describe("FeatureGate(功能墙)", () => {
  it("free 级 → 渲染锁('PRO 专享' + 查看演示)", () => {
    render(<FeatureGate feature="arb_pro"><div>ARB 面板</div></FeatureGate>);
    expect(screen.getByText("PRO 专享")).toBeTruthy();
    expect(screen.getByRole("button", { name: "查看演示" })).toBeTruthy();
    expect(screen.queryByText("ARB 面板")).toBeNull();
  });

  it("pro 级 → 直接渲染 children", () => {
    useTierStore.setState({ tier: "pro" });
    render(<FeatureGate feature="arb_pro"><div>ARB 面板</div></FeatureGate>);
    expect(screen.getByText("ARB 面板")).toBeTruthy();
    expect(screen.queryByText("PRO 专享")).toBeNull();
  });

  it("vip 功能 free 级 → 'VIP 专享';pro 级也锁", () => {
    const { unmount } = render(<FeatureGate feature="member_group"><div>会员群</div></FeatureGate>);
    expect(screen.getByText("VIP 专享")).toBeTruthy();
    unmount();
    useTierStore.setState({ tier: "pro" });
    render(<FeatureGate feature="member_group"><div>会员群</div></FeatureGate>);
    expect(screen.getByText("VIP 专享")).toBeTruthy();
    expect(screen.queryByText("会员群")).toBeNull();
  });

  it("点查看演示 → switchDemo 一键切到解锁层级 → children 重渲染 + localStorage 持久", () => {
    render(<FeatureGate feature="arb_pro"><div>ARB 面板</div></FeatureGate>);
    fireEvent.click(screen.getByRole("button", { name: "查看演示" }));
    expect(useTierStore.getState().tier).toBe("pro");
    // 刷新持久:localStorage selenespace:tier:v1 已写 tier:pro(读取走 readInitial)
    expect(window.localStorage.getItem("selenespace:tier:v1")).toContain('"pro"');
    expect(screen.getByText("ARB 面板")).toBeTruthy();
    expect(screen.queryByText("PRO 专享")).toBeNull();
  });

  it("blur 模式锁定时保留 children 但被模糊包裹", () => {
    const { container } = render(
      <FeatureGate feature="arb_pro" lockMode="blur"><div>ARB 面板</div></FeatureGate>,
    );
    const blurred = container.querySelector(".blur-\\[3px\\]");
    expect(blurred).toBeTruthy();
    expect(screen.getByText("PRO 专享")).toBeTruthy();
  });

  it("fallback 存在时优先渲染 fallback 而非锁", () => {
    render(
      <FeatureGate feature="arb_pro" fallback={<div>降级占位</div>}>
        <div>ARB 面板</div>
      </FeatureGate>,
    );
    expect(screen.getByText("降级占位")).toBeTruthy();
    expect(screen.queryByText("PRO 专享")).toBeNull();
  });
});

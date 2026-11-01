import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { WorkspaceGrid } from "./workspace-grid";
import type { WorkspaceLayout } from "@/lib/workspace/workspace-layouts";

// mock panel-registry 避免动态 import 真实 panel(SSR/jsdom 复杂依赖)
// P7: 补 PANEL_LABELS + PANEL_REGISTRY 导出(WorkspaceGrid 现在用它们做类型切换 Select)
vi.mock("./panel-registry", () => ({
  renderPanel: (slot: { id: string }) => (
    <div data-testid={`panel-${slot.id}`}>panel-{slot.id}</div>
  ),
  PANEL_LABELS: {
    alpha_mine: "Alpha 挖矿",
    factors: "因子研究",
    watch_vchart: "专业图表",
    watch_gex: "GEX",
    watch_liqmap: "爆仓热力",
    watch_sig: "信号中心",
    watch_kf: "盯盘主图",
    order_form: "下单",
  },
  PANEL_REGISTRY: {
    alpha_mine: { Component: () => null },
    factors: { Component: () => null },
    watch_vchart: { Component: () => null },
    watch_gex: { Component: () => null },
    watch_liqmap: { Component: () => null },
    watch_sig: { Component: () => null },
    watch_kf: { Component: () => null },
    order_form: { Component: () => null },
  },
}));

const baseLayout: WorkspaceLayout = {
  id: "test",
  name: "测试",
  panels: [
    { id: "alpha_mine" },
    { id: "factors" },
  ],
  columns: 2,
  schemaVersion: 1,
  createdAt: "2026-08-03T00:00:00Z",
  updatedAt: "2026-08-03T00:00:00Z",
};

function getGrid(container: HTMLElement): HTMLElement {
  return container.querySelector(".workspace-grid") as HTMLElement;
}

describe("WorkspaceGrid 渲染", () => {
  it("渲染所有 panel", () => {
    const { container } = render(<WorkspaceGrid layout={baseLayout} />);
    expect(getGrid(container)).toBeInTheDocument();
    expect(screen.getByTestId("panel-alpha_mine")).toBeInTheDocument();
    expect(screen.getByTestId("panel-factors")).toBeInTheDocument();
  });

  it("columns=1 — data-ws-columns=1", () => {
    const layout: WorkspaceLayout = { ...baseLayout, columns: 1 };
    const { container } = render(<WorkspaceGrid layout={layout} />);
    expect(getGrid(container).getAttribute("data-ws-columns")).toBe("1");
  });

  it("columns=3 — data-ws-columns=3", () => {
    const layout: WorkspaceLayout = { ...baseLayout, columns: 3 };
    const { container } = render(<WorkspaceGrid layout={layout} />);
    expect(getGrid(container).getAttribute("data-ws-columns")).toBe("3");
  });

  it("columns 缺省 — 默认 2", () => {
    const layout: WorkspaceLayout = { ...baseLayout, columns: undefined };
    const { container } = render(<WorkspaceGrid layout={layout} />);
    expect(getGrid(container).getAttribute("data-ws-columns")).toBe("2");
  });

  it("columns=0 — clamp 到 1(最小)", () => {
    const layout: WorkspaceLayout = { ...baseLayout, columns: 0 };
    const { container } = render(<WorkspaceGrid layout={layout} />);
    expect(getGrid(container).getAttribute("data-ws-columns")).toBe("1");
  });

  it("columns=99 — clamp 到 3(最大)", () => {
    const layout: WorkspaceLayout = { ...baseLayout, columns: 99 };
    const { container } = render(<WorkspaceGrid layout={layout} />);
    expect(getGrid(container).getAttribute("data-ws-columns")).toBe("3");
  });

  it("空 panels — 不渲染 panel 槽位", () => {
    const layout: WorkspaceLayout = { ...baseLayout, panels: [] };
    const { container } = render(<WorkspaceGrid layout={layout} />);
    expect(container.querySelectorAll(".workspace-panel")).toHaveLength(0);
  });

  it("每个 panel 槽位有 data-panel-id", () => {
    const { container } = render(<WorkspaceGrid layout={baseLayout} />);
    const alphaSlot = container.querySelector('[data-panel-id="alpha_mine"]');
    const factorsSlot = container.querySelector('[data-panel-id="factors"]');
    expect(alphaSlot).not.toBeNull();
    expect(factorsSlot).not.toBeNull();
  });

  it("3 个 panel — 渲染 3 个槽位", () => {
    const layout: WorkspaceLayout = {
      ...baseLayout,
      panels: [{ id: "alpha_mine" }, { id: "factors" }, { id: "watch_vchart" }],
    };
    const { container } = render(<WorkspaceGrid layout={layout} />);
    expect(container.querySelectorAll(".workspace-panel")).toHaveLength(3);
  });
});

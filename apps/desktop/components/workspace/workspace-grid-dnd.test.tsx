import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { WorkspaceGrid } from "./workspace-grid";
import type { PanelId, WorkspaceLayout } from "@/lib/workspace/workspace-layouts";

// mock panel-registry(同 workspace-grid.test.tsx)
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

// mock @/components/ui/select — Radix Select 在 jsdom 里弹层难测,直接渲染 trigger + 模拟 onValueChange
vi.mock("@/components/ui/select", () => ({
  Select: ({ value, onValueChange }: { value: string; onValueChange?: (v: string) => void }) => (
    <div data-testid={`select-${value}`} data-onvaluechange={onValueChange ? "true" : "false"} />
  ),
  SelectTrigger: ({ children, className, ...props }: any) => (
    <button data-testid="select-trigger" className={className} {...props}>{children}</button>
  ),
  SelectValue: () => <span data-testid="select-value" />,
  SelectContent: ({ children }: any) => <div data-testid="select-content">{children}</div>,
  SelectItem: ({ value, children }: any) => (
    <div data-testid={`select-item-${value}`} data-value={value}>{children}</div>
  ),
}));

const baseLayout: WorkspaceLayout = {
  id: "test",
  name: "测试",
  panels: [
    { id: "alpha_mine" },
    { id: "factors" },
  ],
  columns: 2,
  schemaVersion: 2,
  createdAt: "2026-08-04T00:00:00Z",
  updatedAt: "2026-08-04T00:00:00Z",
};

// helper:mock dataTransfer(HTML5 DnD 在 jsdom 需手动 mock)
function makeDataTransfer() {
  const store: Record<string, string> = {};
  return {
    effectAllowed: "move" as const,
    dropEffect: "move" as const,
    setData: (k: string, v: string) => { store[k] = v; },
    getData: (k: string) => store[k] ?? "",
  };
}

describe("P7 WorkspaceGrid DnD 拖拽重排", () => {
  beforeEach(() => {
    vi.useRealTimers();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("拖拽 slot 0 到 slot 1 — onLayoutChange 被调用,panels 重排", () => {
    const onLayoutChange = vi.fn();
    render(<WorkspaceGrid layout={baseLayout} onLayoutChange={onLayoutChange} />);

    const panels = screen.getAllByTestId(/^panel-/);
    expect(panels).toHaveLength(2);

    // 拖拽源 = slot 0
    const sourceHeader = screen.getAllByText("Alpha 挖矿")[0].closest(".workspace-panel-header")!;
    const targetPanel = panels[1].closest(".workspace-panel")!;

    // dragStart
    fireEvent.dragStart(sourceHeader, { dataTransfer: makeDataTransfer() });
    // dragOver target
    fireEvent.dragOver(targetPanel, { dataTransfer: makeDataTransfer() });
    // drop target
    fireEvent.drop(targetPanel, { dataTransfer: makeDataTransfer() });

    expect(onLayoutChange).toHaveBeenCalledTimes(1);
    const next = onLayoutChange.mock.calls[0][0];
    // panels 数组顺序反转:原本 [alpha_mine, factors] → [factors, alpha_mine]
    expect(next.panels[0].id).toBe("factors");
    expect(next.panels[1].id).toBe("alpha_mine");
  });

  it("onLayoutChange 未传时拖拽不报错(只视觉反馈)", () => {
    render(<WorkspaceGrid layout={baseLayout} />);
    const panels = screen.getAllByTestId(/^panel-/);
    const sourceHeader = screen.getAllByText("Alpha 挖矿")[0].closest(".workspace-panel-header")!;
    const targetPanel = panels[1].closest(".workspace-panel")!;

    expect(() => {
      fireEvent.dragStart(sourceHeader, { dataTransfer: makeDataTransfer() });
      fireEvent.dragOver(targetPanel, { dataTransfer: makeDataTransfer() });
      fireEvent.drop(targetPanel, { dataTransfer: makeDataTransfer() });
    }).not.toThrow();
  });

  it("dragSource 状态 — 拖拽时源 panel 有 data-drag-source=true", () => {
    render(<WorkspaceGrid layout={baseLayout} />);
    const panels = screen.getAllByTestId(/^panel-/);
    const sourceHeader = screen.getAllByText("Alpha 挖矿")[0].closest(".workspace-panel-header")!;

    fireEvent.dragStart(sourceHeader, { dataTransfer: makeDataTransfer() });
    const sourcePanel = panels[0].closest(".workspace-panel")!;
    expect(sourcePanel.getAttribute("data-drag-source")).toBe("true");
  });
});

describe("P7 WorkspaceGrid Select 类型切换", () => {
  it("渲染 Select 类型切换器(每个 panel 一个)", () => {
    render(<WorkspaceGrid layout={baseLayout} />);
    // 2 panel = 2 个 Select
    expect(screen.getAllByTestId(/^select-/)).toHaveLength(2);
  });

  it("Select 显示当前 panel id 作为 value", () => {
    render(<WorkspaceGrid layout={baseLayout} />);
    expect(screen.getByTestId("select-alpha_mine")).toBeInTheDocument();
    expect(screen.getByTestId("select-factors")).toBeInTheDocument();
  });
});

describe("P7 WorkspaceGrid grid 跨度 resize", () => {
  it("slot 有 w/h 时,panel style 含 gridColumn span + minHeight", () => {
    const layout: WorkspaceLayout = {
      ...baseLayout,
      panels: [
        { id: "alpha_mine", w: 960, h: 600 },
      ],
    };
    const { container } = render(<WorkspaceGrid layout={layout} />);
    const panel = container.querySelector(".workspace-panel") as HTMLElement;
    expect(panel.style.gridColumn).toContain("span");
    // 960 / 480 = 2,span 2
    expect(panel.style.gridColumn).toBe("span 2");
    expect(panel.style.minHeight).toBe("600px");
  });

  it("slot 无 w/h 时,panel style 不含 gridColumn span", () => {
    const { container } = render(<WorkspaceGrid layout={baseLayout} />);
    const panels = container.querySelectorAll(".workspace-panel");
    expect((panels[0] as HTMLElement).style.gridColumn).toBe("");
  });

  it("w < GRID_TRACK_MIN(480)— span 1(最小)", () => {
    const layout: WorkspaceLayout = {
      ...baseLayout,
      panels: [{ id: "alpha_mine", w: 300 }],
    };
    const { container } = render(<WorkspaceGrid layout={layout} />);
    const panel = container.querySelector(".workspace-panel") as HTMLElement;
    expect(panel.style.gridColumn).toBe("span 1");
  });

  it("w > 3 * GRID_TRACK_MIN(1440)— span 3(最大,clamp)", () => {
    const layout: WorkspaceLayout = {
      ...baseLayout,
      panels: [{ id: "alpha_mine", w: 2000 }],
    };
    const { container } = render(<WorkspaceGrid layout={layout} />);
    const panel = container.querySelector(".workspace-panel") as HTMLElement;
    expect(panel.style.gridColumn).toBe("span 3");
  });
});

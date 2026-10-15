import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { GraphViewPanel } from "@/components/research/graph-view-panel";
import {
  getGraphView,
  type GraphView,
} from "@/lib/api/research-graph";

const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useSearchParams: () => ({ get: (k: string) => (k === "focus" ? FOCUS : null) }),
  useRouter: () => ({ push: mockPush }),
}));

// echarts mock:必须覆盖组件实际调用的方法(init/dispose/setOption/on)。
// `.on` 是可见性刀加的节点点击跳转;漏了它会抛 unhandled rejection
// (vitest 报 "chartInstance.on is not a function" 但测试仍"通过")。
vi.mock("echarts", () => ({
  init: () => ({ dispose: () => {}, setOption: () => {}, on: () => {} }),
}));

vi.mock("@/lib/api/research-graph", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/api/research-graph")
  >("@/lib/api/research-graph");
  return {
    ...actual,
    getGraphView: vi.fn(),
  };
});

const mockView = vi.mocked(getGraphView);

let FOCUS: string | null = null;

const view: GraphView = {
  nodes: [
    {
      id: "q-1",
      category: "question",
      name: "BTC 突破是否有效",
      status: "open",
      created_at: "2026-09-16T08:00:00",
    },
    {
      id: "rev-2",
      category: "revision",
      name: "v2 突破确认",
      question_id: "q-1",
      actor: "selene",
      created_at: "2026-09-17T08:05:00",
    },
  ],
  links: [],
};

beforeEach(() => {
  mockView.mockReset();
  mockPush.mockReset();
  FOCUS = null;
});

describe("GraphViewPanel", () => {
  it("空库渲染诚实空数据头(echarts 画布容器仍在)", async () => {
    mockView.mockResolvedValue({ nodes: [], links: [] });
    render(<GraphViewPanel />);
    await waitFor(() => expect(mockView).toHaveBeenCalled());
    expect(screen.getByText("研究图谱")).toBeInTheDocument();
    expect(screen.getByText(/0 节点 · 0 边/)).toBeInTheDocument();
  });

  it("有数据显示节点与边计数", async () => {
    mockView.mockResolvedValue(view);
    render(<GraphViewPanel />);
    await screen.findByText(/2 节点 · 0 边/);
  });

  it("focus 参数显示高亮徽标并标注节点类型", async () => {
    FOCUS = "rev-2";
    mockView.mockResolvedValue(view);
    render(<GraphViewPanel />);
    await screen.findByText(/高亮:回答 v2 突破确认/);
    // 取消按钮链回无 focus 的图谱
    expect(screen.getByRole("link", { name: "✕" })).toHaveAttribute(
      "href",
      "/research/graph",
    );
  });
});

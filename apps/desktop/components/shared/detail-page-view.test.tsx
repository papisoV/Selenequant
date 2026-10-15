import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { PageMeta } from "@/lib/page-manifest";
import { DetailPageView } from "./detail-page-view";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(mockQuery),
}));

let mockQuery: Record<string, string> = {};

const { useApiMock } = vi.hoisted(() => ({ useApiMock: vi.fn() }));

vi.mock("@/lib/hooks/use-api", () => ({
  useApi: useApiMock,
}));

function meta(overrides: Partial<PageMeta> = {}): PageMeta {
  return {
    slug: "backtests-task-id",
    title: "回测详情",
    scene: "trade",
    category: "数据",
    renderMode: "list",
    removable: true,
    origin: "backend-insert",
    rest: "/api/v1/backtests/{task_id}",
    ws: "none",
    fate: "detail",
    ...overrides,
  } as PageMeta;
}

beforeEach(() => {
  mockQuery = {};
  useApiMock.mockReset();
  useApiMock.mockReturnValue({ data: undefined, isLoading: false });
});

describe("DetailPageView(通用详情页)", () => {
  it("缺参数 → 提示从列表进入,不发请求", () => {
    render(<DetailPageView meta={meta()} />);
    expect(screen.getByText(/请从列表进入/)).toBeInTheDocument();
    expect(useApiMock).toHaveBeenCalledWith(null);
  });

  it("?task_id= 填充占位符 → fetch 填参后的 URL,dict → 键值渲染", async () => {
    mockQuery = { task_id: "bt-42" };
    useApiMock.mockReturnValue({
      data: { task_id: "bt-42", status: "completed", sharpe_ratio: 1.83, trades: 128 },
      isLoading: false,
    });
    render(<DetailPageView meta={meta()} />);
    await waitFor(() => expect(useApiMock).toHaveBeenCalledWith("/api/v1/backtests/bt-42"));
    expect(screen.getByText("Task Id")).toBeInTheDocument();
    expect(screen.getByText("bt-42")).toBeInTheDocument();
    expect(screen.getByText("Status")).toBeInTheDocument();
    expect(screen.getByText("completed")).toBeInTheDocument();
    expect(screen.getByText("1.83")).toBeInTheDocument();
  });

  it("回退别名:rest 是 {id} 时 ?id= 也可用", async () => {
    mockQuery = { id: "opt-9" };
    useApiMock.mockReturnValue({ data: { id: "opt-9", status: "running" }, isLoading: false });
    render(
      <DetailPageView meta={meta({ slug: "optimizations-optimization-id", rest: "/api/v1/optimizations/{id}" })} />,
    );
    await waitFor(() => expect(useApiMock).toHaveBeenCalledWith("/api/v1/optimizations/opt-9"));
  });

  it("数组数据 → 表格渲染", async () => {
    mockQuery = { exchange: "binance" };
    useApiMock.mockReturnValue({
      data: { markets: [{ symbol: "BTC/USDT", tick_size: 0.1 }, { symbol: "ETH/USDT", tick_size: 0.01 }] },
      isLoading: false,
    });
    render(
      <DetailPageView meta={meta({ slug: "grid-markets-exchange", rest: "/api/v1/grid/markets/{exchange}" })} />,
    );
    await waitFor(() => expect(useApiMock).toHaveBeenCalledWith("/api/v1/grid/markets/binance"));
    expect(screen.getByText("Symbol")).toBeInTheDocument();
    expect(screen.getByText("BTC/USDT")).toBeInTheDocument();
    expect(screen.getByText("ETH/USDT")).toBeInTheDocument();
  });

  it("请求完成但数据空 → 暂无数据", () => {
    mockQuery = { task_id: "bt-empty" };
    useApiMock.mockReturnValue({ data: null, isLoading: false });
    render(<DetailPageView meta={meta()} />);
    expect(screen.getByText("暂无数据")).toBeInTheDocument();
  });
});

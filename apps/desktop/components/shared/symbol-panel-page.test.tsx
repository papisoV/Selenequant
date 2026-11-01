import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { SymbolPanelPage } from "./symbol-panel-page";
import { useApi } from "@/lib/hooks/use-api";
import { useMarketStore } from "@/lib/store/market";
import type { PageMeta } from "@/lib/page-manifest";

vi.mock("@/lib/hooks/use-api", () => ({
  useApi: vi.fn(),
}));

const mockUseApi = vi.mocked(useApi);

function meta(partial: Partial<PageMeta> & { slug: string; renderMode: "symbol" }): PageMeta {
  return {
    title: partial.slug,
    scene: "watch",
    category: "数据",
    removable: true,
    origin: "backend-insert",
    ...partial,
  } as PageMeta;
}

const symbolMeta = meta({
  slug: "liqmap-current",
  renderMode: "symbol",
  rest: "/api/v1/liqmap/current",
});

const dynamicMeta = meta({
  slug: "derivatives-open-interest-detail",
  renderMode: "symbol",
  rest: "/api/v1/derivatives/open-interest/{oi_id}",
});

beforeEach(() => {
  useMarketStore.setState({ pageFilter: { tf: "1h", symbol: "BTC", exchange: "all" } });
  mockUseApi.mockReset();
});

describe("SymbolPanelPage(键值面板)", () => {
  it("分组渲染:数值/状态/嵌套/其他 分区", () => {
    mockUseApi.mockReturnValue({
      data: {
        total_liquidated_usd: 1500000,
        change_pct: -2.5,
        status: "running",
        nested: { a: 1, b: 2 },
        name: "BTC",
      },
      isLoading: false,
    } as never);
    const { container } = render(<SymbolPanelPage meta={symbolMeta} />);
    expect(screen.getByText("数值")).toBeTruthy();
    expect(screen.getByText("状态")).toBeTruthy();
    expect(screen.getByText("数据")).toBeTruthy();
    expect(screen.getByText("其他")).toBeTruthy();
    // 大数字缩写:1.5M
    expect(screen.getByText("1.50M")).toBeTruthy();
    // 状态 pill running
    expect(screen.getByText("running")).toBeTruthy();
    // 百分比键带 % 后缀
    expect(screen.getByText("-2.50%")).toBeTruthy();
    // 嵌套对象摘要
    expect(container.textContent).toContain("a:1");
  });

  it("数值着色:正数 text-pos 绿,负数 text-neg 红", () => {
    mockUseApi.mockReturnValue({
      data: { positive: 1.5, negative: -3.2 },
      isLoading: false,
    } as never);
    const { container } = render(<SymbolPanelPage meta={symbolMeta} />);
    const posEl = [...document.querySelectorAll("dd span")].find((e) => e.textContent === "1.50");
    const negEl = [...document.querySelectorAll("dd span")].find((e) => e.textContent === "-3.20");
    expect(posEl?.className).toContain("text-pos");
    expect(negEl?.className).toContain("text-neg");
    expect(container).toBeTruthy();
  });

  it("布尔状态 → on/off pill", () => {
    mockUseApi.mockReturnValue({
      data: { enabled: true, frozen: false },
      isLoading: false,
    } as never);
    render(<SymbolPanelPage meta={symbolMeta} />);
    expect(screen.getByText("on")).toBeTruthy();
    expect(screen.getByText("off")).toBeTruthy();
  });

  it("动态页(非 symbol 占位符) → '需从列表进入'", () => {
    mockUseApi.mockReturnValue({ data: null, isLoading: true } as never);
    render(<SymbolPanelPage meta={dynamicMeta} />);
    expect(screen.getByText("此页面需从列表进入")).toBeTruthy();
    expect(screen.queryByText("暂无数据")).toBeNull();
  });

  it("symbol 占位符 → 用 pageFilter.symbol 替换并请求", () => {
    const symMeta = meta({ slug: "ashare-factors-symbol", renderMode: "symbol", rest: "/api/v1/ashare/factors/{symbol}" });
    mockUseApi.mockReturnValue({ data: { factors: [] }, isLoading: false } as never);
    render(<SymbolPanelPage meta={symMeta} />);
    expect(mockUseApi).toHaveBeenCalledWith("/api/v1/ashare/factors/BTC");
  });

  it("顶层数组 data(如 signals-dashboard)→ 卡片网格渲染而非空白", () => {
    mockUseApi.mockReturnValue({
      data: [
        { name: "动量突破", value: 1500000, status: "active" },
        { name: "均值回归", value: -3.2, status: "inactive" },
      ],
      isLoading: false,
    } as never);
    render(<SymbolPanelPage meta={symbolMeta} />);
    // 卡片标题用 id/name
    expect(screen.getByText("动量突破")).toBeTruthy();
    expect(screen.getByText("均值回归")).toBeTruthy();
    // 数值着色 + 大数字缩写仍生效
    expect(screen.getByText("1.50M")).toBeTruthy();
    // 状态 pill 渲染
    expect(screen.getByText("active")).toBeTruthy();
    expect(screen.queryByText(/暂无数据/)).toBeNull();
  });

  it("空数据 → '暂无数据' + 路径", () => {
    mockUseApi.mockReturnValue({ data: null, isLoading: false } as never);
    render(<SymbolPanelPage meta={symbolMeta} />);
    expect(screen.getByText(/暂无数据/)).toBeTruthy();
    expect(screen.getByText("/api/v1/liqmap/current")).toBeTruthy();
  });
});

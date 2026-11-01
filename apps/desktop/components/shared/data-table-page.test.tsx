import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { DataTablePage } from "./data-table-page";
import { useApi } from "@/lib/hooks/use-api";
import type { PageMeta } from "@/lib/page-manifest";

vi.mock("@/lib/hooks/use-api", () => ({
  useApi: vi.fn(),
}));

const mockUseApi = vi.mocked(useApi);

function meta(partial: Partial<PageMeta> & { slug: string; renderMode: "list" }): PageMeta {
  return {
    title: partial.slug,
    scene: "trade",
    category: "数据",
    removable: true,
    origin: "backend-insert",
    ...partial,
  } as PageMeta;
}

const usRadarMeta = meta({
  slug: "us-radar-snapshot",
  renderMode: "list",
  rest: "/api/v1/us-radar/snapshot",
});

const plainTableMeta = meta({ slug: "backtests", renderMode: "list", rest: "/api/v1/backtests" });

const dynamicMeta = meta({
  slug: "backtests-task-id",
  renderMode: "list",
  rest: "/api/v1/backtests/{task_id}",
});

const kvMeta = meta({
  slug: "liquidations-stats",
  renderMode: "list",
  rest: "/api/v1/liquidations/stats",
});

beforeEach(() => {
  mockUseApi.mockReset();
});

describe("DataTablePage(列表页)", () => {
  it("dict 数组字段自动解包 → 渲染成表(us-radar capabilities)", () => {
    mockUseApi.mockReturnValue({
      data: {
        capabilities: [
          { provider: "futu", quotes: 1, candles: 1 },
          { provider: "tiger", quotes: 1, candles: 0 },
        ],
        updated_at: "2026-01-01",
      },
      isLoading: false,
    } as never);
    const { container } = render(<DataTablePage meta={usRadarMeta} />);
    expect(screen.getByText("Provider")).toBeTruthy();
    expect(container.querySelectorAll("tbody tr").length).toBe(2);
    expect(screen.getByText("futu")).toBeTruthy();
  });

  it("顶层数组 → 直接渲染成表", () => {
    mockUseApi.mockReturnValue({
      data: [{ id: 1, name: "btc" }, { id: 2, name: "eth" }],
      isLoading: false,
    } as never);
    render(<DataTablePage meta={plainTableMeta} />);
    expect(screen.getByText("Name")).toBeTruthy();
    expect(screen.getAllByRole("row").length).toBe(3); // header + 2
  });

  it("纯 dict 页 → 键值视图而非单行表", () => {
    mockUseApi.mockReturnValue({
      data: { window: "1h", total_liquidated_usd: 123456 },
      isLoading: false,
    } as never);
    render(<DataTablePage meta={kvMeta} />);
    expect(screen.getByText("Window")).toBeTruthy();
    expect(screen.getByText("Total Liquidated Usd")).toBeTruthy();
    // 大数字缩写:123456 → 123.5K
    expect(screen.getByText("123.5K")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("动态页(task_id) → '需从列表进入' 特殊态而非骨架", () => {
    mockUseApi.mockReturnValue({ data: null, isLoading: true } as never);
    render(<DataTablePage meta={dynamicMeta} />);
    expect(screen.getByText("此页面需从列表进入")).toBeTruthy();
    expect(screen.queryByText("暂无数据")).toBeNull();
    expect(screen.queryByTestId("skeleton")).toBeNull();
  });

  it("列排序:点列头切换 asc/desc", () => {
    mockUseApi.mockReturnValue({
      data: [{ id: 1, val: 30 }, { id: 2, val: 10 }, { id: 3, val: 20 }],
      isLoading: false,
    } as never);
    render(<DataTablePage meta={plainTableMeta} />);
    const cells = () => Array.from(document.querySelectorAll("tbody tr:first-child td")).map((c) => c.textContent);
    // 初始顺序 30,10,20(第 1 行 val=30)
    fireEvent.click(screen.getByText("Val"));
    // asc:10,20,30 → 第 1 行 val=10
    expect(cells()![1]).toBe("10.00");
    fireEvent.click(screen.getByText("Val"));
    // desc:30,20,10 → 第 1 行 val=30
    expect(cells()![1]).toBe("30.00");
  });

  it("文本过滤:按行任意字段值过滤", () => {
    mockUseApi.mockReturnValue({
      data: [{ name: "btc" }, { name: "eth" }, { name: "sol" }],
      isLoading: false,
    } as never);
    render(<DataTablePage meta={plainTableMeta} />);
    const input = screen.getByPlaceholderText("过滤…");
    fireEvent.change(input, { target: { value: "eth" } });
    expect(screen.getByText("eth")).toBeTruthy();
    expect(screen.queryByText("btc")).toBeNull();
    expect(screen.queryByText("sol")).toBeNull();
  });

  it("分页:每页 10 条,下一页显示后续行", () => {
    const rows = Array.from({ length: 15 }, (_, i) => ({ id: i + 1, name: `item${i + 1}` }));
    mockUseApi.mockReturnValue({ data: rows, isLoading: false } as never);
    render(<DataTablePage meta={plainTableMeta} />);
    // 第 1 页 10 行
    expect(document.querySelectorAll("tbody tr").length).toBe(10);
    expect(screen.getByText("item1")).toBeTruthy();
    expect(screen.queryByText("item11")).toBeNull();
    // 点下一页
    fireEvent.click(screen.getByText("→"));
    expect(document.querySelectorAll("tbody tr").length).toBe(5);
    expect(screen.getByText("item11")).toBeTruthy();
  });

  it("CSV 导出:点击生成含表头与数据的下载", () => {
    // stub URL.createObjectURL + HTMLAnchorElement.click
    const origCreate = URL.createObjectURL;
    const origRevoke = URL.revokeObjectURL;
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    URL.createObjectURL = vi.fn(() => "blob:test");
    URL.revokeObjectURL = vi.fn();

    mockUseApi.mockReturnValue({
      data: [{ id: 1, name: "btc" }, { id: 2, name: "eth" }],
      isLoading: false,
    } as never);
    render(<DataTablePage meta={plainTableMeta} />);
    fireEvent.click(screen.getByText("CSV"));

    expect(clickSpy).toHaveBeenCalled();
    expect(URL.createObjectURL).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test");

    clickSpy.mockRestore();
    URL.createObjectURL = origCreate;
    URL.revokeObjectURL = origRevoke;
  });
});

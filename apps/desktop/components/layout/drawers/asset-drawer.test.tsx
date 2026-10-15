import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const h = vi.hoisted(() => ({
  urls: [] as string[],
  payload: null as unknown,
}));

vi.mock("@/lib/hooks/use-api", () => ({
  useApi: (url: string | null) => {
    if (url) h.urls.push(url);
    return { data: h.payload, isLoading: false, error: null };
  },
  fetcher: () => Promise.resolve(null),
}));

vi.mock("./drawer-panel", () => ({
  DrawerPanel: ({ children, title }: { children: React.ReactNode; title: string }) => (
    <div data-testid="drawer">
      <div data-testid="drawer-title">{title}</div>
      {children}
    </div>
  ),
}));

import { AssetDrawer } from "./asset-drawer";

// 后端 /api/v1/portfolio 只返回 7 字段；不含 equity_history/week_pnl/margin_ratio/market_breakdown
const POSITIONS = [
  { symbol: "BTC/USDT", side: "long", size: 1, entry_price: 50000, current_price: 60000, unrealized_pnl: 10000 },
  { symbol: "ETH/USDT", side: "long", size: 10, entry_price: 2000, current_price: 2100, unrealized_pnl: 1000 },
];

describe("AssetDrawer — 移除后端从不发送的死字段", () => {
  beforeEach(() => {
    h.urls.length = 0;
    h.payload = null;
  });

  it("只渲染真实字段:持仓市值 + 未实现盈亏,无 7 日曲线/周 PNL/保证金率", () => {
    h.payload = POSITIONS;
    const { container } = render(<AssetDrawer show onClose={() => {}} />);

    // 持仓市值 = 1*60000 + 10*2100 = 81000
    expect(container.textContent).toContain("$81,000");
    // 未实现盈亏 = 10000 + 1000 = 11000
    expect(container.textContent).toContain("+$11,000");
    // 已删:7 日净资产曲线、周 PNL、保证金率、杠杆
    expect(screen.queryByText("7 日净资产曲线")).toBeNull();
    expect(screen.queryByText("周 PNL")).toBeNull();
    expect(screen.queryByText("保证金率")).toBeNull();
    expect(screen.queryByText("杠杆")).toBeNull();
    // "日 PNL" 标签曾误标未实现盈亏,已改为准确的"未实现盈亏"
    expect(screen.queryByText("日 PNL")).toBeNull();
    expect(screen.getByText("未实现盈亏")).toBeTruthy();
  });

  it("空仓 → 占位,不崩溃", () => {
    h.payload = [];
    const { container } = render(<AssetDrawer show onClose={() => {}} />);
    expect(container.textContent).toContain("—");
  });

  it("请求打到 /api/v1/portfolio", () => {
    render(<AssetDrawer show onClose={() => {}} />);
    expect(h.urls.some((u) => u.startsWith("/api/v1/portfolio"))).toBe(true);
  });
});

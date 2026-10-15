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

vi.mock("@/components/shared/page-filter-bar", () => ({
  V17Card: ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div data-testid={`card-${title}`}>{children}</div>
  ),
  StatBlock: ({ label, value }: { label: string; value: string | number }) => (
    <div data-testid={`stat-${label}`}>{value}</div>
  ),
  PageFilterBar: () => <div data-testid="page-filter-bar" />,
}));

vi.mock("@/components/shared/workspace-link", () => ({
  WorkspaceLink: () => <span data-testid="ws-link" />,
}));

vi.mock("@/lib/store/context", () => ({
  useContextStore: () => ({ pageFilter: { symbol: "BTC/USDT", tf: "1h" } }),
}));

import { SigPage } from "./sig-page";

// /api/v1/signals/current 返回**单个** CompositeSignal(非数组)
const SIG = {
  symbol: "BTC/USDT",
  exchange: "binance",
  side: "buy",
  strength: 72.5,
  confidence: 0.8,
  mark_price: 60000,
  factors: [
    { name: "cvd", value: 1.2, weight: 0.3, contribution: 0.36, direction: "bullish" },
    { name: "gex", value: -0.4, weight: 0.2, contribution: -0.08, direction: "bearish" },
  ],
};

describe("SigPage — 对齐单对象 CompositeSignal(旧代码按数组读 → 恒空)", () => {
  beforeEach(() => {
    h.urls.length = 0;
    h.payload = null;
  });

  it("渲染方向/强度/置信度 + 因子拆解", () => {
    h.payload = SIG;
    const { container } = render(<SigPage />);

    expect(screen.getByTestId("stat-方向").textContent).toBe("看多");
    expect(screen.getByTestId("stat-强度").textContent).toBe("72.5");
    expect(screen.getByTestId("stat-置信度").textContent).toBe("80%");
    // 两个因子都渲染
    expect(container.querySelectorAll(".sig-feed")!.length).toBeGreaterThan(0);
    expect(container.textContent).toContain("cvd");
    expect(container.textContent).toContain("gex");
    // 不再出现旧的"暂无信号数据"
    expect(screen.queryByText("暂无信号数据")).toBeNull();
  });

  it("无数据 → 空态", () => {
    h.payload = null;
    render(<SigPage />);
    expect(screen.getByText("暂无信号数据")).toBeTruthy();
  });

  it("请求打到 /api/v1/signals/current", () => {
    render(<SigPage />);
    expect(h.urls.some((u) => u.startsWith("/api/v1/signals/current"))).toBe(true);
  });
});

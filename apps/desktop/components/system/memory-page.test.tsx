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
}));

import { MemoryPage } from "./memory-page";

// 后端 /api/v1/memory/market-patterns 返回**裸 list**(旧代码读 {patterns_stored, recent} → 恒 0/空)
const PATTERNS = [
  { id: "p1", name: "缩量回踩", symbol: "BTC/USDT", enabled: true, created_at: 1_700_000_000_000, hit_count: 3 },
  { id: "p2", name: "放量突破", symbol: "ETH/USDT", enabled: false, created_at: 1_700_100_000_000, hit_count: 5 },
];

describe("MemoryPage — 对齐裸 list 形态", () => {
  beforeEach(() => {
    h.urls.length = 0;
    h.payload = null;
  });

  it("从数组长度/命中数算概览,读真实 name/hit_count", () => {
    h.payload = PATTERNS;
    render(<MemoryPage />);

    expect(screen.getByTestId("stat-已存储模式").textContent).toBe("2");
    expect(screen.getByTestId("stat-启用中").textContent).toBe("1");
    // 累计命中 3 + 5 = 8
    expect(screen.getByTestId("stat-累计命中").textContent).toBe("8");
    expect(screen.getByText("缩量回踩")).toBeTruthy();
  });

  it("空列表 → 空态", () => {
    h.payload = [];
    render(<MemoryPage />);
    expect(screen.getByText("暂无数据")).toBeTruthy();
  });

  it("请求打到 /api/v1/memory/market-patterns", () => {
    render(<MemoryPage />);
    expect(h.urls.some((u) => u.startsWith("/api/v1/memory/market-patterns"))).toBe(true);
  });
});

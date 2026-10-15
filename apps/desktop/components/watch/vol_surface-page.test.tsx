import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// === mock useApi =================================
const mockApiData: Record<string, unknown> = {};
vi.mock("@/lib/hooks/use-api", () => ({
  useApi: (url: string) => ({ data: mockApiData[url] ?? null, loading: false, error: null }),
}));

// === mock card primitives (avoid heavy deps) =====
vi.mock("@/components/shared/page-filter-bar", () => ({
  V17Card: ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div data-testid={`card-${title}`}>{children}</div>
  ),
  StatBlock: ({ label, value }: { label: string; value: string }) => (
    <div data-testid={`stat-${label}`}>{value}</div>
  ),
}));

// === import AFTER mocks =================================
import { VolSurfacePage } from "./vol_surface-page";

const SURFACE_URL = "/api/v1/options/vol-surface";

function populatedSurface() {
  return {
    underlying: "BTC",
    ts: 1742630400000,
    points: [
      { expiry: "2025-03-22", strike: 70000, iv: 0.72, volume: 800 },
      { expiry: "2025-03-22", strike: 80000, iv: 0.65, volume: 1200 },
    ],
    expiries: ["2025-03-22"],
    strikes: [70000, 80000],
  };
}

describe("VolSurfacePage", () => {
  beforeEach(() => {
    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
  });

  it("renders term structure + skew when data present (no 数据不足 fallback)", () => {
    mockApiData[SURFACE_URL] = populatedSurface();
    render(<VolSurfacePage />);

    // 数据不足降级视图必须消失
    expect(screen.queryByText(/数据不足/)).toBeNull();
    // 期限结构 + 偏度卡片渲染
    expect(screen.getByTestId("card-期限结构 (Term Structure)")).toBeTruthy();
    expect(screen.getByTestId("card-偏度 (Skew)")).toBeTruthy();
    // 标的统计块有值
    expect(screen.getByTestId("stat-标的").textContent).toContain("BTC");
  });

  it("shows 数据不足 fallback when API returns empty", () => {
    mockApiData[SURFACE_URL] = {
      underlying: "BTC",
      ts: 0,
      points: [],
      expiries: [],
      strikes: [],
    };
    render(<VolSurfacePage />);
    expect(screen.getByText(/数据不足/)).toBeTruthy();
  });
});

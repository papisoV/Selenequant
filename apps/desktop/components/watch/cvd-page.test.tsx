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

vi.mock("@/lib/store/context", () => ({
  useContextStore: () => ({ pageFilter: { symbol: "BTC/USDT", tf: "1h" } }),
}));

import { CvdPage } from "./cvd-page";

describe("CvdPage — 对齐 /api/v1/cvd/all 的 CVDBarEvent", () => {
  beforeEach(() => {
    h.urls.length = 0;
    h.payload = null;
  });

  it("按 interval 读 cvd_usd,不再读不存在的 delta_* 字段", () => {
    h.payload = {
      "5m": { cvd_usd: 1_500_000, buy_notional_usd: 3_000_000, sell_notional_usd: 1_500_000, trade_count: 42 },
      "1h": { cvd_usd: -800_000 },
      "4h": { cvd_usd: 250 },
    };
    render(<CvdPage />);

    expect(screen.getByTestId("stat-5m").textContent).toBe("+$1.50M");
    expect(screen.getByTestId("stat-1h").textContent).toBe("-$800.0K");
    expect(screen.getByTestId("stat-4h").textContent).toBe("+$250");
    // 5m 明细来自真实字段(旧代码无此卡)
    expect(screen.getByTestId("stat-成交笔数").textContent).toBe("42");
  });

  it("无数据 → 全占位,不伪装 +$0", () => {
    h.payload = null;
    render(<CvdPage />);
    expect(screen.getByTestId("stat-5m").textContent).toBe("—");
    expect(screen.getByTestId("stat-1h").textContent).toBe("—");
  });

  it("请求打到 /api/v1/cvd/all", () => {
    render(<CvdPage />);
    expect(h.urls.some((u) => u.startsWith("/api/v1/cvd/all"))).toBe(true);
  });
});

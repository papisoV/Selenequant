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

vi.mock("swr", () => ({ preload: () => Promise.resolve() }));

vi.mock("@/components/shared/page-filter-bar", () => ({
  V17Card: ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div data-testid={`card-${title}`}>{children}</div>
  ),
  PageFilterBar: () => <div data-testid="page-filter-bar" />,
}));

vi.mock("@/lib/store/context", () => ({
  useContextStore: () => ({ pageFilter: { symbol: "BTC/USDT", tf: "1h" } }),
}));

import { LiqRatioPage } from "./liqratio-page";

describe("LiqRatioPage — 从 long/short_liq_weight 推导", () => {
  beforeEach(() => {
    h.urls.length = 0;
    h.payload = null;
  });

  it("用真实权重算多空占比,不再依赖不存在的 ratio 字段(旧代码恒 1.00 / balanced)", () => {
    h.payload = [
      { symbol: "BTC", long_liq_weight: 300, short_liq_weight: 100 },
    ];
    const { container: c } = render(<LiqRatioPage />);

    const rows = c.querySelectorAll(".lr-row");
    expect(rows.length).toBe(1);
    // 比值 300/100 = 3.00；旧代码读 d.ratio(不存在) → 恒 1.00
    const skewCell = rows[0].querySelector(".lr-ratio");
    expect(skewCell?.textContent).toBe("3.00");
    // 行内 skew 标签(非说明卡里的文案)
    expect(rows[0].querySelector(".lr-skew")?.textContent).toBe("多头拥挤");
    // 长条宽度 = 300/(300+100) = 75.0%(jsdom 归一化为 "75%")
    const longBar = c.querySelector(".lr-bar-long") as HTMLElement;
    expect(longBar.style.width).toBe("75%");
  });

  it("空数组 → 空态", () => {
    h.payload = [];
    render(<LiqRatioPage />);
    expect(screen.getByText("暂无多空比数据")).toBeTruthy();
  });

  it("请求打到 /api/v1/liqratio/current", () => {
    render(<LiqRatioPage />);
    expect(h.urls.some((u) => u.startsWith("/api/v1/liqratio/current"))).toBe(true);
  });
});

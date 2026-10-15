import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AshareStylePage } from "./ashare-style-page";

vi.mock("@/lib/api-client", () => ({
  apiDirectRaw: vi.fn(),
  ApiError: class ApiError extends Error {
    constructor(
      message: string,
      readonly status: number,
    ) {
      super(message);
    }
  },
}));

import { apiDirectRaw } from "@/lib/api-client";
const mockApiDirect = vi.mocked(apiDirectRaw);

const REPORT = {
  forward_col: "fwd_5d",
  lookback: 20,
  n_symbols: 300,
  n_days: 151,
  styles: {
    SIZE: {
      regime: "leading",
      cum_spread: 0.0176,
      n_days: 20,
      spread_series: [
        { date: "2026-09-22", spread: 0.0021 },
        { date: "2026-09-23", spread: 0.0034 },
      ],
    },
    BP: { regime: "lagging", cum_spread: -0.04, n_days: 20, spread_series: [] },
    YOY_NI: { regime: "factor_absent" },
  },
  orthogonal: {
    kept: [
      {
        factor: "SIZE",
        IC_mean: 0.0312,
        IC_std: 0.12,
        IR: 0.26,
        IC_pos_rate: 0.65,
        t_stat: 2.6,
      },
    ],
    dropped: [{ factor: "BP", reason: "orthogonal t=1.200 < 2.0" }],
    order: ["SIZE", "BP", "YOY_NI"],
    params: { min_abs_t: 2.0, max_factors: null },
  },
};

function envelopeError(message: string) {
  return { data: null, meta: {}, error: { message } };
}

describe("AshareStylePage", () => {
  beforeEach(() => {
    mockApiDirect.mockReset();
  });

  it("裸 dict 成功态:三风格卡片渲染,regime 徽章与 spread 曲线出现", async () => {
    mockApiDirect.mockResolvedValue(REPORT);
    render(<AshareStylePage />);

    await waitFor(() => {
      expect(screen.getByTestId("style-badge-SIZE")).toHaveTextContent("领先");
    });
    expect(screen.getByTestId("style-badge-BP")).toHaveTextContent("落后");
    expect(screen.getByTestId("style-badge-YOY_NI")).toHaveTextContent("无数据");
    expect(screen.getByText(/覆盖 300 票 × 151 日/)).toBeTruthy();
    // SIZE has a series → svg chart rendered
    expect(screen.getByRole("img", { name: /SIZE 日度 spread/ })).toBeTruthy();
    // factor_absent style shows the absence note, no chart
    expect(screen.getByText(/面板中无该因子列/)).toBeTruthy();
    // orthogonal card renders kept factors and dropped reasons
    expect(screen.getByText(/因子正交化（贪心残差）/)).toBeTruthy();
    expect(screen.getByText("SIZE")).toBeTruthy();
    expect(screen.getByText(/orthogonal t=1.200 < 2.0/)).toBeTruthy();
  });

  it("envelope 错误态:错误文案展示", async () => {
    mockApiDirect.mockResolvedValue(envelopeError("kline cache empty"));
    render(<AshareStylePage />);

    await waitFor(() => {
      expect(screen.getByText(/kline cache empty/)).toBeTruthy();
    });
  });

  it("404 错误(envelope):显示错误且无卡片", async () => {
    mockApiDirect.mockResolvedValue(
      envelopeError("no style report data (kline cache empty for this window)"),
    );
    render(<AshareStylePage />);

    await waitFor(() => {
      expect(screen.getByText(/no style report data/)).toBeTruthy();
    });
    expect(screen.queryByTestId("style-badge-SIZE")).toBeNull();
  });

  it("重新计算按钮:POST refresh 并用返回 report 更新", async () => {
    mockApiDirect.mockResolvedValueOnce(REPORT);
    const refreshed = {
      ...REPORT,
      styles: {
        ...REPORT.styles,
        SIZE: { ...REPORT.styles.SIZE, regime: "lagging", cum_spread: -0.02 },
      },
    };
    mockApiDirect.mockResolvedValueOnce({ rebuilt: true, params: {}, report: refreshed });
    render(<AshareStylePage />);

    await waitFor(() => {
      expect(screen.getByTestId("style-badge-SIZE")).toHaveTextContent("领先");
    });

    fireEvent.click(screen.getByText("重新计算"));
    await waitFor(() => {
      expect(screen.getByTestId("style-badge-SIZE")).toHaveTextContent("落后");
    });
    expect(mockApiDirect).toHaveBeenCalledWith(
      "/api/v1/ashare/style-report/refresh",
      { method: "POST" },
    );
  });
});

describe("AshareStylePage 可访问性", () => {
  it("渲染页面级 h1（axe page-has-heading-one:此前缺失）", async () => {
    mockApiDirect.mockResolvedValue(REPORT);
    render(<AshareStylePage />);
    const h1 = await screen.findByRole("heading", { level: 1 });
    expect(h1.textContent).toBe("A股风格监测");
  });
});

describe("AshareStylePage 深色主题可读性", () => {
  it("状态徽章用深色友好底/字（防浅底浅字 1.01:1 复发）", async () => {
    mockApiDirect.mockResolvedValue(REPORT);
    render(<AshareStylePage />);
    const badge = await screen.findByTestId("style-badge-SIZE");
    const cls = badge.className;
    // 禁止"实心浅色底"（bg-<hue>-50/100/200，后面不接 /alpha）——深色主题里会与
    // 继承的浅文字撞成不可读。低透明的 bg-<hue>-500/15 是允许的(house 约定)。
    expect(cls).not.toMatch(/bg-(red|emerald|gray|green|amber)-(50|100|200)\b/);
    // 必须有显式文字色（不能靠继承）
    expect(cls).toMatch(/text-(red|emerald|gray|green|amber)-(300|400|500)\b/);
    expect(cls).toMatch(/bg-(red|emerald|gray|green|amber)-500\/\d+/);
  });

  it("错误框用深色友好样式（防浅白块）", async () => {
    mockApiDirect.mockRejectedValue(new Error("boom"));
    render(<AshareStylePage />);
    const err = await screen.findByText("boom");
    const cls = err.className;  // getByText 命中 text 所在元素本身
    expect(cls).not.toMatch(/bg-red-50\b|border-red-200\b|text-red-700\b/);
    expect(cls).toMatch(/bg-red-500\/\d+/);
  });
});

import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AshareFundamentalsPage } from "./ashare-fundamentals-page";

vi.mock("@/lib/api-client", () => ({
  apiDirectRaw: vi.fn(),
}));

import { apiDirectRaw } from "@/lib/api-client";
const mockApiDirect = vi.mocked(apiDirectRaw);

const TIMELINE_PAYLOAD = {
  symbol: "000008",
  n_records: 2,
  records: [
    {
      symbol: "000008",
      year: 2025,
      quarter: 4,
      statDate: "2025-12-31",
      pubDate: "2026-04-28",
      roeAvg: 0.0812,
      gpMargin: 0.234,
      epsTTM: 0.41,
      YOYNI: 0.153,
      BPS: 2.91,
    },
    {
      symbol: "000008",
      year: 2026,
      quarter: 1,
      statDate: "2026-03-31",
      pubDate: "2026-04-27",
      roeAvg: 0.0234,
      gpMargin: 0.241,
      epsTTM: null,
      YOYNI: -0.061,
      BPS: 3.02,
    },
  ],
};

const SNAPSHOT_PAYLOAD = {
  field: "BPS",
  year: 2026,
  quarter: 2,
  n_universe: 300,
  median: 5.8,
  max: { symbol: "603202", value: 42.51 },
  min: { symbol: "AAA", value: 0.12 },
  top: [
    { symbol: "603202", value: 42.51 },
    { symbol: "300724", value: 38.17 },
  ],
};

function routeApi(url: string) {
  if (url.includes("/financials/")) return Promise.resolve(TIMELINE_PAYLOAD);
  if (url.includes("financials-snapshot")) return Promise.resolve(SNAPSHOT_PAYLOAD);
  return Promise.reject(new Error(`unexpected url: ${url}`));
}

describe("AshareFundamentalsPage", () => {
  beforeEach(() => {
    mockApiDirect.mockReset();
    mockApiDirect.mockImplementation((url: string) => routeApi(url));
  });

  it("初始加载:时间线卡渲染季度行(pubDate/statDate/字段),快照卡渲染统计+top 横条", async () => {
    render(<AshareFundamentalsPage />);

    await waitFor(() => {
      expect(screen.getAllByText("2026Q1").length).toBeGreaterThan(0);
    });
    // timeline rows carry PIT anchors and formatted fields
    expect(screen.getByText("2026-04-28")).toBeTruthy();
    expect(screen.getByText("2026-03-31")).toBeTruthy();
    expect(screen.getByText("8.12%")).toBeTruthy(); // roeAvg decimal->pct
    expect(screen.getByText("—")).toBeTruthy(); // epsTTM None -> em dash

    // snapshot stats
    await waitFor(() => {
      expect(screen.getByText("覆盖股票数")).toBeTruthy();
    });
    expect(screen.getByText("300")).toBeTruthy();
    expect(screen.getByText("603202")).toBeTruthy();
  });

  it("时间线查询:输入票号点击查询 → 以新 symbol 发 GET", async () => {
    render(<AshareFundamentalsPage />);
    await waitFor(() => {
      expect(screen.getAllByText("2026Q1").length).toBeGreaterThan(0);
    });

    fireEvent.change(screen.getByPlaceholderText("000008"), {
      target: { value: "600519" },
    });
    fireEvent.click(screen.getByRole("button", { name: "查询" }));

    await waitFor(() => {
      expect(mockApiDirect).toHaveBeenCalledWith(
        "/api/v1/ashare/financials/600519",
      );
    });
  });

  it("快照切字段/季度 → 对应 GET 参数", async () => {
    render(<AshareFundamentalsPage />);
    await waitFor(() => {
      expect(screen.getByText("覆盖股票数")).toBeTruthy();
    });

    fireEvent.change(screen.getByDisplayValue("BPS"), {
      target: { value: "roeAvg" },
    });
    await waitFor(() => {
      expect(mockApiDirect).toHaveBeenCalledWith(
        "/api/v1/ashare/financials-snapshot?field=roeAvg",
      );
    });

    fireEvent.change(screen.getByDisplayValue("最新季"), {
      target: { value: "2025-4" },
    });
    await waitFor(() => {
      expect(mockApiDirect).toHaveBeenCalledWith(
        expect.stringContaining("field=roeAvg&year=2025&quarter=4"),
      );
    });
  });

  it("端点 404 → 错误文案显示在对应卡内", async () => {
    mockApiDirect.mockImplementation((url: string) => {
      if (url.includes("/financials/"))
        return Promise.reject(new Error("HTTP 404 NOT_FOUND for /financials/999999"));
      return Promise.resolve(SNAPSHOT_PAYLOAD);
    });

    render(<AshareFundamentalsPage />);
    await waitFor(() => {
      expect(screen.getByText(/HTTP 404/)).toBeTruthy();
    });
  });
});

describe("AshareFundamentalsPage 可访问性", () => {
  it("快照两个 select 有 accessible name（axe select-name critical:此前缺失）", async () => {
    // 用既有 routeApi：timeline 与 snapshot 返回各自形状，避免误喂导致渲染崩
    mockApiDirect.mockImplementation((url: string) => routeApi(url));
    render(<AshareFundamentalsPage />);
    // aria-label 提供 accessible name；以 role+name 断言，等价于 axe 的判定
    expect(await screen.findByRole("combobox", { name: "快照字段" })).toBeTruthy();
    expect(await screen.findByRole("combobox", { name: "快照季度" })).toBeTruthy();
  });
});

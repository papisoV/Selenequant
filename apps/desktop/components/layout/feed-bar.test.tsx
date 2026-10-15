import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FeedBar } from "./feed-bar";

const h = vi.hoisted(() => ({ mockApiData: {} as Record<string, unknown> }));

vi.mock("swr", () => ({
  default: (key: string | null) => ({
    data: key ? (h.mockApiData[key] ?? null) : null,
    error: undefined,
    isLoading: false,
    mutate: vi.fn(),
  }),
}));

vi.mock("@/lib/hooks/use-api", () => ({
  useApi: (url: string | null) => ({
    data: url ? (h.mockApiData[url] ?? null) : null,
    error: undefined,
    isLoading: false,
    mutate: vi.fn(),
  }),
  fetcher: vi.fn(async () => null),
}));

vi.mock("@/lib/hooks/use-monitor-feed", () => ({
  useMonitorFeed: () => ({
    items: [],
    source: "live",
    connected: true,
    clearFeed: vi.fn(),
  }),
}));

describe("FeedBar 系统事件 tab(透明性批3-12)", () => {
  beforeEach(() => {
    Object.keys(h.mockApiData).forEach((k) => delete h.mockApiData[k]);
  });

  it("切到系统 tab → 渲染 GET /events/recent 行", async () => {
    h.mockApiData["/api/v1/events/recent?limit=30"] = [
      {
        id: "evt-1",
        timestamp: 1759279200000,
        kind: "task_completed",
        source: "backtest",
        severity: "info",
        title: "回测完成 BTC 1d (bb_v3)",
      },
      {
        id: "evt-2",
        timestamp: 1759279000000,
        kind: "heartbeat_anomaly",
        source: "health",
        severity: "warning",
        title: "Worker 心跳失联 2 个",
      },
    ];

    render(<FeedBar />);
    fireEvent.click(screen.getByText("系统"));

    await waitFor(() => {
      expect(screen.getAllByTestId("system-event-row")).toHaveLength(2);
    });
    expect(screen.getByText("回测完成 BTC 1d (bb_v3)")).toBeTruthy();
    expect(screen.getByText("Worker 心跳失联 2 个")).toBeTruthy();
  });

  it("系统 tab 空态诚实提示", async () => {
    h.mockApiData["/api/v1/events/recent?limit=30"] = [];

    render(<FeedBar />);
    fireEvent.click(screen.getByText("系统"));

    await waitFor(() => {
      expect(screen.getByText("暂无系统事件")).toBeTruthy();
    });
  });
});

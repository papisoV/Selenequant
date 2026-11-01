import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MonitorPane } from "@/components/research/alpha-mining-monitor";
import type { MonitorQuad, MonitorReading } from "@/lib/api/alpha";
import { api, apiPost } from "@/lib/api-client";

const QUAD: MonitorQuad = { symbol: "BTC/USDT", interval: "1h", factor: "volume_zscore", source: "klines.db" };

const READING: MonitorReading = {
  value: 1.2345,
  computed_at: "2026-01-01T00:00:00Z",
  stale: false,
  last_bar_ts: 1700003600000,
  recomputed: false,
};

vi.mock("@/lib/api-client", () => ({
  api: vi.fn(async (url: string) => {
    if (url.includes("/monitor/list")) return [QUAD];
    return [];
  }),
  apiPost: vi.fn(async (url: string) => {
    if (url.includes("/monitor/read")) return { ...READING };
    return {};
  }),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

describe("监控 tab B4 四元组读数", () => {
  it("列表渲染 B4 对象;点读数 → monitorRead 返回新鲜值显示 + 新鲜徽章", async () => {
    render(<MonitorPane />);
    await waitFor(() => expect(screen.getByTestId("mon-row-0")).toBeInTheDocument());
    expect(screen.getByText(/BTC\/USDT · 1h/)).toBeInTheDocument();
    expect(screen.getByText(/volume_zscore/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("mon-read-0"));
    await waitFor(() => expect(screen.getByTestId("mon-reading-0")).toBeInTheDocument(), { timeout: 5000 });
    // 读数取值(tnum 格式化 toFixed(4))
    expect(screen.getByTestId("mon-reading-0")).toHaveTextContent("1.2345");
    expect(screen.getByTestId("mon-reading-0")).toHaveTextContent("新鲜");
    // 真实端点 URL 命中
    expect(vi.mocked(apiPost)).toHaveBeenCalledWith(
      expect.stringContaining("/monitor/read"),
      expect.objectContaining({ symbol: "BTC/USDT", interval: "1h", factor: "volume_zscore" }),
    );
  });

  it("注册新监控对象 → monitorRegister 写入四元组 payload", async () => {
    render(<MonitorPane />);
    await waitFor(() => expect(screen.getByTestId("mon-list")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("mon-symbol"), { target: { value: "ETH/USDT" } });
    fireEvent.change(screen.getByTestId("mon-interval"), { target: { value: "4h" } });
    fireEvent.change(screen.getByTestId("mon-factor"), { target: { value: "of_imbalance" } });
    fireEvent.click(screen.getByTestId("mon-register"));
    await waitFor(() =>
      expect(vi.mocked(apiPost)).toHaveBeenCalledWith(
        expect.stringContaining("/monitor/register"),
        expect.objectContaining({ symbol: "ETH/USDT", interval: "4h", factor: "of_imbalance" }),
      ),
    );
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type { ForecastEvalReport } from "@/lib/api/market-analysis";

const { evalMock } = vi.hoisted(() => ({ evalMock: vi.fn() }));
vi.mock("@/lib/api/market-analysis", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/market-analysis")>(
    "@/lib/api/market-analysis",
  );
  return { ...actual, getForecastEval: evalMock };
});

import { ForecastEvalCard } from "@/components/research/forecast-eval-card";

const report = (): ForecastEvalReport => ({
  symbol: "BTC/USDT",
  interval: "1h",
  samples_total: 3,
  samples_with_forecast: 2,
  hit_rate_forecast: 0.5,
  hit_rate_baseline: 0.4,
  rows: [
    {
      record_id: "ar-hit-123",
      forecast: "up",
      baseline: "up",
      actual: "up",
      forecast_hit: true,
      baseline_hit: true,
      probability: 0.6,
    },
    {
      record_id: "ar-miss-456",
      forecast: "down",
      baseline: "up",
      actual: "up",
      forecast_hit: false,
      baseline_hit: true,
      probability: 0.4,
    },
    {
      record_id: "ar-neut-789",
      forecast: null,
      baseline: "flat",
      actual: "flat",
      forecast_hit: null,
      baseline_hit: true,
      probability: null,
    },
  ],
});

beforeEach(() => {
  evalMock.mockReset();
});

describe("ForecastEvalCard 预测校准审计", () => {
  it("渲染诚实横幅 + 聚合命中率 + ✓/✗ 行", async () => {
    evalMock.mockResolvedValue(report());
    render(<ForecastEvalCard symbol="BTC/USDT" interval="1h" />);

    await waitFor(() => expect(screen.getByText(/预测为实验性 · 未充分回测/)).toBeInTheDocument());
    // 命中率/基线/方向分布（同一数值可能同时出现在横幅与聚合行）
    expect(screen.getAllByText(/50\.0%/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/40\.0%/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/▲ 1 · ▼ 1/).length).toBeGreaterThan(0);
    // 命中行 ✓、未中行 ✗、无预测行
    expect(screen.getByText("✓")).toBeInTheDocument();
    expect(screen.getByText("✗")).toBeInTheDocument();
    expect(screen.getByText("无预测")).toBeInTheDocument();
  });

  it("无样本时空态提示", async () => {
    evalMock.mockResolvedValue({
      symbol: "BTC/USDT",
      interval: "1h",
      samples_total: 0,
      samples_with_forecast: 0,
      hit_rate_forecast: null,
      hit_rate_baseline: null,
      rows: [],
    });
    render(<ForecastEvalCard symbol="BTC/USDT" interval="1h" />);

    await waitFor(() => expect(screen.getByText(/暂无历史预测样本/)).toBeInTheDocument());
  });

  it("symbol/interval 变化时重新拉取", async () => {
    evalMock.mockResolvedValue(report());
    const { rerender } = render(<ForecastEvalCard symbol="BTC/USDT" interval="1h" />);
    await waitFor(() => expect(evalMock).toHaveBeenCalledWith("BTC/USDT", "1h"));

    rerender(<ForecastEvalCard symbol="ETH/USDT" interval="4h" />);
    await waitFor(() => expect(evalMock).toHaveBeenCalledWith("ETH/USDT", "4h"));
  });
});

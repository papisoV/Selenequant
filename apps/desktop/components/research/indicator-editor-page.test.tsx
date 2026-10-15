import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import type { ReactNode } from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { I18nProvider } from "@/components/i18n/i18n-provider";
import { IndicatorEditorPage, DEMO_FALLBACK_MS } from "./indicator-editor-page";
import { INDICATORS } from "@/lib/indicators";

vi.mock("@/lib/api-client", () => ({
  apiDirect: vi.fn().mockRejectedValue(new Error("backend down")),
}));

function renderWithProvider(ui: ReactNode) {
  return render(<I18nProvider>{ui}</I18nProvider>);
}

describe("IndicatorEditorPage(自定义指标编辑器)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("渲染模板画廊:指标卡数量≥实读 26 个模板", () => {
    const { container } = renderWithProvider(<IndicatorEditorPage />);
    expect(screen.getByText("自定义指标编辑器")).toBeTruthy();
    expect(container.querySelectorAll("[data-indicator-id]").length).toBeGreaterThanOrEqual(INDICATORS.length);
  });

  it("选指标 → 公式串正确渲染(默认参数)", () => {
    renderWithProvider(<IndicatorEditorPage />);
    fireEvent.click(screen.getByTestId("indicator-card-template-ema-cross"));
    expect(screen.getByText("EMA Crossover( 快线 EMA = 12, 慢线 EMA = 26 )")).toBeTruthy();
  });

  it("改参数 → 公式串实时更新", () => {
    renderWithProvider(<IndicatorEditorPage />);
    fireEvent.click(screen.getByTestId("indicator-card-template-ema-cross"));
    fireEvent.change(screen.getByLabelText("快线 EMA"), { target: { value: "5" } });
    expect(screen.getByText("EMA Crossover( 快线 EMA = 5, 慢线 EMA = 26 )")).toBeTruthy();
  });

  it("运行回测 → 后端不可达 → 8s 后 demo 结果 + 演示徽章", async () => {
    const { container } = renderWithProvider(<IndicatorEditorPage />);
    fireEvent.click(screen.getByRole("button", { name: "运行回测" }));
    expect(container.querySelector(".backtest-result-panel")).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEMO_FALLBACK_MS);
    });
    expect(screen.getByText("演示")).toBeTruthy();
    expect(container.querySelector(".backtest-result-panel")).toBeTruthy();
    expect(screen.getByText("总收益")).toBeTruthy();
  });
});

import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";

import { useDrawingsStore } from "@/lib/store/drawings";
import { useMarketStore } from "@/lib/store/market";
import { CHART_APPEARANCE_KEY, DEFAULT_CHART_APPEARANCE } from "@/lib/chart/chart-appearance";

/**
 * 图表外观的**组件级**回归锁(1022)。
 *
 * 为什么纯函数测试(chart-appearance.test.ts)不够:它只保证"给定外观 → 选项对象
 * 长这样"。真正会坏的是接线 —— 建图时没取快照、applyOptions effect 的依赖漏了、
 * 或者干脆把外观塞进 createChart 的依赖数组(那会导致每改一次色就重建整张图,
 * 视区回到默认位置)。这三条纯函数测试全绿时依然可能发生。
 *
 * 所以这里把 lightweight-charts 换成**可观测的假图**,驱动真 KfChart:
 *   1. 建图时的 createChart 参数 = 当前外观;
 *   2. 改外观 → chart/series 各收到一次 applyOptions,且**没有第二次 createChart**;
 *   3. 蜡烛六色走的是 LWC 的 *Color 键,不是 store 的短键。
 */

const h = vi.hoisted(() => ({
  createCalls: [] as Array<Record<string, unknown>>,
  seriesCreateCalls: [] as Array<Record<string, unknown>>,
  chartApplyCalls: [] as Array<Record<string, unknown>>,
  seriesApplyCalls: [] as Array<Record<string, unknown>>,
  created: 0,
}));

vi.mock("lightweight-charts", () => {
  const mkSeries = () => ({
    setData: vi.fn(),
    update: vi.fn(),
    applyOptions: (o: Record<string, unknown>) => h.seriesApplyCalls.push(o),
    options: () => ({ visible: true }),
    priceScale: () => ({ applyOptions: vi.fn() }),
    createPriceLine: vi.fn(() => ({})),
    removePriceLine: vi.fn(),
    coordinateToPrice: () => 100,
    priceToCoordinate: () => 100,
  });
  return {
    CandlestickSeries: "CandlestickSeries",
    LineSeries: "LineSeries",
    HistogramSeries: "HistogramSeries",
    CrosshairMode: { Normal: 1, Magnet: 0 },
    LineStyle: { Solid: 0, Dotted: 2 },
    ColorType: { Solid: "solid" },
    createSeriesMarkers: () => ({ setMarkers: vi.fn() }),
    createChart: (_el: HTMLElement, opts: Record<string, unknown>) => {
      h.created += 1;
      h.createCalls.push(opts);
      return {
        addSeries: (_kind: string, seriesOpts: Record<string, unknown>) => {
          h.seriesCreateCalls.push(seriesOpts);
          return mkSeries();
        },
        panes: () => [],
        removeSeries: vi.fn(),
        applyOptions: (o: Record<string, unknown>) => h.chartApplyCalls.push(o),
        subscribeClick: vi.fn(),
        subscribeCrosshairMove: vi.fn(),
        remove: vi.fn(),
        timeScale: () => ({
          fitContent: vi.fn(),
          getVisibleLogicalRange: () => ({ from: 0, to: 10 }),
          setVisibleLogicalRange: vi.fn(),
          subscribeVisibleLogicalRangeChange: vi.fn(),
          unsubscribeVisibleLogicalRangeChange: vi.fn(),
          subscribeSizeChange: vi.fn(),
          unsubscribeSizeChange: vi.fn(),
          applyOptions: vi.fn(),
        }),
      };
    },
  };
});

const BARS = Array.from({ length: 40 }, (_, i) => ({
  open_time: (1792000000 + i * 3600) * 1000,
  open: 100 + i,
  high: 105 + i,
  low: 95 + i,
  close: 101 + i,
  volume: 1000 + i,
}));

vi.mock("@/lib/hooks/use-klines", () => ({
  useKlines: () => ({
    klines: BARS,
    fromCache: false,
    source: "exchange",
    error: null,
    isLoading: false,
    mutate: async () => undefined,
    loadMore: async () => 0,
  }),
}));

import { KfChart } from "./kf-chart";

const store = () => useDrawingsStore.getState();

beforeEach(() => {
  h.createCalls.length = 0;
  h.seriesCreateCalls.length = 0;
  h.chartApplyCalls.length = 0;
  h.seriesApplyCalls.length = 0;
  h.created = 0;
  store().resetForTest();
  localStorage.clear();
  useMarketStore.setState({
    symBase: "BTC",
    pageFilter: { tf: "1h", symbol: "BTC", exchange: "all" },
    marketsStreamState: "live",
    lastWsKline: null,
  });
});

describe("KfChart 消费图表外观(组件级)", () => {
  it("建图时用当前外观:背景透明、网格用内置色、蜡烛色走 *Color 键", () => {
    render(<KfChart />);

    expect(h.createCalls).toHaveLength(1);
    const layout = h.createCalls[0].layout as { background: { color: string } };
    expect(layout.background.color).toBe("transparent");

    const grid = h.createCalls[0].grid as {
      vertLines: { color: string; visible: boolean };
      horzLines: { color: string; visible: boolean };
    };
    expect(grid.vertLines).toEqual({ color: "rgba(255,255,255,0.04)", visible: true });
    expect(grid.horzLines).toEqual({ color: "rgba(255,255,255,0.04)", visible: true });

    // 第一个 addSeries 是蜡烛 —— 它拿到的必须是 LWC 的键名
    expect(h.seriesCreateCalls[0]).toEqual({
      upColor: DEFAULT_CHART_APPEARANCE.candles.up,
      downColor: DEFAULT_CHART_APPEARANCE.candles.down,
      borderUpColor: DEFAULT_CHART_APPEARANCE.candles.borderUp,
      borderDownColor: DEFAULT_CHART_APPEARANCE.candles.borderDown,
      wickUpColor: DEFAULT_CHART_APPEARANCE.candles.wickUp,
      wickDownColor: DEFAULT_CHART_APPEARANCE.candles.wickDown,
    });
  });

  it("改外观 → 就地在 chart/series 上 applyOptions,不重建图", () => {
    render(<KfChart />);
    expect(h.created).toBe(1);

    act(() =>
      store().setAppearance({
        background: { kind: "solid", color: "#101418" },
        gridVisible: false,
        candles: { ...DEFAULT_CHART_APPEARANCE.candles, up: "#26a69a" },
      }),
    );

    // 关键:没有第二次 createChart(重建会丢视区 / 重订阅)
    expect(h.created).toBe(1);

    const lastChart = h.chartApplyCalls[h.chartApplyCalls.length - 1];
    expect((lastChart.layout as { background: { color: string } }).background.color).toBe("#101418");
    expect((lastChart.grid as { vertLines: { visible: boolean } }).vertLines.visible).toBe(false);

    const lastSeries = h.seriesApplyCalls[h.seriesApplyCalls.length - 1];
    expect(lastSeries.upColor).toBe("#26a69a");
    // 未改的颜色不能被"顺手"重置
    expect(lastSeries.downColor).toBe(DEFAULT_CHART_APPEARANCE.candles.down);
  });

  it("网格自定义色生效;清空后回到内置色", () => {
    render(<KfChart />);

    act(() => store().setAppearance({ gridColor: "#222831" }));
    expect(
      (h.chartApplyCalls[h.chartApplyCalls.length - 1].grid as { horzLines: { color: string } })
        .horzLines.color,
    ).toBe("#222831");

    act(() => store().setAppearance({ gridColor: "" }));
    expect(
      (h.chartApplyCalls[h.chartApplyCalls.length - 1].grid as { horzLines: { color: string } })
        .horzLines.color,
    ).toBe("rgba(255,255,255,0.04)");
  });

  it("已落盘的外观在刷新后会生效(不只是内存里改一下)", () => {
    // 模拟"上次会话改了背景色" → 刷新 → hydrate 读回来 → 建图用它
    localStorage.setItem(
      CHART_APPEARANCE_KEY,
      JSON.stringify({
        ...DEFAULT_CHART_APPEARANCE,
        background: { kind: "solid", color: "#0b0f14" },
      }),
    );
    act(() => store().hydrate());

    render(<KfChart />);
    expect(
      (h.createCalls[0].layout as { background: { color: string } }).background.color,
    ).toBe("#0b0f14");
  });
});

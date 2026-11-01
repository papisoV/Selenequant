import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render } from "@testing-library/react";

import { useDrawingsStore } from "@/lib/store/drawings";
import { useMarketStore } from "@/lib/store/market";

/**
 * Delete 删对象的**端到端**回归锁(1022)。
 *
 * 纯函数测试(lib/chart/drawing-keys.test.ts)锁的是"给定键盘事件与上下文 → 该发什么指令"。
 * 这里要锁的是接线:图层真的把 window keydown 挂上了、真的把 store 的选中集喂进去、
 * 真的调到了 removeMany。任何一环断了纯函数测试都还是绿的。
 *
 * 走真 KfChart(lightweight-charts 换成假图),因为 keydown 监听是挂在整个文档上的,
 * 只有一张图真的在页面上它才会注册。
 */

const h = vi.hoisted(() => ({ created: 0 }));

vi.mock("lightweight-charts", () => {
  const mkSeries = () => ({
    setData: vi.fn(),
    update: vi.fn(),
    applyOptions: vi.fn(),
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
    createChart: () => {
      h.created += 1;
      return {
        addSeries: () => mkSeries(),
        panes: () => [],
        removeSeries: vi.fn(),
        applyOptions: vi.fn(),
        subscribeClick: vi.fn(),
        subscribeCrosshairMove: vi.fn(),
        remove: vi.fn(),
        paneSize: () => ({ width: 600, height: 400 }),
        timeScale: () => ({
          fitContent: vi.fn(),
          getVisibleLogicalRange: () => ({ from: 0, to: 10 }),
          setVisibleLogicalRange: vi.fn(),
          subscribeVisibleLogicalRangeChange: vi.fn(),
          unsubscribeVisibleLogicalRangeChange: vi.fn(),
          subscribeSizeChange: vi.fn(),
          unsubscribeSizeChange: vi.fn(),
          logicalToCoordinate: (n: number) => n * 10,
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

const INPUT = {
  start: { time: 1792000000, price: 100 },
  end: { time: 1792003600, price: 110 },
  symbol: "BTC",
  timeframe: "1h",
};

beforeEach(() => {
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

function draw(): string {
  return store().createFromDrag(INPUT);
}

describe("Delete 删除选中对象(KfChart + DrawingLayer 接线)", () => {
  it("选中后按 Delete → 对象从 store 里消失", () => {
    render(<KfChart />);
    const id = draw();
    act(() => store().select(id));
    expect(store().grids).toHaveLength(1);

    act(() => {
      fireEvent.keyDown(window, { key: "Delete" });
    });

    expect(store().grids).toHaveLength(0);
    expect(store().selectedIds.size).toBe(0);
  });

  it("Backspace 同效", () => {
    render(<KfChart />);
    const id = draw();
    act(() => store().select(id));

    act(() => {
      fireEvent.keyDown(window, { key: "Backspace" });
    });
    expect(store().grids).toHaveLength(0);
  });

  it("没选中时按 Delete 什么都不删", () => {
    render(<KfChart />);
    draw();
    act(() => store().clearSelection());

    act(() => {
      fireEvent.keyDown(window, { key: "Delete" });
    });
    expect(store().grids).toHaveLength(1);
  });

  it("锁定的对象不被 Delete 删掉(锁定就是防误删)", () => {
    render(<KfChart />);
    const id = draw();
    act(() => {
      store().select(id);
      store().setLockedOf([id], true);
    });

    act(() => {
      fireEvent.keyDown(window, { key: "Delete" });
    });
    expect(store().grids).toHaveLength(1);
  });

  it("多选时一次删掉全部选中项,未选中的留下", () => {
    render(<KfChart />);
    const a = draw();
    const b = draw();
    const c = draw();
    act(() => {
      store().select(a);
      store().toggleSelect(b);
    });

    act(() => {
      fireEvent.keyDown(window, { key: "Delete" });
    });
    expect(store().grids.map((g) => g.id)).toEqual([c]);
  });

  it("在输入框里按 Delete 只删文字,不删图上的对象", () => {
    render(<KfChart />);
    const id = draw();
    act(() => store().select(id));

    const input = document.createElement("input");
    document.body.appendChild(input);
    act(() => {
      fireEvent.keyDown(input, { key: "Delete" });
    });
    document.body.removeChild(input);

    expect(store().grids).toHaveLength(1);
  });

  it("⌘/Ctrl+Delete 不触发(留给系统/浏览器)", () => {
    render(<KfChart />);
    const id = draw();
    act(() => store().select(id));

    act(() => {
      fireEvent.keyDown(window, { key: "Delete", metaKey: true });
    });
    expect(store().grids).toHaveLength(1);
  });

  it("Escape 先把工具卸下,再按一次才清选中;工具态下不清选中", () => {
    render(<KfChart />);
    const id = draw();
    act(() => {
      store().select(id);
      store().setActiveTool("trend");
    });

    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    // 第一级:工具退回选择态,选中集与对象都还在
    expect(store().activeTool).toBe("select");
    expect(store().selectedIds.size).toBe(1);

    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    // 第二级:清选中,对象还在
    expect(store().selectedIds.size).toBe(0);
    expect(store().grids).toHaveLength(1);
  });
});

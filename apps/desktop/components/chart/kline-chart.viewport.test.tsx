import { describe, expect, it, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";

/**
 * 视口重置治本（批3.4）：`fitContent()` 每秒被调一次，缩放/平移一放手就弹回。
 *
 * 病因是三件事叠加：父组件每秒重渲染 → bars 每次都是新数组 → KlineChart 的
 * 灌数据 effect 依赖 `[candles]` 因此每秒重跑 → 重跑里带着 `fitContent()`。
 *
 * 判据不是"fitContent 被调过"（首帧当然要调），而是**首帧之后不再调**：
 * 同标的同周期的数据刷新属于流式更新，必须保持用户当前的视口。
 * 只有首次拿到数据、或换了标的/周期，才允许重新自适应一次。
 */

const hooks = {
  setData: vi.fn(),
  fitContent: vi.fn(),
  remove: vi.fn(),
  applyOptions: vi.fn(),
  seriesCount: 0,
};

vi.mock("lightweight-charts", () => {
  const mkSeries = () => {
    hooks.seriesCount += 1;
    return {
      setData: hooks.setData,
      priceScale: () => ({ applyOptions: vi.fn() }),
      createPriceLine: vi.fn(),
      applyOptions: vi.fn(),
    };
  };
  return {
    CandlestickSeries: "CandlestickSeries",
    LineSeries: "LineSeries",
    HistogramSeries: "HistogramSeries",
    AreaSeries: "AreaSeries",
    CrosshairMode: { Normal: 1, Magnet: 0 },
    ColorType: { Solid: "solid" },
    createChart: () => ({
      addSeries: mkSeries,
      subscribeCrosshairMove: vi.fn(),
      timeScale: () => ({ fitContent: hooks.fitContent }),
      applyOptions: hooks.applyOptions,
      remove: hooks.remove,
    }),
  };
});

import { KlineChart, type Candle } from "./kline-chart";

const mkBars = (n: number, base = 100): Candle[] =>
  Array.from({ length: n }, (_, i) => ({
    open: base + i,
    high: base + 8 + i,
    low: base - 5 + i,
    close: base + 4 + i,
    up: i % 2 === 0,
    volume: 1000 + i,
    t: 1792000000 + i * 3600,
  }));

beforeEach(() => {
  hooks.seriesCount = 0;
  hooks.setData.mockClear();
  hooks.fitContent.mockClear();
  hooks.remove.mockClear();
});

describe("KlineChart 视口握持（批3.4）", () => {
  it("首帧拿到数据时自适应一次", () => {
    render(<KlineChart sym="AAPL" tf="1d" bars={mkBars(10)} />);
    expect(hooks.fitContent).toHaveBeenCalledTimes(1);
  });

  it("同标的同周期的数据刷新不再 fitContent（否则每秒把用户缩放按回去）", () => {
    const { rerender } = render(<KlineChart sym="AAPL" tf="1d" bars={mkBars(10)} />);
    hooks.fitContent.mockClear();

    // 模拟 1s tick 带来的流式更新：同一标的/周期，末尾多一根
    rerender(<KlineChart sym="AAPL" tf="1d" bars={mkBars(11)} />);
    rerender(<KlineChart sym="AAPL" tf="1d" bars={mkBars(12)} />);

    expect(hooks.fitContent).not.toHaveBeenCalled();
    // 数据本身仍要刷新（别把"不重置视口"做成"不更新数据"）
    expect(hooks.setData).toHaveBeenCalled();
  });

  it("换标的时重新自适应（新标的该有自己的视口）", () => {
    const { rerender } = render(<KlineChart sym="AAPL" tf="1d" bars={mkBars(10)} />);
    hooks.fitContent.mockClear();

    rerender(<KlineChart sym="MSFT" tf="1d" bars={mkBars(10, 300)} />);

    expect(hooks.fitContent).toHaveBeenCalledTimes(1);
  });

  it("换周期时重新自适应（K 线粒度变了，旧视口没有意义）", () => {
    const { rerender } = render(<KlineChart sym="AAPL" tf="1d" bars={mkBars(10)} />);
    hooks.fitContent.mockClear();

    rerender(<KlineChart sym="AAPL" tf="1h" bars={mkBars(10)} />);

    expect(hooks.fitContent).toHaveBeenCalledTimes(1);
  });

  it("空 → 有数据时自适应一次（首次真正画出内容）", () => {
    const { rerender } = render(
      <KlineChart sym="AAPL" tf="1d" bars={[]} allowFallback={false} />
    );
    hooks.fitContent.mockClear();

    rerender(<KlineChart sym="AAPL" tf="1d" bars={mkBars(10)} allowFallback={false} />);

    expect(hooks.fitContent).toHaveBeenCalledTimes(1);
  });

  it("空数组重渲染不反复自适应（空态每秒 tick 也不该动作）", () => {
    const { rerender } = render(
      <KlineChart sym="AAPL" tf="1d" bars={[]} allowFallback={false} />
    );
    hooks.fitContent.mockClear();

    rerender(<KlineChart sym="AAPL" tf="1d" bars={[]} allowFallback={false} />);

    expect(hooks.fitContent).not.toHaveBeenCalled();
  });
});

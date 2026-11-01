import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, render } from "@testing-library/react";
import { useMarketStore } from "@/lib/store/market";

/**
 * 换标的残留的**组件级**回归锁（2026-10-15 实机缺陷）。
 *
 * 为什么单测 pickRenderPath 不够：纯函数测试只锁住"给定 key 不同要返回 full"，
 * 但没有任何东西保证组件真的把 key 传进去、真的在 skip 之前不提前 return、
 * 真的在出口处拒绝旧标的的行。删掉 `pickRenderPath(..., dataKey)` 的第四个实参、
 * 删掉 `dataKeyRef` 的两处出口校验，纯函数测试仍全绿，缺陷照样复发。
 *
 * 这里把 lightweight-charts 换成可观测的假图，驱动**真 KfChart**，断言：
 *   1. 形状完全相同（同根数、同末根时间戳）但身份不同 → 画布必须重画（setData 再次被调用
 *      且喂进去的是新标的的价格）。
 *   2. 换标的后新数据还没到的那一帧，旧标的的行还在 rowsRef 里 —— 此时
 *      tickerLast（旧标的的价）绝不能写进旧标的的末根，否则就是"旧 OHLC + 新 close"
 *      的嵌合 K 线。
 *
 * 这两条对应的都是用户看得见的现象：K 线滞留、图挂在旧标的上、蜡烛实体错乱。
 */

const h = vi.hoisted(() => ({
  klinesBySymbol: {} as Record<string, unknown[]>,
  setDataCalls: [] as Array<{ id: number; data: unknown[] }>,
  updateCalls: [] as Array<{ id: number; data: Record<string, unknown> }>,
  seriesSeq: 0,
  fitContentCalls: 0,
}));

vi.mock("lightweight-charts", () => {
  const mkSeries = () => {
    const id = h.seriesSeq++;
    return {
      setData: (data: unknown[]) => h.setDataCalls.push({ id, data }),
      update: (data: Record<string, unknown>) => h.updateCalls.push({ id, data }),
      applyOptions: vi.fn(),
      options: () => ({ visible: true }),
      priceScale: () => ({ applyOptions: vi.fn() }),
      createPriceLine: vi.fn(() => ({})),
      removePriceLine: vi.fn(),
      coordinateToPrice: () => 100,
      priceToCoordinate: () => 100,
    };
  };
  return {
    CandlestickSeries: "CandlestickSeries",
    LineSeries: "LineSeries",
    HistogramSeries: "HistogramSeries",
    CrosshairMode: { Normal: 1, Magnet: 0 },
    LineStyle: { Solid: 0, Dotted: 2 },
    ColorType: { Solid: "solid" },
    // ICT 结构层：swing 标记插件。合并 yanjiu 后 KfChart 挂载时会调用它，
    // 缺这个导出会让整个组件在 effect 里抛错（其余用例一起红）。
    createSeriesMarkers: () => ({ setMarkers: vi.fn() }),
    createChart: () => ({
      addSeries: mkSeries,
      panes: () => [],
      removeSeries: vi.fn(),
      applyOptions: vi.fn(),
      subscribeClick: vi.fn(),
      subscribeCrosshairMove: vi.fn(),
      remove: vi.fn(),
      timeScale: () => ({
        fitContent: () => {
          h.fitContentCalls += 1;
        },
        getVisibleLogicalRange: () => ({ from: 0, to: 10 }),
        setVisibleLogicalRange: vi.fn(),
        subscribeVisibleLogicalRangeChange: vi.fn(),
        unsubscribeVisibleLogicalRangeChange: vi.fn(),
        // 绘图层（DrawingLayer）订阅尺寸变化来重算叠加画布
        subscribeSizeChange: vi.fn(),
        unsubscribeSizeChange: vi.fn(),
      }),
    }),
  };
});

// 数据源按 symbol 返回。测试通过改 h.klinesBySymbol 决定"新标的数据到了没有"。
// 每次调用返回新数组：真实 SWR 每次 revalidate 都换引用，且图表的数据 effect
// 靠引用变化重跑（首次挂载时容器还没建好，effect 会提前 return）。
vi.mock("@/lib/hooks/use-klines", () => ({
  useKlines: (symbol: string | null) => ({
    klines: [...((symbol && h.klinesBySymbol[symbol]) || [])],
    fromCache: false,
    source: "exchange",
    error: null,
    isLoading: false,
    mutate: async () => undefined,
    loadMore: async () => 0,
  }),
}));

import { KfChart } from "./kf-chart";

const T0 = 1792000000; // 任意整点，秒
const N = 60;

/** 造 N 根 1h bar。价格围绕 base 浮动 —— 两个标的形状相同、价格区间不同。 */
function bars(base: number) {
  return Array.from({ length: N }, (_, i) => ({
    open_time: (T0 + i * 3600) * 1000,
    open: base + i,
    high: base + i + 5,
    low: base + i - 5,
    close: base + i + 1,
    volume: 1000 + i,
  }));
}

/** 蜡烛 series 是图表上第一个 addSeries 出来的（id 0，见 createChart 的挂载顺序）。 */
function candleSetDataCalls() {
  return h.setDataCalls.filter((c) => c.id === 0);
}

function setSymbol(symbol: string) {
  act(() => {
    useMarketStore.setState({
      symBase: symbol,
      pageFilter: { tf: "1h", symbol, exchange: "all" },
    });
  });
}

describe("KfChart 换标的残留（组件级）", () => {
  beforeEach(() => {
    h.klinesBySymbol = {};
    h.setDataCalls.length = 0;
    h.updateCalls.length = 0;
    h.seriesSeq = 0;
    h.fitContentCalls = 0;
    useMarketStore.setState({
      symBase: "BTC",
      pageFilter: { tf: "1h", symbol: "BTC", exchange: "all" },
      marketsStreamState: "recovering",
      lastWsKline: null,
    });
  });

  it("★形状完全相同但标的变了 → 画布必须重画，且画的是新标的的价", () => {
    // 真实场景：交易所对任何标的都返回"最近 N 根、末根对齐当前时刻"，
    // 所以 BTC 与 ETH 的 (根数, 首根时间, 末根时间) 天然完全相同。
    h.klinesBySymbol.BTCUSDT = bars(100);
    h.klinesBySymbol.ETHUSDT = bars(2000);

    render(<KfChart />);

    const first = candleSetDataCalls();
    expect(first.length).toBeGreaterThan(0);
    const btcCloses = first[first.length - 1].data
      .filter((d) => (d as { close?: number }).close != null)
      .map((d) => (d as { close: number }).close);
    expect(btcCloses[0]).toBe(101); // bars(100)[0].close
    expect(Math.max(...btcCloses)).toBeLessThan(1000);

    const callsBeforeSwitch = candleSetDataCalls().length;

    // 换标的：不重挂载组件（symbolOverride 路径 / pa-panel 这类调用点没有外层 key）
    setSymbol("ETH");

    const after = candleSetDataCalls();
    expect(after.length).toBeGreaterThan(callsBeforeSwitch);

    const ethCloses = after[after.length - 1].data
      .filter((d) => (d as { close?: number }).close != null)
      .map((d) => (d as { close: number }).close);
    expect(ethCloses[0]).toBe(2001); // bars(2000)[0].close
    expect(Math.min(...ethCloses)).toBeGreaterThan(1000);
  });

  it("★换标的后新数据未到时，旧标的的末根不得被新标的的实时价改写（嵌合 K 线）", () => {
    h.klinesBySymbol.BTCUSDT = bars(100);
    // ETH 此时**还没有数据**（SWR 新 key 首帧返回空），rowsRef 里仍是 BTC 的行。
    h.klinesBySymbol.ETHUSDT = [];

    const { rerender } = render(<KfChart tickerLast={105} />);
    const btcCalls = candleSetDataCalls();
    expect(btcCalls.length).toBeGreaterThan(0);

    const updatesBefore = h.updateCalls.length;

    // 换标的，同时 tickerLast 还是上一个标的的价（页面上的 wsTicker 就是这种状态：
    // 新标的的价要等 WS/REST 回来才有，这中间窗口属于旧标的）。
    act(() => useMarketStore.setState({ pageFilter: { tf: "1h", symbol: "ETH", exchange: "all" } }));
    rerender(<KfChart tickerLast={1999} />);

    // 出口锁：rowsRef 仍属于 BTC，dataKey 已是 ETH → 整条 ticker 通道不执行。
    const chimera = h.updateCalls
      .slice(updatesBefore)
      .filter((u) => u.data.close === 1999);
    expect(chimera).toEqual([]);
  });

  it("同标的同数据重复渲染 → 不重画（增量优化没被顺手丢掉）", () => {
    h.klinesBySymbol.BTCUSDT = bars(100);
    const { rerender } = render(<KfChart />);
    const n = candleSetDataCalls().length;
    expect(n).toBeGreaterThan(0);

    rerender(<KfChart />);
    expect(candleSetDataCalls().length).toBe(n);
  });
});

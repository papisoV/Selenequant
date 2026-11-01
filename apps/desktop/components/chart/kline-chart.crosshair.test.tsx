import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, act } from "@testing-library/react";

/**
 * 批2.3：十字线交互的可见证据。
 *
 * 迁移到 lightweight-charts 之后，十字线不再是我们自己的 SVG 节点，而是 LWC 内部
 * 画进 canvas 的 —— jsdom 既没有真指针也没有 canvas 后端，直接 render 的话
 * "悬停" 这个动作根本没法发生。所以这里把 lightweight-charts 整个换成可驱动的假图：
 * 捕获组件注册的 crosshair 回调，手动喂 { time, point }，验证组件侧的响应
 * （tooltip 内容、显隐、贴边翻转）与"移出即隐藏"。LWC 自己画的十字线由 e2e 截图背书。
 */
const hooks = {
  crosshairCb: null as ((p: unknown) => void) | null,
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
      subscribeCrosshairMove: (cb: (p: unknown) => void) => {
        hooks.crosshairCb = cb;
      },
      timeScale: () => ({ fitContent: hooks.fitContent }),
      applyOptions: hooks.applyOptions,
      remove: hooks.remove,
    }),
  };
});

import { KlineChart, type Candle } from "./kline-chart";

// t 必须显式给：tooltip 的 Map 以秒级时间为键，没有 t 会走兜底时间基，
// 测试里就得重复实现那套基址推算，反而测不出真路径。
const bars: Candle[] = Array.from({ length: 10 }, (_, i) => ({
  open: 100 + i,
  high: 108 + i,
  low: 95 + i,
  close: 104 + i,
  up: i % 2 === 0,
  volume: 1000 + i,
  t: 1792000000 + i * 3600,
}));

const T = (i: number) => 1792000000 + i * 3600;

function mount(clientWidth = 600) {
  const { container } = render(<KlineChart sym="BTC" tf="1h" bars={bars} />);
  const host = screen.getByTestId("kline-canvas");
  Object.defineProperty(host, "clientWidth", { value: clientWidth, configurable: true });
  Object.defineProperty(host, "clientHeight", { value: 300, configurable: true });
  return { container, host };
}

const hover = (i: number, x = 200, y = 120) =>
  act(() => hooks.crosshairCb?.({ time: T(i), point: { x, y } }));

const leave = () => act(() => hooks.crosshairCb?.({ time: undefined, point: undefined }));

beforeEach(() => {
  hooks.crosshairCb = null;
  hooks.seriesCount = 0;
  hooks.setData.mockClear();
  hooks.fitContent.mockClear();
  hooks.remove.mockClear();
});

describe("KlineChart 十字线交互（批2.3）", () => {
  it("建图时注册了十字线回调，且 K线/MA/量 三条 series 都灌了数据", () => {
    mount();
    expect(hooks.crosshairCb).toBeTypeOf("function");
    expect(hooks.seriesCount).toBe(3);
    expect(hooks.setData).toHaveBeenCalledTimes(3); // 蜡烛 + MA + 量
    expect(hooks.fitContent).toHaveBeenCalled();
  });

  it("未悬停时 tooltip 隐藏", () => {
    mount();
    expect(screen.getByTestId("kline-cross-tip").style.display).toBe("none");
  });

  it("悬停第 1 根时显示的是第 1 根而非最后一根", () => {
    mount();
    hover(1);
    const tip = screen.getByTestId("kline-cross-tip");
    expect(tip.style.display).toBe("block");
    // 第 1 根 O=101 H=109 L=96 C=105；末根 O=109 H=117 L=104 C=113。
    // 用 C 判别：第 1 根的 H=109 恰好等于末根的 O，拿 H/O 做判别会撞车。
    expect(tip.textContent).toContain("C 105");
    expect(tip.textContent).not.toContain("C 113");
  });

  it("悬停另一根时内容随之切换（不是停在第一次的结果）", () => {
    mount();
    hover(1);
    expect(screen.getByTestId("kline-cross-tip").textContent).toContain("C 105");
    hover(5);
    const tip = screen.getByTestId("kline-cross-tip");
    expect(tip.textContent).toContain("C 109"); // 第 5 根 close=109
    expect(tip.textContent).not.toContain("C 105");
  });

  it("光标移出图外（time 为 undefined）后 tooltip 隐藏，不留残影", () => {
    mount();
    hover(3);
    expect(screen.getByTestId("kline-cross-tip").style.display).toBe("block");
    leave();
    expect(screen.getByTestId("kline-cross-tip").style.display).toBe("none");
  });

  it("光标落在空白区（time 不在数据里）也隐藏，不显示上一根的陈旧值", () => {
    mount();
    hover(3);
    expect(screen.getByTestId("kline-cross-tip").style.display).toBe("block");
    act(() => hooks.crosshairCb?.({ time: T(3) + 17, point: { x: 300, y: 100 } }));
    expect(screen.getByTestId("kline-cross-tip").style.display).toBe("none");
  });

  it("右侧贴边时 tooltip 翻到光标左侧，不被容器裁掉", () => {
    const { host } = mount(600);
    hover(5, 560); // 560 + 140 + 12 > 600 → 翻转
    const tip = screen.getByTestId("kline-cross-tip");
    expect(parseFloat(tip.style.left)).toBeLessThan(560);
    // 左边界兜底：翻过去也不能出负值
    Object.defineProperty(host, "clientWidth", { value: 300, configurable: true });
    hover(5, 10);
    expect(parseFloat(screen.getByTestId("kline-cross-tip").style.left)).toBeGreaterThanOrEqual(4);
  });

  it("卸载时移除图表（LWC 实例必须 remove，否则 canvas 与订阅泄漏）", () => {
    const { unmount } = render(<KlineChart sym="BTC" tf="1h" bars={bars} />);
    expect(hooks.remove).not.toHaveBeenCalled();
    unmount();
    expect(hooks.remove).toHaveBeenCalledTimes(1);
  });

  it("数据变化只 setData 不重建图（重建会丢视区与十字线订阅）", () => {
    const { rerender } = render(<KlineChart sym="BTC" tf="1h" bars={bars} />);
    const created = hooks.seriesCount;
    hooks.setData.mockClear();
    rerender(<KlineChart sym="BTC" tf="1h" bars={bars.map((b) => ({ ...b, close: b.close + 1 }))} />);
    expect(hooks.seriesCount).toBe(created); // 没多建 series = 没重建图
    expect(hooks.setData).toHaveBeenCalledTimes(3); // 三条序列各自重灌
  });
});

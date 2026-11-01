import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { KlineChart, buildTipHtml, type Candle } from "./kline-chart";
import { chartColors } from "@/lib/theme/chart-colors";

const bars: Candle[] = Array.from({ length: 10 }, (_, i) => ({
  open: 100 + i,
  high: 108 + i,
  low: 95 + i,
  close: 104 + i,
  up: true,
  volume: 1000 + i,
  t: 1792000000 + i * 3600,
}));

/**
 * 批2.3：渲染层换成 lightweight-charts 后，旧版"数 SVG 节点"的断言全部失效 ——
 * 十字线、K 线本体、成交量柱现在都画在 canvas 上，没有 DOM 节点可查。
 * 所以这里改测两类仍然真实可见的东西：
 *   1. buildTipHtml 的纯逻辑（涨跌色/正负号/百分比口径）
 *   2. 组件对外的 DOM 契约（可访问名、chips、布局按钮、空态、tooltip 挂载点）
 * 真正的十字线交互见 kline-chart.crosshair.test.tsx（那里把 LWC 换成可驱动的假图）。
 *
 * 数字口径：与加密图 kf-chart 的 tooltip 完全一致（maximumFractionDigits: 2，
 * 不做定长补零）—— 两张图同屏切换时读数不该有两种写法。
 */
describe("buildTipHtml（十字线 tooltip 内容）", () => {
  it("阳线：涨跌用 up 色，正号 + 百分比按开盘价算", () => {
    const html = buildTipHtml({ open: 100, high: 110, low: 95, close: 105, up: true }, 1792000000);
    expect(html).toContain(chartColors.up);
    expect(html).not.toContain(chartColors.down);
    expect(html).toContain(">100</span>"); // O
    expect(html).toContain(">110</span>"); // H
    expect(html).toContain(">95</span>"); // L
    expect(html).toContain(">105</span>"); // C
    expect(html).toContain("+5 (+5.00%)"); // 涨跌额 105-100，5/100
  });

  it("阴线：用 down 色且带负号，不出现错误的 + 号", () => {
    const html = buildTipHtml({ open: 105, high: 108, low: 98, close: 100, up: false }, 1792000000);
    expect(html).toContain(chartColors.down);
    expect(html).not.toContain(chartColors.up);
    expect(html).toContain("-5 (-4.76%)"); // -5/105
  });

  it("同位小数不被截断（310.11 这种真实报价要保持两位）", () => {
    const html = buildTipHtml({ open: 310.11, high: 311.26, low: 309.23, close: 311.16, up: true }, 1792000000);
    expect(html).toContain(">310.11</span>");
    expect(html).toContain(">311.26</span>");
    expect(html).toContain(">309.23</span>");
    expect(html).toContain("+1.05 (+0.34%)");
  });

  it("平盘：chg=0 归 up 色且带 + 号，涨跌额处不出现负号", () => {
    const html = buildTipHtml({ open: 100, high: 100, low: 100, close: 100, up: true }, 1792000000);
    expect(html).toContain(chartColors.up);
    expect(html).toContain("+0 (+0.00%)");
    expect(html).not.toContain("(-");
  });

  it("开盘价为 0 不做除零，百分比回落 0 而非 NaN/Infinity", () => {
    const html = buildTipHtml({ open: 0, high: 5, low: 0, close: 5, up: true }, 1792000000);
    expect(html).not.toContain("NaN");
    expect(html).not.toContain("Infinity");
    expect(html).toContain("+0.00%");
  });
});

describe("KlineChart DOM 契约（批2.3 LWC 迁移后）", () => {
  it("暴露 role=img + 可访问名，供 e2e/屏幕阅读器定位", () => {
    render(<KlineChart sym="BTC" tf="1h" bars={bars} />);
    expect(screen.getByRole("img", { name: "BTC-PERP K线图" })).toBeTruthy();
  });

  it("label 覆盖默认名（kf-page 用 'AAPL · 美股 · Yahoo · 股票' 这种带来源的标题）", () => {
    render(<KlineChart sym="AAPL" tf="1h" bars={bars} label="AAPL · 美股 · Yahoo · 股票" />);
    expect(screen.getByRole("img", { name: "AAPL · 美股 · Yahoo · 股票 K线图" })).toBeTruthy();
  });

  it("canvas 挂载点存在且挂在 .kline-main 内（LWC 要求容器有非零盒子）", () => {
    const { container } = render(<KlineChart sym="BTC" tf="1h" bars={bars} />);
    const host = screen.getByTestId("kline-canvas");
    expect(host).toBeTruthy();
    expect(container.querySelector(".kline-main")?.contains(host)).toBe(true);
  });

  it("tooltip 挂载点常驻但默认隐藏（LWC 的 canvas 不产生 DOM，只能靠这个 div 显示）", () => {
    render(<KlineChart sym="BTC" tf="1h" bars={bars} />);
    const tip = screen.getByTestId("kline-cross-tip");
    expect(tip.style.display).toBe("none");
    expect(tip.className).toContain("kf-cross-tip");
  });

  it("OHLC 读数条常显最新一根，带日期与涨跌", () => {
    const { container } = render(<KlineChart sym="BTC" tf="1h" bars={bars} />);
    const ohlc = container.querySelector(".kline-ohlc");
    expect(ohlc).toBeTruthy();
    // 末根 O=109 H=117 L=104 C=113
    expect(ohlc?.textContent).toContain("109.0");
    expect(ohlc?.textContent).toContain("113.0");
    expect(ohlc?.textContent).toContain("+4.0"); // 113-109
  });

  it("onTfChange 存在时 chips 是可点的 button，并回传原始 tf 值", () => {
    const onTfChange = vi.fn();
    render(
      <KlineChart
        sym="MSFT"
        tf="1h"
        bars={bars}
        tfOptions={["1h", "1d"]}
        tfLabels={{ "1h": "1H", "1d": "1D" }}
        onTfChange={onTfChange}
      />
    );
    const chip = screen.getByTestId("kline-tf-chip-1d");
    expect(chip.tagName).toBe("BUTTON");
    expect(chip.textContent).toBe("1D"); // 展示大写，回传原始小写
    fireEvent.click(chip);
    expect(onTfChange).toHaveBeenCalledWith("1d");
  });

  it("无 onTfChange 时 chips 退化为静态 span（不可点，不做假的可操作提示）", () => {
    render(<KlineChart sym="AU0" tf="1" bars={bars} tfOptions={["1", "5"]} />);
    const chip = screen.getByText("5");
    expect(chip.tagName).toBe("SPAN");
  });

  it("布局按钮是 button 且 aria-pressed 跟随选中态", () => {
    render(<KlineChart sym="BTC" tf="1h" bars={bars} />);
    const btn1 = screen.getByTitle("单图");
    const btn4 = screen.getByTitle("4 宫格");
    expect(btn1.tagName).toBe("BUTTON");
    expect(btn1.getAttribute("aria-pressed")).toBe("true");
    expect(btn4.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(btn4);
    expect(btn4.getAttribute("aria-pressed")).toBe("true");
    expect(btn1.getAttribute("aria-pressed")).toBe("false");
  });

  it("空数据（allowFallback=false）走无数据空态，不渲染 OHLC、不生成模拟 K 线", () => {
    const { container } = render(<KlineChart sym="BTC" tf="1h" bars={[]} allowFallback={false} />);
    expect(screen.getByText("无数据")).toBeTruthy();
    expect(container.querySelector(".kline-ohlc")).toBeNull();
    // canvas 挂载点仍在：容器不能随数据一起消失，否则重挂时 LWC 拿不到盒子
    expect(screen.getByTestId("kline-canvas")).toBeTruthy();
  });

  it("数据从空变为有：OHLC 从无到有（不因 setData 路径分叉而卡死）", () => {
    const { container, rerender } = render(<KlineChart sym="BTC" tf="1h" bars={[]} allowFallback={false} />);
    expect(container.querySelector(".kline-ohlc")).toBeNull();
    rerender(<KlineChart sym="BTC" tf="1h" bars={bars} allowFallback={false} />);
    expect(container.querySelector(".kline-ohlc")).toBeTruthy();
    expect(screen.queryByText("无数据")).toBeNull();
  });
});

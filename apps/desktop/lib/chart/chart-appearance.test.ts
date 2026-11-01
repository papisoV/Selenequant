import { describe, expect, it } from "vitest";

import {
  CHART_APPEARANCE_KEY,
  DEFAULT_CHART_APPEARANCE,
  appearanceToChartOptions,
  appearanceToSeriesOptions,
  cloneAppearance,
  defaultGridColor,
  isDefaultAppearance,
  parseChartAppearance,
  parseChartAppearanceJson,
  serializeChartAppearance,
  type ChartAppearance,
} from "@/lib/chart/chart-appearance";

const modified: ChartAppearance = {
  background: { kind: "solid", color: "#101418" },
  gridColor: "#222831",
  gridVisible: false,
  candles: {
    up: "#26a69a",
    down: "#ef5350",
    borderUp: "#1e8f85",
    borderDown: "#d94543",
    wickUp: "#26a69a",
    wickDown: "#ef5350",
  },
};

describe("chart-appearance", () => {
  it("默认 = 改造前的硬编码字面量(不动设置的人观感不变)", () => {
    expect(DEFAULT_CHART_APPEARANCE.background).toEqual({ kind: "transparent", color: "#04070b" });
    expect(DEFAULT_CHART_APPEARANCE.gridVisible).toBe(true);
    expect(DEFAULT_CHART_APPEARANCE.gridColor).toBe("");
    // 与改造前 kf-chart.tsx createChart / addSeries 的取值逐字一致
    expect(DEFAULT_CHART_APPEARANCE.candles).toEqual({
      up: "#61bd67",
      down: "#f3625d",
      borderUp: "#61bd67",
      borderDown: "#f3625d",
      wickUp: "#61bd67",
      wickDown: "#f3625d",
    });
  });

  it("与另外两套存档分开 —— 三个 key 互不相同", () => {
    expect(CHART_APPEARANCE_KEY).toBe("selenespace-chart-appearance");
    expect(CHART_APPEARANCE_KEY).not.toBe("selenespace-fib-drawings");
    expect(CHART_APPEARANCE_KEY).not.toBe("selenespace-drawing-prefs");
  });

  it("往返相等", () => {
    expect(parseChartAppearanceJson(serializeChartAppearance(modified))).toEqual(modified);
  });

  it("坏字段只回退那一个,不整包重置", () => {
    // 只有背景色写坏 → 蜡烛配色(用户改过的)必须原样保留
    const parsed = parseChartAppearance({
      background: { kind: "solid", color: "rebeccapurple" },
      gridVisible: false,
      candles: { up: "#26a69a" },
    });
    expect(parsed.background).toEqual({ kind: "solid", color: DEFAULT_CHART_APPEARANCE.background.color });
    expect(parsed.gridVisible).toBe(false);
    expect(parsed.candles.up).toBe("#26a69a");
    // 未提供的字段回落默认,而不是变成 undefined
    expect(parsed.candles.down).toBe(DEFAULT_CHART_APPEARANCE.candles.down);
  });

  it("gridColor 允许空串(表示用内置色),其它非法色串拒绝", () => {
    expect(parseChartAppearance({ gridColor: "" }).gridColor).toBe("");
    expect(parseChartAppearance({ gridColor: "rgba(0,0,0,0.1)" }).gridColor).toBe(
      DEFAULT_CHART_APPEARANCE.gridColor,
    );
  });

  it("background.kind 只认 'solid',其余一律 transparent", () => {
    for (const kind of ["gradient", "", null, 1, {}]) {
      expect(parseChartAppearance({ background: { kind, color: "#101418" } }).background.kind).toBe(
        "transparent",
      );
    }
    expect(parseChartAppearance({ background: { kind: "solid", color: "#101418" } }).background.kind).toBe(
      "solid",
    );
  });

  it("非对象 / 坏 JSON / 空串一律收敛为默认,不抛", () => {
    for (const bad of [null, undefined, 1, "x", [], true]) {
      expect(parseChartAppearance(bad)).toEqual(DEFAULT_CHART_APPEARANCE);
    }
    for (const bad of [null, "", "{", "null", "[1,2]"]) {
      expect(parseChartAppearanceJson(bad)).toEqual(DEFAULT_CHART_APPEARANCE);
    }
  });

  it("返回的是新对象,改嵌套字段不污染默认常量", () => {
    const a = parseChartAppearance(null);
    a.candles.up = "#ffffff";
    a.background.color = "#ffffff";
    expect(DEFAULT_CHART_APPEARANCE.candles.up).toBe("#61bd67");
    expect(DEFAULT_CHART_APPEARANCE.background.color).toBe("#04070b");
    expect(cloneAppearance(DEFAULT_CHART_APPEARANCE).candles).not.toBe(DEFAULT_CHART_APPEARANCE.candles);
  });

  it("翻译成图表 options:transparent 走透明的老路,solid 喂具体色", () => {
    expect(appearanceToChartOptions(DEFAULT_CHART_APPEARANCE).layout.background).toEqual({
      color: "transparent",
    });
    expect(appearanceToChartOptions(modified).layout.background).toEqual({ color: "#101418" });

    // 网格:没自定义色 → 用内置色;关掉可见性后 visible=false
    const d = appearanceToChartOptions(DEFAULT_CHART_APPEARANCE);
    expect(d.grid.vertLines.color).toBe(defaultGridColor());
    expect(d.grid.vertLines.visible).toBe(true);
    const m = appearanceToChartOptions(modified);
    expect(m.grid.vertLines).toEqual({ color: "#222831", visible: false });
    expect(m.grid.horzLines).toEqual({ color: "#222831", visible: false });
  });

  it("蜡烛六色逐字段映射到 LWC 的 *Color 键(不靠同名透传)", () => {
    // 键名必须逐个点名断言:store 用短键 `up`,LWC 要 `upColor`。
    // 若用 toEqual(modified.candles) 只测"透传",映射写反(up↔down)也能通过。
    expect(appearanceToSeriesOptions(modified)).toEqual({
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderUpColor: "#1e8f85",
      borderDownColor: "#d94543",
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
    });
    expect(appearanceToSeriesOptions(modified)).not.toBe(modified.candles);
  });

  it("isDefaultAppearance 能识别'改过又改回去'", () => {
    expect(isDefaultAppearance(DEFAULT_CHART_APPEARANCE)).toBe(true);
    expect(isDefaultAppearance(cloneAppearance(DEFAULT_CHART_APPEARANCE))).toBe(true);
    expect(isDefaultAppearance(modified)).toBe(false);
    // 只把背景色改成默认值以外的一个值,也必须判成"非默认"
    expect(
      isDefaultAppearance({
        ...cloneAppearance(DEFAULT_CHART_APPEARANCE),
        background: { kind: "transparent", color: "#04070b" },
        gridVisible: true,
      }),
    ).toBe(true);
    expect(
      isDefaultAppearance({ ...cloneAppearance(DEFAULT_CHART_APPEARANCE), gridVisible: false }),
    ).toBe(false);
  });
});

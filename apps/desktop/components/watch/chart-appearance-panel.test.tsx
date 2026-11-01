import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ChartAppearance } from "./chart-appearance-panel";
import {
  CHART_APPEARANCE_KEY,
  DEFAULT_CHART_APPEARANCE,
  appearanceToSeriesOptions,
  parseChartAppearanceJson,
} from "@/lib/chart/chart-appearance";
import { useDrawingsStore } from "@/lib/store/drawings";

const store = () => useDrawingsStore.getState();

beforeEach(() => {
  store().resetForTest();
  localStorage.clear();
});

/** 点调色板图标开面板,返回 dialog(portal 到 body,不在 render 容器里)。 */
function openPanel(): HTMLElement {
  render(<ChartAppearance />);
  fireEvent.click(screen.getByRole("button", { name: "图表外观" }));
  return screen.getByRole("dialog", { name: "图表外观" });
}

/** 某个颜色输入框。`aria-label` 就是它那一行的名字,颜色输入没有可见文字。 */
function colorInput(name: string): HTMLInputElement {
  const el = document.querySelector(`input[type="color"][aria-label="${name}"]`);
  if (!el) throw new Error(`没有「${name}」取色器`);
  return el as HTMLInputElement;
}

function hasColorInput(name: string): boolean {
  return document.querySelector(`input[type="color"][aria-label="${name}"]`) !== null;
}

describe("图表外观面板", () => {
  it("默认关着;点图标开,再点图标关", () => {
    render(<ChartAppearance />);
    const trigger = screen.getByRole("button", { name: "图表外观" });
    expect(screen.queryByRole("dialog", { name: "图表外观" })).toBeNull();
    expect(trigger.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(trigger);
    expect(screen.getByRole("dialog", { name: "图表外观" })).not.toBeNull();
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(trigger);
    expect(screen.queryByRole("dialog", { name: "图表外观" })).toBeNull();
  });

  it("Escape 收起面板", () => {
    openPanel();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "图表外观" })).toBeNull();
  });

  it("点面板外收起", () => {
    openPanel();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("dialog", { name: "图表外观" })).toBeNull();
  });

  it("默认背景是透明:不渲染取色器,提示说跟随主题", () => {
    const dialog = openPanel();
    const radios = dialog.querySelectorAll('[role="radio"]');
    expect(Array.from(radios).map((r) => r.getAttribute("aria-checked"))).toEqual(["true", "false"]);
    expect(hasColorInput("背景色")).toBe(false);
    expect(dialog.textContent).toContain("透明跟随站点主题");
  });

  it("切到纯色:出现取色器,默认色是改造前的 #04070b,并写进 store", () => {
    const dialog = openPanel();
    const solid = Array.from(dialog.querySelectorAll('[role="radio"]'))[1] as HTMLElement;
    fireEvent.click(solid);

    expect(store().appearance.background.kind).toBe("solid");
    const input = colorInput("背景色");
    expect(input.value.toLowerCase()).toBe(DEFAULT_CHART_APPEARANCE.background.color);
  });

  it("改背景色 → store 跟着变,且顺手把 kind 钉成 solid", () => {
    const dialog = openPanel();
    fireEvent.click(Array.from(dialog.querySelectorAll('[role="radio"]'))[1] as HTMLElement);
    fireEvent.change(colorInput("背景色"), { target: { value: "#101418" } });

    expect(store().appearance.background).toEqual({ kind: "solid", color: "#101418" });
    // hex 文案同步,用户能看到自己选的到底是什么值
    expect(dialog.textContent).toContain("#101418");
  });

  it("切回透明:背景色字段收起,颜色值仍保留(下次切纯色还在)", () => {
    const dialog = openPanel();
    fireEvent.click(Array.from(dialog.querySelectorAll('[role="radio"]'))[1] as HTMLElement);
    fireEvent.change(colorInput("背景色"), { target: { value: "#223344" } });
    fireEvent.click(Array.from(dialog.querySelectorAll('[role="radio"]'))[0] as HTMLElement);

    expect(store().appearance.background).toEqual({ kind: "transparent", color: "#223344" });
    expect(hasColorInput("背景色")).toBe(false);
  });

  it("网格开关写进 store;关掉时网格取色器禁用并显示「默认」", () => {
    const dialog = openPanel();
    const box = Array.from(dialog.querySelectorAll('input[type="checkbox"]'))[0] as HTMLInputElement;
    expect(box.checked).toBe(true);
    expect(dialog.textContent).toContain("默认");

    fireEvent.click(box);
    expect(store().appearance.gridVisible).toBe(false);
    // 禁用而不是消失:位置不跳,用户知道"关掉的是这一项"
    expect(colorInput("网格颜色").disabled).toBe(true);
  });

  it("网格颜色留空 = 跟随内置色;选了具体色才落进 gridColor", () => {
    openPanel();
    expect(store().appearance.gridColor).toBe("");
    fireEvent.change(colorInput("网格颜色"), { target: { value: "#222831" } });
    expect(store().appearance.gridColor).toBe("#222831");
  });

  it("六行 K 线颜色各自只改自己那一格", () => {
    openPanel();
    const rows = ["上涨主体", "下跌主体", "上涨边框", "下跌边框", "上涨影线", "下跌影线"] as const;
    for (const row of rows) expect(colorInput(row)).toBeTruthy();

    fireEvent.change(colorInput("下跌影线"), { target: { value: "#ef5350" } });
    expect(store().appearance.candles).toEqual({
      ...DEFAULT_CHART_APPEARANCE.candles,
      wickDown: "#ef5350",
    });
    // 改的是 wick 而不是 body:短键映射错了会同时体现成「主体也变了」
    expect(appearanceToSeriesOptions(store().appearance).wickDownColor).toBe("#ef5350");
    expect(appearanceToSeriesOptions(store().appearance).downColor).toBe(
      DEFAULT_CHART_APPEARANCE.candles.down,
    );
  });

  it("每次改动都落盘,重载后读到的是改过的值", () => {
    const dialog = openPanel();
    fireEvent.change(colorInput("上涨主体"), { target: { value: "#26a69a" } });
    expect(localStorage.getItem(CHART_APPEARANCE_KEY)).not.toBeNull();
    expect(parseChartAppearanceJson(localStorage.getItem(CHART_APPEARANCE_KEY)).candles.up).toBe(
      "#26a69a",
    );

    // 改背景不该把蜡烛色带跑 —— 三个字段各写各的
    fireEvent.click(Array.from(dialog.querySelectorAll('[role="radio"]'))[1] as HTMLElement);
    expect(parseChartAppearanceJson(localStorage.getItem(CHART_APPEARANCE_KEY)).candles.up).toBe(
      "#26a69a",
    );
  });

  it("「恢复默认」在默认态禁用,改过才可点,点完回到默认", () => {
    const dialog = openPanel();
    const reset = Array.from(dialog.querySelectorAll("button")).find(
      (b) => b.textContent === "恢复默认",
    ) as HTMLButtonElement;
    expect(reset.disabled).toBe(true);

    fireEvent.change(colorInput("上涨主体"), { target: { value: "#26a69a" } });
    expect(reset.disabled).toBe(false);
    fireEvent.click(reset);
    expect(store().appearance).toEqual(DEFAULT_CHART_APPEARANCE);
    expect(reset.disabled).toBe(true);
  });

  it("「恢复默认」给的是新对象,不是默认常量本身(改一处不会污染默认值)", () => {
    const dialog = openPanel();
    fireEvent.change(colorInput("上涨主体"), { target: { value: "#26a69a" } });
    fireEvent.click(
      Array.from(dialog.querySelectorAll("button")).find((b) => b.textContent === "恢复默认")!,
    );
    expect(store().appearance).not.toBe(DEFAULT_CHART_APPEARANCE);
    expect(store().appearance.candles).not.toBe(DEFAULT_CHART_APPEARANCE.candles);
  });

  it("面板不碰绘图对象:改外观前后,网格序列化结果逐字节相同", () => {
    store().createFromDrag({
      start: { time: 1000, price: 100 },
      end: { time: 2000, price: 200 },
      symbol: "BTC",
      timeframe: "1h",
    });
    const before = JSON.stringify(store().grids);
    openPanel();
    fireEvent.change(colorInput("上涨主体"), { target: { value: "#26a69a" } });
    fireEvent.click(Array.from(document.querySelectorAll('[role="radio"]'))[1] as HTMLElement);

    expect(JSON.stringify(store().grids)).toBe(before);
    // 外观确实变了,才说明上面那条不是"什么都没发生"的空断言
    expect(store().appearance.candles.up).toBe("#26a69a");
  });
});

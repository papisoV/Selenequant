import { describe, expect, it } from "vitest";

import {
  TOOL_GROUP_LABELS,
  TOOL_ORDER,
  TOOLS,
  isDrawingTool,
  toolLabelForMode,
  toolsInGroup,
} from "@/lib/chart/tool-registry";

describe("tool registry", () => {
  it("每个 kind 都能在表里找到自己(表键与 kind 字段一致)", () => {
    for (const [key, spec] of Object.entries(TOOLS)) {
      expect(spec.kind).toBe(key);
    }
  });

  it("TOOL_ORDER 不漏不多", () => {
    expect([...TOOL_ORDER].sort()).toEqual(Object.keys(TOOLS).sort());
  });

  it("模式映射:每个工具落到各自的几何,六个模式一一对应", () => {
    expect(TOOLS.fib.mode).toBe("price");
    expect(TOOLS.fibTime.mode).toBe("time");
    expect(TOOLS.trend.mode).toBe("trend");
    expect(TOOLS.horizontal.mode).toBe("horizontal");
    expect(TOOLS.vertical.mode).toBe("vertical");
    expect(TOOLS.rect.mode).toBe("rect");
    // 反查表必须自洽:toolLabelForMode 得不到 "绘图对象" 兜底
    for (const spec of Object.values(TOOLS)) {
      expect(toolLabelForMode(spec.mode)).toBe(spec.label);
    }
  });

  it("分组划分完整且不重叠 —— 每个工具恰好属于一组", () => {
    const fib = toolsInGroup("fib").map((s) => s.kind);
    const basic = toolsInGroup("basic").map((s) => s.kind);
    expect(fib).toEqual(["fib", "fibTime"]);
    expect(basic).toEqual(["trend", "horizontal", "vertical", "rect"]);
    expect([...fib, ...basic].sort()).toEqual([...TOOL_ORDER].sort());
    for (const group of ["fib", "basic"] as const) {
      expect(TOOL_GROUP_LABELS[group].trim().length).toBeGreaterThan(0);
    }
  });

  it("单点工具只认一个点,其余工具要拖出一条腿", () => {
    expect(TOOLS.horizontal.pointCount).toBe(1);
    expect(TOOLS.vertical.pointCount).toBe(1);
    for (const kind of ["fib", "fibTime", "trend", "rect"] as const) {
      expect(TOOLS[kind].pointCount).toBe(2);
    }
  });

  it("每个工具都有非空文案(中文源串,供 i18n 取译文)", () => {
    for (const spec of Object.values(TOOLS)) {
      expect(spec.label.trim().length).toBeGreaterThan(0);
      expect(spec.hint.trim().length).toBeGreaterThan(0);
    }
  });

  it("isDrawingTool 只认表里的值,select / 空串 / 原型链键都不算", () => {
    expect(isDrawingTool("fib")).toBe(true);
    expect(isDrawingTool("fibTime")).toBe(true);
    expect(isDrawingTool("trend")).toBe(true);
    expect(isDrawingTool("rect")).toBe(true);
    expect(isDrawingTool("select")).toBe(false);
    expect(isDrawingTool("")).toBe(false);
    // 用 hasOwnProperty 而不是 `in` —— 否则 "constructor" 会被当成合法工具。
    expect(isDrawingTool("constructor")).toBe(false);
    expect(isDrawingTool("toString")).toBe(false);
  });
});

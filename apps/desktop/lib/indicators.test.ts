import { describe, it, expect } from "vitest";
import {
  INDICATORS,
  indicatorById,
  validIndicator,
  listIndicatorsByCategory,
  buildFormulaString,
  buildDemoBacktestResult,
  INDICATOR_CATEGORY_LABELS,
  formatParamValue,
} from "./indicators";

// 实读 templates/*.yaml = 26 个文件(禁臆造 40 模板)
const YAML_TEMPLATE_COUNT = 26;
// factory.py STRATEGY_TEMPLATES 中与本目录重叠的抽样 id(门禁核对 5 个)
const FACTORY_OVERLAP_IDS = [
  "template-sma-cross",
  "template-ema-cross",
  "template-macd-momentum",
  "template-dca-grid",
  "template-adx-trend",
];

describe("indicators 指标目录(镜像 strategy-engine templates/*.yaml)", () => {
  it("目录完整性:26 条(实读数量)、id 唯一、id 形如 template-*", () => {
    expect(INDICATORS.length).toBe(YAML_TEMPLATE_COUNT);
    const ids = INDICATORS.map((i) => i.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) expect(id).toMatch(/^template-/);
  });

  it("与 factory.py 重叠的 5 个模板 id 都在目录里", () => {
    for (const id of FACTORY_OVERLAP_IDS) {
      expect(indicatorById(id)).toBeTruthy();
    }
  });

  it("params schema 合法:key 非空/type 合法/default 类型匹配/min<max", () => {
    for (const meta of INDICATORS) {
      expect(meta.nameZh).toBeTruthy();
      expect(meta.description).toBeTruthy();
      expect(Object.keys(INDICATOR_CATEGORY_LABELS)).toContain(meta.category);
      for (const p of meta.params) {
        expect(p.key.length).toBeGreaterThan(0);
        expect(p.label.length).toBeGreaterThan(0);
        expect(["number", "string", "boolean"]).toContain(p.type);
        if (p.type === "number") expect(typeof p.default).toBe("number");
        if (p.type === "string") expect(typeof p.default).toBe("string");
        if (p.type === "boolean") expect(typeof p.default).toBe("boolean");
        if (p.min != null && p.max != null) expect(p.min).toBeLessThan(p.max);
      }
    }
  });

  it("分类聚合:按分类过滤并集等于全量", () => {
    let sum = 0;
    for (const cat of Object.keys(INDICATOR_CATEGORY_LABELS) as (keyof typeof INDICATOR_CATEGORY_LABELS)[]) {
      sum += listIndicatorsByCategory(cat).length;
    }
    expect(sum).toBe(INDICATORS.length);
    expect(listIndicatorsByCategory("all").length).toBe(INDICATORS.length);
  });

  it("validIndicator:按 id 或 type 命中,未知返回 false", () => {
    expect(validIndicator("template-ema-cross")).toBe(true);
    expect(validIndicator("ema_cross")).toBe(true);
    expect(validIndicator("template-不存在")).toBe(false);
  });

  it("buildFormulaString:参数默认值渲染可读公式串", () => {
    const ema = indicatorById("template-ema-cross")!;
    expect(buildFormulaString(ema)).toBe("EMA Crossover( 快线 EMA = 12, 慢线 EMA = 26 )");
    // 覆盖参数值
    expect(buildFormulaString(ema, { ema_fast: 5, ema_slow: 20 })).toBe(
      "EMA Crossover( 快线 EMA = 5, 慢线 EMA = 20 )",
    );
  });

  it("buildFormulaString:boolean 参数渲染 是/否", () => {
    const mtf = indicatorById("template-mtf-trend")!;
    expect(buildFormulaString(mtf)).toContain("需趋势一致 = 是");
  });

  it("buildDemoBacktestResult:确定性、权益曲线长度固定", () => {
    const a = buildDemoBacktestResult(0);
    const b = buildDemoBacktestResult(0);
    expect(a.equity_curve).toEqual(b.equity_curve);
    expect(a.equity_curve.length).toBe(60);
    expect(a.total_trades).toBeGreaterThan(0);
    expect(formatParamValue(true)).toBe("是");
    expect(formatParamValue(2.0)).toBe("2");
  });
});

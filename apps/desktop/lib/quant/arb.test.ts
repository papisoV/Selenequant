import { describe, it, expect } from "vitest";
import {
  annualizeFunding,
  fundingSide,
  isArbOpenable,
  fundingRateSpread,
  basisSpread,
  FUNDING_THRESHOLD_APR,
  DEMO_FUNDING_RATES,
} from "./arb";

describe("arb 资金费率套利纯函数", () => {
  it("annualizeFunding — 8h 费率 → 年化 %", () => {
    expect(annualizeFunding(0.0001)).toBeCloseTo(10.95, 2);
    expect(annualizeFunding(0)).toBe(0);
  });

  it("fundingSide — 正费率做空吃费,负费率做多吃费", () => {
    expect(fundingSide(0.0001)).toBe("short");
    expect(fundingSide(-0.0001)).toBe("long");
    expect(fundingSide(0)).toBe("short");
  });

  it("isArbOpenable — 达阈值才开仓", () => {
    expect(isArbOpenable(11)).toBe(true);
    expect(isArbOpenable(-11)).toBe(true);
    expect(isArbOpenable(5)).toBe(false);
    expect(isArbOpenable(FUNDING_THRESHOLD_APR)).toBe(true);
  });

  it("fundingRateSpread — 年化降序 + openable 标记 + label", () => {
    const out = fundingRateSpread([
      { symbol: "ETH", rate: 0.0001 },
      { symbol: "BTC", rate: 0.0002 },
      { symbol: "SOL", rate: 0.00005 },
    ]);
    expect(out.map((o) => o.symbol)).toEqual(["BTC", "ETH", "SOL"]);
    expect(out[0].openable).toBe(true); // 21.9%
    expect(out[1].openable).toBe(true); // 10.95%
    expect(out[2].openable).toBe(false); // 5.475%
    expect(out[0].label).toContain("做空");
    expect(out[0].annualizedPct).toBeCloseTo(21.9, 1);
  });

  it("basisSpread — 期现基差 %", () => {
    expect(basisSpread(100, 105)).toBe(5);
    expect(basisSpread(100, 95)).toBe(-5);
    expect(basisSpread(0, 105)).toBe(0);
  });

  it("DEMO_FUNDING_RATES — 有可开仓也有不可开仓(演示差异)", () => {
    const out = fundingRateSpread(DEMO_FUNDING_RATES);
    expect(out.some((o) => o.openable)).toBe(true);
    expect(out.some((o) => !o.openable)).toBe(true);
  });
});

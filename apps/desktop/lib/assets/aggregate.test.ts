import { describe, it, expect } from "vitest";
import { aggregate, totalUsd, fmtMoney, fmtSignedMoney, fmtPct, fmtNum, DASH } from "./aggregate";
import type { AssetBalance } from "./types";

const base: AssetBalance = { exchange: "binance", asset: "USDT", free: 100, locked: 10 };

describe("aggregate(资产聚合)", () => {
  it("同 (exchange,asset) 合并,free/locked 相加", () => {
    const out = aggregate([
      { ...base },
      { ...base, free: 50, locked: 5 },
    ]);
    expect(out).toHaveLength(1);
    expect(out[0]).toEqual({ exchange: "binance", asset: "USDT", free: 150, locked: 15 });
  });

  it("不同 (exchange,asset) 各自保留", () => {
    const out = aggregate([
      { exchange: "binance", asset: "USDT", free: 100, locked: 0 },
      { exchange: "okx", asset: "USDT", free: 200, locked: 0 },
      { exchange: "binance", asset: "BTC", free: 0.5, locked: 0 },
    ]);
    expect(out).toHaveLength(3);
  });

  it("不突变入参", () => {
    const rows: AssetBalance[] = [{ ...base }, { ...base, free: 50 }];
    const snapshot = JSON.stringify(rows);
    aggregate(rows);
    expect(JSON.stringify(rows)).toBe(snapshot);
  });
});

describe("totalUsd(总估值)", () => {
  const priceMap = { USDT: 1, BTC: 60000 };

  it("Σ (free+locked) × price", () => {
    const rows: AssetBalance[] = [
      { exchange: "binance", asset: "USDT", free: 100, locked: 10 },
      { exchange: "binance", asset: "BTC", free: 1, locked: 0.5 },
    ];
    expect(totalUsd(rows, priceMap)).toBeCloseTo(110 + 1.5 * 60000, 4);
  });

  it("缺价按 0", () => {
    const rows: AssetBalance[] = [{ exchange: "okx", asset: "XRP", free: 100, locked: 0 }];
    expect(totalUsd(rows, priceMap)).toBe(0);
  });
});

describe("格式化", () => {
  it("fmtMoney", () => {
    expect(fmtMoney(999.5)).toBe("$999.5");
  });

  it("fmtSignedMoney", () => {
    expect(fmtSignedMoney(100)).toBe("+$100");
    expect(fmtSignedMoney(-50.5)).toBe("-$50.5");
  });

  it("fmtPct", () => {
    expect(fmtPct(1.5)).toBe("+1.50%");
    expect(fmtPct(-2)).toBe("-2.00%");
  });

  it("fmtNum", () => {
    expect(fmtNum(0.42)).toBe("0.42");
    expect(fmtNum(999)).toBe("999");
    expect(fmtNum(12500)).toMatch(/12,?500/);
    expect(fmtNum(Number.NaN)).toBe(DASH);
  });
});

import { describe, it, expect } from "vitest";
import { baseOf, matchesSymbol, filterBySymbol } from "./symbols";

// 行为锁: 交易对匹配契约。cleanroom 重写必须保持; 撤实现 → 必红。

describe("baseOf", () => {
  it("取斜杠前基币", () => expect(baseOf("BTC/USDT")).toBe("BTC"));
  it("无斜杠原样", () => expect(baseOf("BTCUSDT")).toBe("BTCUSDT"));
  it("空串", () => expect(baseOf("")).toBe(""));
});

describe("matchesSymbol", () => {
  it("空 filter → 全匹配", () => expect(matchesSymbol("ANY", "")).toBe(true));
  it("精确相等", () => expect(matchesSymbol("BTC/USDT", "BTC/USDT")).toBe(true));
  it("含基币即匹配", () => expect(matchesSymbol("BTCUSDT-PERP", "BTC/USDT")).toBe(true));
  it("-USDT 写法", () => expect(matchesSymbol("BTC-USDT", "BTC/USDT")).toBe(true));
  it("-USDT-SWAP 写法", () => expect(matchesSymbol("BTC-USDT-SWAP", "BTC/USDT")).toBe(true));
  it("裸基币写法", () => expect(matchesSymbol("BTCUSDT", "BTC/USDT")).toBe(true));
  it("不同币不匹配", () => expect(matchesSymbol("ETH/USDT", "BTC/USDT")).toBe(false));
});

describe("filterBySymbol", () => {
  const rows = [{ symbol: "BTC/USDT" }, { symbol: "ETH-USDT-SWAP" }, { symbol: "SOLUSDT" }];
  it("无 filter → 原样", () => expect(filterBySymbol(rows, undefined)).toBe(rows));
  it("按 filter 过滤", () => {
    expect(filterBySymbol(rows, "BTC/USDT").map((r) => r.symbol)).toEqual(["BTC/USDT"]);
    expect(filterBySymbol(rows, "ETH/USDT").map((r) => r.symbol)).toEqual(["ETH-USDT-SWAP"]);
  });
  it("无命中 → 空", () => expect(filterBySymbol(rows, "DOGE/USDT")).toEqual([]));
});

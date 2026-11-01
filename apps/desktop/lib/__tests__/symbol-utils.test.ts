import { describe, it, expect } from "vitest";
import { toSymbolPair } from "../symbol-utils";

describe("P9 toSymbolPair symbol 归一", () => {
  it("#1 bare \"BTC\" → \"BTC/USDT\"", () => {
    expect(toSymbolPair("BTC")).toBe("BTC/USDT");
  });

  it("#2 已是 pair \"BTC/USDT\" → 原样返回", () => {
    expect(toSymbolPair("BTC/USDT")).toBe("BTC/USDT");
    expect(toSymbolPair("ETH/USDT")).toBe("ETH/USDT");
  });

  it("#3 空字符串 → fallback \"BTC/USDT\"", () => {
    expect(toSymbolPair("")).toBe("BTC/USDT");
  });
});

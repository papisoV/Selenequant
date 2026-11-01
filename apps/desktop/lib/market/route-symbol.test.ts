import { describe, expect, it } from "vitest";
import { routeSymbol, normalizeSymbol } from "./route-symbol";

describe("routeSymbol — 符号自动路由（顺序敏感）", () => {
  it("① 6 位数字 → ashare", () => {
    expect(routeSymbol("000001")).toBe("ashare");
    expect(routeSymbol("600519")).toBe("ashare");
  });

  it("② 4 位数字 → taiwan", () => {
    expect(routeSymbol("2330")).toBe("taiwan");
    expect(routeSymbol("0050")).toBe("taiwan");
  });

  it("③ 字母+0 结尾且命中商品白名单 → commodities", () => {
    expect(routeSymbol("CU0")).toBe("commodities");
    expect(routeSymbol("IF0")).toBe("commodities");
    expect(routeSymbol("AU0")).toBe("commodities");
  });

  it("③b 字母+0 结尾但未命中白名单 → 兜底 us（非商品）", () => {
    expect(routeSymbol("PL0")).toBe("us");
  });

  it("④ _AMBIGUOUS → ambiguous（提示手动选市场）", () => {
    expect(routeSymbol("ATOM")).toBe("ambiguous");
    expect(routeSymbol("LTC")).toBe("ambiguous");
  });

  it("⑤ _CRYPTO_NAMES → crypto", () => {
    expect(routeSymbol("BTC")).toBe("crypto");
    expect(routeSymbol("eth")).toBe("crypto");
  });

  it("⑥ 兜底 → us", () => {
    expect(routeSymbol("AAPL")).toBe("us");
    expect(routeSymbol("MSFT")).toBe("us");
  });

  it("forceMarket 强制覆盖，跳过自动路由", () => {
    expect(routeSymbol("ATOM", { forceMarket: "us" })).toBe("us");
    expect(routeSymbol("BTC", { forceMarket: "commodities" })).toBe("commodities");
  });

  it("斜杠交易对格式剥后缀再路由（BTC/USDT → crypto 而非误判 us）", () => {
    expect(routeSymbol("BTC/USDT")).toBe("crypto");
    expect(routeSymbol("ETH/USDT")).toBe("crypto");
    expect(routeSymbol("PEPE/USDT")).toBe("crypto");
  });

  it("合约后缀 -PERP/-SWAP 剥离后路由（600519-PERP → ashare 而非误判 us）", () => {
    expect(routeSymbol("600519-PERP")).toBe("ashare");
    expect(routeSymbol("000001-SWAP")).toBe("ashare");
    expect(routeSymbol("2330-PERP")).toBe("taiwan");
    expect(routeSymbol("BTC-PERP")).toBe("crypto");
  });

  // 1013 事故："a股还是不能显示" —— 自选/最近里存着带交易所后缀的 "300750.SZ"，
  // 旧实现只剥 -PERP 不剥 .SZ，导致 6 位数字判定失败 → 兜底 us → /chart/bars?market=us
  // → 502 → 图上"无数据"。修法：归一化剥 .SZ/.SH/.SS/.BJ/.TW/.TWO。
  it("交易所后缀 .SZ/.SH 剥离后路由（300750.SZ → ashare 而非误判 us）", () => {
    expect(routeSymbol("300750.SZ")).toBe("ashare");
    expect(routeSymbol("600519.SH")).toBe("ashare");
    expect(routeSymbol("000001.SZ")).toBe("ashare");
    expect(routeSymbol("2330.TW")).toBe("taiwan");
    expect(routeSymbol("2330.TWO")).toBe("taiwan");
  });

  it("normalizeSymbol：剥斜杠/合约/交易所后缀，保留裸符号", () => {
    expect(normalizeSymbol("300750.SZ")).toBe("300750");
    expect(normalizeSymbol("600519.SH")).toBe("600519");
    expect(normalizeSymbol("BTC/USDT")).toBe("BTC");
    expect(normalizeSymbol("BTC-PERP")).toBe("BTC");
    expect(normalizeSymbol("  aapl  ")).toBe("AAPL");
    expect(normalizeSymbol("AAPL")).toBe("AAPL");
    expect(normalizeSymbol("CU0")).toBe("CU0");
  });
});

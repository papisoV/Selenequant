// 符号 → 市场自动路由（前端专用；/chart/bars 后端 market 参数是显式传入的）
// 顺序敏感：① 6 位数字→A股 ② 4 位数字→台股 ③ 字母+0 结尾且去 0 后命中商品白名单→大宗
// ④ _AMBIGUOUS→ambiguous（提示手动选市场） ⑤ _CRYPTO_NAMES→crypto（走 WS 实时分支）
// ⑥ 兜底→us

export type SymbolMarket = "crypto" | "us" | "commodities" | "ashare" | "taiwan" | "ambiguous";

export const _FUTURES_WL = new Set([
  "AU", "AG", "CU", "SC", "NI", "ZN", "AL", "RB", "TA", "PB", "SN", "SS", "FU",
  "OI", "FG", "SA", "UR", "RU", "NR", "BU", "HC", "SP", "JD", "IF", "IH", "IC", "IM",
]);

export const _CRYPTO_NAMES = new Set([
  "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "MATIC", "DOT",
  "LTC", "UNI", "FIL", "APT", "ARB", "OP", "NEAR", "SUI", "TRX", "TON", "SHIB", "PEPE",
]);

export const _AMBIGUOUS = new Set([
  "ATOM", "ADA", "LTC", "DOT", "XLM", "FIL", "ARB", "OP", "NEAR", "SUI", "TRX",
]);

export interface RouteSymbolOpts {
  forceMarket?: Exclude<SymbolMarket, "ambiguous">;
}

// 符号归一化：剥掉交易所/合约后缀，得到后端 /chart/bars 认识的裸符号。
// 后端对带后缀形式一律不认（ashare+300750.SZ→404、us+300750.SZ→502），
// 而后端 /chart/symbols 返回的已是裸符号（如 "300750"）；带后缀的形态来自
// 历史自选/最近里存的脏数据（"300750.SZ"），若不归一化会被路由到 us 且取不到数。
// 处理：斜杠对取斜杠前（BTC/USDT→BTC）、A股交易所后缀（.SZ/.SH/.SS/.BJ）、
// 台股（.TW/.TWO）、合约后缀（-PERP/-SWAP/-FUTURES/-SPOT）。
export function normalizeSymbol(symbol: string): string {
  return symbol
    .trim()
    .toUpperCase()
    .split("/")[0]
    .replace(/-(PERP|SWAP|FUTURES|SPOT)$/, "")
    .replace(/\.(SZ|SH|SS|BJ|TW|TWO)$/, "");
}

export function routeSymbol(symbol: string, opts?: RouteSymbolOpts): SymbolMarket {
  if (opts?.forceMarket) return opts.forceMarket;
  // 归一化先剥后缀再路由，避免兜底误判到 us（600519.SH / 600519-PERP 都会掉进 us）
  const s = normalizeSymbol(symbol);
  if (/^\d{6}$/.test(s)) return "ashare";
  if (/^\d{4}$/.test(s)) return "taiwan";
  if (s.endsWith("0") && _FUTURES_WL.has(s.slice(0, -1))) return "commodities";
  if (_AMBIGUOUS.has(s)) return "ambiguous";
  if (_CRYPTO_NAMES.has(s)) return "crypto";
  return "us";
}

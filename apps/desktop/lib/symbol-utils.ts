/**
 * Symbol 格式归一 helper
 *
 * 背景: pageFilter.symbol 是 bare("BTC"),alpha/factors 等消费点用 pair("BTC/USDT")
 * 不修 market.ts:31 默认值(22 消费者风险太大),在消费点显式归一
 */

/** bare symbol ("BTC") → pair ("BTC/USDT");已是 pair 原样返回;空 fallback "BTC/USDT" */
export function toSymbolPair(base: string): string {
  if (!base) return "BTC/USDT";
  if (base.includes("/")) return base;
  return `${base}/USDT`;
}

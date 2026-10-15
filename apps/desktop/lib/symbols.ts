/**
 * 交易对筛选:把 UI 里的 `BTC/USDT` 形式筛选值与各种交易所 symbol 写法对齐。
 *
 * 上游各面板(清算、大户、鲸鱼转账)各写一份同样的 5 变体匹配, 这里收敛成一处。
 */

/** 从 `BTC/USDT` 取基币 `BTC`; 无斜杠时原样返回。 */
export function baseOf(symbol: string): string {
  return symbol.split("/")[0]
}

/**
 * symbol 是否匹配筛选值 `filter`(形如 `BTC/USDT` 或 `BTC`)。
 *
 * 命中任一即算匹配: 含基币 / 等于筛选值 / `BTC-USDT` / `BTC-USDT-SWAP` / `BTCUSDT`。
 */
export function matchesSymbol(symbol: string, filter: string): boolean {
  if (!filter) return true
  const base = baseOf(filter)
  return (
    symbol.includes(base) ||
    symbol === filter ||
    symbol === `${base}-USDT` ||
    symbol === `${base}-USDT-SWAP` ||
    symbol === `${base}USDT`
  )
}

/** 按筛选值过滤一组带 `symbol` 字段的对象(空筛选返回原样)。 */
export function filterBySymbol<T extends { symbol: string }>(
  items: T[],
  filter: string | undefined,
): T[] {
  if (!filter) return items
  return items.filter((item) => matchesSymbol(item.symbol, filter))
}

/**
 * Centralized number formatting utilities for the trading UI.
 * All price/qty/PnL formatting should use these functions — no ad-hoc toFixed() in components.
 */

/** Tiered price formatting by magnitude (matches orderbook-panel convention). */
export function formatPrice(price: number): string {
  if (!Number.isFinite(price)) return "—";
  if (price >= 10000) return price.toFixed(1);
  if (price >= 100) return price.toFixed(2);
  if (price >= 1) return price.toFixed(3);
  if (price >= 0.01) return price.toFixed(4);
  return price.toFixed(6);
}

/** Tiered quantity formatting by magnitude. */
export function formatQty(qty: number): string {
  if (!Number.isFinite(qty)) return "—";
  if (qty >= 1000) return qty.toFixed(2);
  if (qty >= 1) return qty.toFixed(4);
  return qty.toFixed(5);
}

/** USDT value formatting with K/M suffixes. */
export function formatUsdt(val: number): string {
  if (!Number.isFinite(val)) return "—";
  if (val >= 1_000_000) return (val / 1_000_000).toFixed(2) + "M";
  if (val >= 10_000) return (val / 1_000).toFixed(1) + "K";
  if (val >= 1_000) return (val / 1_000).toFixed(2) + "K";
  if (val >= 1) return val.toFixed(2);
  return val.toFixed(4);
}

/** PnL formatting with +/- prefix, 2 decimal places. */
export function formatPnl(value: number): string {
  const prefix = value >= 0 ? "+" : "";
  return `${prefix}${value.toFixed(2)}`;
}

/** PnL percentage formatting with +/- prefix and % suffix. */
export function formatPnlPct(value: number): string {
  const prefix = value >= 0 ? "+" : "";
  return `${prefix}${value.toFixed(2)}%`;
}

/** Returns the design-token CSS class for PnL coloring. */
export function pnlClass(value: number): "text-pos" | "text-neg" {
  return value >= 0 ? "text-pos" : "text-neg";
}

// -- 日期/时间 ---------------------------------------------------------------

/** 时间戳(ms) → `MM/DD HH:MM`。 */
export function formatTime(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * 配置里的日期串(`YYYYMMDD`)→ `<input type=date>` 需要的 `YYYY-MM-DD`。
 * 非 8 位则原样返回。
 */
export function formatDateForInput(dateStr: string): string {
  if (dateStr.length === 8) {
    return `${dateStr.slice(0, 4)}-${dateStr.slice(4, 6)}-${dateStr.slice(6, 8)}`;
  }
  return dateStr;
}

/**
 * `<input type=date>` 的 `YYYY-MM-DD` → 配置存储的 `YYYYMMDD`。
 * 不含连字符则原样返回。
 */
export function formatDateForConfig(dateStr: string): string {
  if (dateStr.includes("-")) {
    return dateStr.replace(/-/g, "");
  }
  return dateStr;
}

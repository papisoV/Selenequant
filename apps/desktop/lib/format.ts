/**
 * Centralized number formatting utilities for the trading UI.
 * All price/qty/PnL formatting should use these functions — no ad-hoc toFixed() in components.
 */

/**
 * 分档价格精度(小数位数)。
 *
 * 与 formatPrice 共用同一套分档 —— 画布上的档位标签要 `toFixed(n)` 而不是
 * 拿一个字符串,所以需要把"几位小数"单独暴露出来。两处分档若各写一套,
 * 图上标的价和面板里的价会差最后一位。
 */
export function pricePrecision(price: number): number {
  const a = Math.abs(price);
  if (!Number.isFinite(a)) return 2;
  if (a >= 10000) return 1;
  if (a >= 100) return 2;
  if (a >= 1) return 3;
  if (a >= 0.01) return 4;
  return 6;
}

/** Tiered price formatting by magnitude (matches orderbook-panel convention). */
export function formatPrice(price: number): string {
  if (!Number.isFinite(price)) return "—";
  return price.toFixed(pricePrecision(price));
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

// -- 比例 ↔ 百分比 -----------------------------------------------------------
//
// 存储层用小数比例(0.035),界面用百分比(3.5%)。这两个函数是唯一的换算口,
// 组件里不许再出现 `* 100` 的临时写法。

/** 小数比例 → 百分比数字(0.035 → 3.5),四舍五入到 `decimals` 位。 */
export function fractionToPercent(fraction: number, decimals = 2): number {
  const factor = 10 ** decimals;
  return Math.round(fraction * 100 * factor) / factor;
}

/**
 * 小数比例 → 展示串(0.035 → "3.5%")。
 *
 * 最多两位小数并去掉尾零 —— 步长最小的是 T1 阈值 0.0025,即 0.25%,
 * 少一位小数就会把滑块能停的档位四舍五入掉。
 */
export function formatFractionAsPercent(
  fraction: number,
  { decimals = 2, withSign = false }: { decimals?: number; withSign?: boolean } = {},
): string {
  if (!Number.isFinite(fraction)) return "—";
  const pct = fractionToPercent(fraction, decimals);
  const sign = withSign && pct > 0 ? "+" : "";
  return `${sign}${String(pct)}%`;
}

/** 百分比数字 → 小数比例(3.5 → 0.035),写回存储用。 */
export function percentToFraction(percent: number, decimals = 4): number {
  const factor = 10 ** decimals;
  return Math.round(percent * factor) / 100 / factor;
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
 * ISO 时间串 → `MM-DD`(取串上的月日,不做时区换算)。
 *
 * 研究面各列表原本各写一份逐字相同的 `iso.slice(5, 10)`,渲染成三处
 * "看似一致、改一处漏两处"的日期。这里收成唯一实现。
 */
export function formatMonthDay(iso: string): string {
  return iso.slice(5, 10);
}

/** ISO 时间串 → `MM-DD HH:MM`(本地时区;解析失败原样返回)。 */
export function formatDateTimeShort(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
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

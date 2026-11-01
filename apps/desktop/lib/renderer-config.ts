// lib/renderer-config.ts — 通用渲染器配置层(纯函数,可直接 vitest)
// 按内容域(slug 前缀 + rest 路径)配数组键名/格式化规则,避免通用渲染器对后端 data= 形状靠猜。

import type { PageMeta } from "@/lib/page-manifest";
import { identityTranslate, type Translate } from "@/lib/i18n/types";

// ---- 内容域推断 ----
// 数组键名分散在 dict 里(capabilities/channels/symbols/events/tokens/data/skills…),
// 无单一主键。按 slug/rest 前缀归域,域内配"哪个键是数组主表"。
export type ContentDomain =
  | "us-radar"
  | "polymarket"
  | "derivatives"
  | "paper"
  | "intelligence"
  | "alpha"
  | "dex"
  | "serenity"
  | "strategies"
  | "trading"
  | "markets"
  | "onchain"
  | "research"
  | "chat"
  | "marketplace"
  | "hyperliquid"
  | "kf-index"
  | "mcp"
  | "skills"
  | "notifications"
  | "grid"
  | "tuning"
  | "backtests"
  | "optimizations"
  | "general";

/** slug 前缀 → 内容域。按最长前缀优先匹配(derivatives 先于 markets 等)。 */
const SLUG_DOMAIN: Array<[RegExp, ContentDomain]> = [
  [/^us-radar/, "us-radar"],
  [/^polymarket/, "polymarket"],
  [/^derivatives/, "derivatives"],
  [/^paper/, "paper"],
  [/^intelligence/, "intelligence"],
  [/^alpha/, "alpha"],
  [/^dex/, "dex"],
  [/^serenity/, "serenity"],
  [/^strategies/, "strategies"],
  [/^trading/, "trading"],
  [/^markets/, "markets"],
  [/^onchain/, "onchain"],
  [/^research/, "research"],
  [/^chat/, "chat"],
  [/^marketplace/, "marketplace"],
  [/^hyperliquid/, "hyperliquid"],
  [/^kf-index/, "kf-index"],
  [/^mcp/, "mcp"],
  [/^skills/, "skills"],
  [/^notifications/, "notifications"],
  [/^grid/, "grid"],
  [/^tuning/, "tuning"],
  [/^backtests/, "backtests"],
  [/^optimizations/, "optimizations"],
];

/** rest 路径段 → 内容域兜底(部分页 slug 不含域前缀,如 "geographic"/"sentiment")。 */
const REST_DOMAIN: Array<[RegExp, ContentDomain]> = [
  [/us-radar/, "us-radar"],
  [/polymarket/, "polymarket"],
  [/derivatives/, "derivatives"],
  [/paper/, "paper"],
  [/intelligence/, "intelligence"],
  [/alpha/, "alpha"],
  [/serenity/, "serenity"],
  [/strategies/, "strategies"],
  [/trading/, "trading"],
  [/markets/, "markets"],
  [/onchain/, "onchain"],
  [/research/, "research"],
  [/chat/, "chat"],
  [/marketplace/, "marketplace"],
  [/hyperliquid/, "hyperliquid"],
  [/kf-index/, "kf-index"],
  [/mcp/, "mcp"],
  [/skills/, "skills"],
  [/notifications/, "notifications"],
  [/grid/, "grid"],
  [/tuning/, "tuning"],
  [/backtests/, "backtests"],
  [/optimizations/, "optimizations"],
];

/** 数组主键按内容域配置;general 兜底常见键名。键值存在即认为该 dict 是"带数组的容器"。 */
const DOMAIN_ARRAY_KEYS: Record<ContentDomain, string[]> = {
  "us-radar": ["capabilities", "symbols", "events", "scores", "providers", "data"],
  polymarket: ["events", "markets", "tokens", "assets", "outcomes", "results", "addresses", "entries", "rules", "data"],
  derivatives: ["events", "data", "trades", "liquidations", "funding_rates", "open_interest"],
  paper: ["positions", "orders", "fills", "accounts", "pnl", "equity_history", "data"],
  intelligence: ["channels", "events", "signals", "pulse", "summary", "data"],
  alpha: ["formulas", "strategies", "results", "data", "runs"],
  dex: ["tokens", "pairs", "chains", "transactions", "positions", "data"],
  serenity: ["scores", "factors", "alerts", "history", "data"],
  strategies: ["strategies", "templates", "deployments", "versions", "data"],
  trading: ["positions", "orders", "balances", "history", "data"],
  markets: ["klines", "tickers", "orderbook", "data", "symbols"],
  onchain: ["metrics", "addresses", "whales", "transfers", "data"],
  research: ["results", "tasks", "factors", "formulas", "saved_strategies", "data"],
  chat: ["sessions", "messages", "conversations", "data"],
  marketplace: ["listings", "purchases", "items", "data"],
  hyperliquid: ["addresses", "positions", "fills", "data"],
  "kf-index": ["indexes", "symbols", "components", "data"],
  mcp: ["servers", "tools", "resources", "data"],
  skills: ["skills", "categories", "data"],
  notifications: ["channels", "event_types", "preferences", "notifications", "data"],
  grid: ["grids", "markets", "state", "orders", "data"],
  tuning: ["tasks", "results", "data"],
  backtests: ["backtests", "results", "metrics", "data"],
  optimizations: ["optimizations", "results", "tasks", "data"],
  general: ["items", "data", "results", "rows", "list"],
};

export function inferDomain(meta: PageMeta): ContentDomain {
  for (const [re, d] of SLUG_DOMAIN) if (re.test(meta.slug)) return d;
  const rest = meta.rest ?? "";
  for (const [re, d] of REST_DOMAIN) if (re.test(rest)) return d;
  return "general";
}

export function arrayKeysForDomain(domain: ContentDomain): string[] {
  return DOMAIN_ARRAY_KEYS[domain];
}

// ---- 解包 ----
export type UnwrapResult =
  | { kind: "table"; rows: Record<string, unknown>[] }
  | { kind: "kv"; entries: Array<[string, unknown]> };

const isPlainObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** 从 dict 里找"数组主表"键(空数组也算——表示容器空而非纯 dict)。 */
function findArrayKey(obj: Record<string, unknown>, keys: string[]): string | null {
  for (const k of keys) {
    const v = obj[k];
    if (Array.isArray(v)) return k;
  }
  return null;
}

/** 把后端 data= 解包成"表格行"或"键值对"。表格:顶层数组或 dict 内的数组主表。键值:纯 dict。 */
export function unwrapRows(data: unknown, domain: ContentDomain): UnwrapResult {
  if (Array.isArray(data)) {
    const rows = data.filter((d): d is Record<string, unknown> => isPlainObject(d));
    return rows.length > 0 || data.length === 0
      ? { kind: "table", rows }
      : { kind: "kv", entries: data.map((v, i) => [String(i), v]) };
  }
  if (isPlainObject(data)) {
    const key = findArrayKey(data, arrayKeysForDomain(domain));
    if (key) {
      const arr = data[key] as unknown[];
      const rows = arr.filter((d): d is Record<string, unknown> => isPlainObject(d));
      return { kind: "table", rows };
    }
    // 纯 dict → 键值视图(过滤掉嵌套对象/数组字段,留给 SymbolPanel 展示)
    return { kind: "kv", entries: Object.entries(data) };
  }
  return { kind: "kv", entries: [] };
}

// ---- 动态页判定 ----
/** 非 symbol 占位符(需从列表进入的详情页)。空/纯空白占位符不计。 */
const DYNAMIC_PLACEHOLDER = /{[^}\s]+}/;
const SYMBOL_PLACEHOLDER = /\{(?:symbol|sym)\}/;

export function isDynamicPage(meta: PageMeta): boolean {
  const rest = meta.rest ?? "";
  // 有占位符,且不是纯 symbol 占位符 → 需从列表进入
  if (!DYNAMIC_PLACEHOLDER.test(rest)) return false;
  return !SYMBOL_PLACEHOLDER.test(rest);
}

// ---- 格式化 ----
/** 大数字缩写:>=1e6 → M,>=1e3 → K;金额字段带 $。 */
export function abbrevNumber(value: number): string {
  if (!Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  if (abs >= 1) return value.toFixed(2);
  return value.toFixed(4);
}

/** 单元格式化:大数字缩写 + 对象截断 + null/undefined 显示 '—'。 */
export function formatCellValue(value: unknown, tr: Translate = identityTranslate): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return abbrevNumber(value);
  if (typeof value === "object") return JSON.stringify(value).slice(0, 40);
  return tr(String(value));
}

/** 键名 humanize:snake_case / camelCase → 空格标题,方便表头/卡片标签。 */
export function humanizeKey(key: string): string {
  return key
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

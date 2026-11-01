/**
 * 数据分类层 — 在 scene 之上提供一层"数据类别"语义
 * 用于 loading 骨架色条 / sidebar 分组标签 / 跨场景导航的视觉一致性
 *
 * market   = watch 场景（盯盘 / 行情数据）
 * trade    = trade 场景（执行 / 交易）
 * research = research 场景（研策 / 研究）
 * system   = system 场景（系统 / 管理）
 *
 * 与 lib/page-manifest.ts 的 Scene 一一对应，不重写页面定义，
 * 仅作为视觉/语义分类的统一来源。
 */

export type PageCategory = "market" | "trade" | "research" | "system";

export const PAGE_CATEGORIES: Record<string, PageCategory> = {
  // market (= watch scene)
  "watch/kf": "market",
  "watch/kfi": "market",
  "watch/mk": "market",
  "watch/gex": "market",
  "watch/tac": "market",
  "watch/sig": "market",
  "watch/vchart": "market",
  "watch/poly": "market",
  "watch/dex": "market",
  "watch/onchain": "market",
  "watch/us-radar": "market",
  "watch/hyperliquid": "market",
  "watch/cvd": "market",
  "watch/heatmap": "market",
  "watch/liquidations": "market",
  // trade (= trade scene)
  "trade/pos": "trade",
  "trade/paper": "trade",
  "trade/trd": "trade",
  "trade/grid": "trade",
  "trade/tof": "trade",
  "trade/live_log": "trade",
  "trade/backtests": "trade",
  "trade/dex-trading": "trade",
  "trade/optimizations": "trade",
  "trade/risk": "trade",
  // research (= research scene)
  "research/rsch": "research",
  "research/pa": "research",
  "research/strat": "research",
  "research/alpha_mine": "research",
  "research/serenity": "research",
  "research/qevolve": "research",
  "research/factors": "research",
  "research/optimizer": "research",
  "research/scanner": "research",
  "research/tuning": "research",
  // system (= system scene)
  "system/dash": "system",
  "system/skill": "system",
  "system/mcp": "system",
  "system/set": "system",
  "system/notifications": "system",
  "system/memory": "system",
  "system/marketplace": "system",
  "system/agent": "system",
  "system/chat-sessions": "system",
};

export const CATEGORY_LABELS: Record<PageCategory, string> = {
  market: "行情",
  trade: "交易",
  research: "研策",
  system: "系统",
};

/**
 * 颜色映射 — 仅用 tailwind class（不写 oklch，避免图表库崩溃）。
 * border-* 用于色条，text-* 用于分类标签。
 */
export const CATEGORY_COLORS: Record<PageCategory, { border: string; text: string; bg: string }> = {
  market: { border: "border-blue-500", text: "text-blue-400", bg: "bg-blue-500/10" },
  trade: { border: "border-amber-500", text: "text-amber-400", bg: "bg-amber-500/10" },
  research: { border: "border-purple-500", text: "text-purple-400", bg: "bg-purple-500/10" },
  system: { border: "border-slate-500", text: "text-slate-400", bg: "bg-slate-500/10" },
};

/**
 * 从 pathname 提取 PageCategory。
 * 支持以下格式：
 *   /watch/kf          → market
 *   /trade/trd         → trade
 *   /research/rsch     → research
 *   /system/dash       → system
 *   /trade/trd?foo=bar → trade（剥离 query）
 *   /                  → null（根路径无分类）
 *
 * 未匹配返回 null（调用方自行 fallback）。
 */
export function getCategoryFromPath(pathname: string): PageCategory | null {
  if (!pathname || typeof pathname !== "string") return null;
  // 剥离 query / hash
  const clean = pathname.split(/[?#]/)[0];
  // 去掉开头 /，得到 "watch/kf" 形式
  const key = clean.replace(/^\/+/, "").toLowerCase();
  if (!key) return null;
  // 精确匹配
  if (PAGE_CATEGORIES[key]) return PAGE_CATEGORIES[key];
  // 容错：取前两段（忽略子路径）
  const parts = key.split("/");
  if (parts.length >= 2) {
    const k = `${parts[0]}/${parts[1]}`;
    if (PAGE_CATEGORIES[k]) return PAGE_CATEGORIES[k];
  }
  // 单段 fallback：scene → category
  if (parts.length === 1) {
    if (parts[0] === "watch") return "market";
    if (parts[0] === "trade") return "trade";
    if (parts[0] === "research") return "research";
    if (parts[0] === "system") return "system";
  }
  return null;
}

/**
 * 从 PageMeta 的 scene 字段推导 category（无需 pathname）。
 * 用于 sidebar 这种已知 scene 的场景。
 */
export function categoryFromScene(scene: "watch" | "research" | "trade" | "system" | "workspace"): PageCategory {
  switch (scene) {
    case "watch": return "market";
    case "trade": return "trade";
    case "research": return "research";
    case "system": return "system";
    case "workspace": return "system";
  }
}

import { describe, expect, it } from "vitest";
import {
  abbrevNumber,
  arrayKeysForDomain,
  formatCellValue,
  humanizeKey,
  inferDomain,
  isDynamicPage,
  unwrapRows,
  type ContentDomain,
} from "./renderer-config";
import { ALL_PAGES, type PageMeta } from "./page-manifest";

function meta(partial: Partial<PageMeta> & { slug: string }): PageMeta {
  return {
    title: partial.slug,
    scene: "watch",
    category: "数据",
    renderMode: "symbol",
    removable: true,
    origin: "backend-insert",
    ...partial,
  } as PageMeta;
}

describe("inferDomain(内容域推断)", () => {
  it("按 slug 前缀归域(最长前缀优先)", () => {
    expect(inferDomain(meta({ slug: "us-radar-snapshot" }))).toBe("us-radar");
    expect(inferDomain(meta({ slug: "polymarket-events" }))).toBe("polymarket");
    expect(inferDomain(meta({ slug: "derivatives-foo" }))).toBe("derivatives");
    expect(inferDomain(meta({ slug: "paper-positions" }))).toBe("paper");
    expect(inferDomain(meta({ slug: "intelligence-news-summary" }))).toBe("intelligence");
    expect(inferDomain(meta({ slug: "alpha-formulas" }))).toBe("alpha");
    expect(inferDomain(meta({ slug: "dex-pairs" }))).toBe("dex");
    expect(inferDomain(meta({ slug: "serenity-scores" }))).toBe("serenity");
    expect(inferDomain(meta({ slug: "strategies" }))).toBe("strategies");
    expect(inferDomain(meta({ slug: "markets" }))).toBe("markets");
    expect(inferDomain(meta({ slug: "backtests" }))).toBe("backtests");
    expect(inferDomain(meta({ slug: "optimizations" }))).toBe("optimizations");
  });

  it("slug 无前缀时按 rest 路径兜底归域", () => {
    expect(inferDomain(meta({ slug: "capabilities", rest: "/api/v1/us-radar/providers/capabilities" }))).toBe("us-radar");
    expect(inferDomain(meta({ slug: "random", rest: "/api/v1/tuning/results" }))).toBe("tuning");
  });

  it("无匹配归 general", () => {
    expect(inferDomain(meta({ slug: "zzz-unknown" }))).toBe("general");
  });
});

describe("unwrapRows(自动解包)", () => {
  it("顶层数组 → table", () => {
    const r = unwrapRows([{ id: 1 }, { id: 2 }], "general");
    expect(r).toEqual({ kind: "table", rows: [{ id: 1 }, { id: 2 }] });
  });

  it("dict 带配置数组键 → table(解包该数组)", () => {
    const r = unwrapRows({ capabilities: [{ name: "a" }], updated_at: "x" }, "us-radar");
    expect(r).toEqual({ kind: "table", rows: [{ name: "a" }] });
  });

  it("dict 数组键为空数组 → 视为 table 空(不误判 kv)", () => {
    const r = unwrapRows({ capabilities: [], updated_at: "x" }, "us-radar");
    expect(r).toEqual({ kind: "table", rows: [] });
  });

  it("纯 dict 无数组键 → kv 视图", () => {
    const r = unwrapRows({ window: "1h", total_liquidated_usd: 123456 }, "general");
    expect(r.kind).toBe("kv");
    if (r.kind === "kv") {
      expect(r.entries.map(([k]) => k)).toEqual(["window", "total_liquidated_usd"]);
    }
  });

  it("dex portfolio → 解包 positions 数组", () => {
    const r = unwrapRows({ positions: [{ symbol: "SOL", pnl: 100 }] }, "dex");
    expect(r).toEqual({ kind: "table", rows: [{ symbol: "SOL", pnl: 100 }] });
  });

  it("polymarket 各 dict 数组键 → 解包(results/addresses/entries/rules)", () => {
    expect(unwrapRows({ results: [{ a: 1 }] }, "polymarket").kind).toBe("table");
    expect(unwrapRows({ addresses: [{ a: 1 }] }, "polymarket").kind).toBe("table");
    expect(unwrapRows({ entries: [{ a: 1 }] }, "polymarket").kind).toBe("table");
    expect(unwrapRows({ rules: [{ a: 1 }] }, "polymarket").kind).toBe("table");
  });

  it("数组内非对象元素 → kv 索引视图", () => {
    const r = unwrapRows(["a", "b"], "general");
    expect(r.kind).toBe("kv");
    if (r.kind === "kv") {
      expect(r.entries.map(([, v]) => v)).toEqual(["a", "b"]);
    }
  });

  it("null/undefined/原语 → kv 空", () => {
    expect(unwrapRows(null, "general")).toEqual({ kind: "kv", entries: [] });
    expect(unwrapRows(42, "general")).toEqual({ kind: "kv", entries: [] });
  });
});

describe("arrayKeysForDomain(数组键配置)", () => {
  it("每域都有数组键配置", () => {
    const domains: ContentDomain[] = [
      "us-radar", "polymarket", "derivatives", "paper", "intelligence", "alpha", "dex",
      "serenity", "strategies", "trading", "markets", "onchain", "research", "chat",
      "marketplace", "hyperliquid", "kf-index", "mcp", "skills", "notifications", "grid",
      "tuning", "backtests", "optimizations", "general",
    ];
    for (const d of domains) expect(arrayKeysForDomain(d).length).toBeGreaterThan(0);
  });
});

describe("isDynamicPage(动态页判定)", () => {
  it("非 symbol 占位符 → 动态(需从列表进入)", () => {
    expect(isDynamicPage(meta({ slug: "backtests-task-id", rest: "/api/v1/backtests/{task_id}" }))).toBe(true);
    expect(isDynamicPage(meta({ slug: "chat-sessions-session-id", rest: "/api/v1/chat/sessions/{session_id}" }))).toBe(true);
    expect(isDynamicPage(meta({ slug: "strategies-strategy-id", rest: "/api/v1/strategies/{strategy_id}" }))).toBe(true);
    expect(isDynamicPage(meta({ slug: "dex-pair-addr", rest: "/api/v1/dex/pair/{chain}/{pair_address}" }))).toBe(true);
  });

  it("symbol 占位符 → 非动态(SymbolPanel 正常拉数)", () => {
    expect(isDynamicPage(meta({ slug: "ashare-factors-symbol", rest: "/api/v1/ashare/factors/{symbol}" }))).toBe(false);
    expect(isDynamicPage(meta({ slug: "kf", rest: "/api/v1/markets/{symbol}/kline" }))).toBe(false);
  });

  it("静态无占位符 → 非动态", () => {
    expect(isDynamicPage(meta({ slug: "backtests", rest: "/api/v1/backtests" }))).toBe(false);
    expect(isDynamicPage(meta({ slug: "dash", rest: "/api/v1/dashboard" }))).toBe(false);
  });

  it("rest 为空 → 非动态", () => {
    expect(isDynamicPage(meta({ slug: "risk", rest: undefined }))).toBe(false);
  });
});

describe("formatCellValue / abbrevNumber(格式化)", () => {
  it("大数字缩写", () => {
    expect(abbrevNumber(1_500_000)).toBe("1.50M");
    expect(abbrevNumber(12_000)).toBe("12.0K");
    expect(abbrevNumber(123)).toBe("123.00");
    expect(abbrevNumber(-5_000)).toBe("-5.0K");
    expect(abbrevNumber(NaN)).toBe("—");
  });

  it("对象截断 40 字符", () => {
    const v = formatCellValue({ a: 1, b: "x".repeat(100) });
    expect(v.length).toBeLessThanOrEqual(40);
    expect(v).toContain("{");
  });

  it("null/undefined → '—'", () => {
    expect(formatCellValue(null)).toBe("—");
    expect(formatCellValue(undefined)).toBe("—");
  });

  it("字符串透传", () => {
    expect(formatCellValue("hello")).toBe("hello");
  });
});

describe("humanizeKey(键名美化)", () => {
  it("snake_case → 空格标题", () => {
    expect(humanizeKey("total_liquidated_usd")).toBe("Total Liquidated Usd");
    expect(humanizeKey("fundingRate")).toBe("Funding Rate");
  });
});

describe("全清单 isDynamicPage 分类(11 动态页特殊态覆盖)", () => {
  it("11 个非 symbol 动态页全被识别为动态(1001 删 versions 26→25;1002 删 3 死占位 25→22;1002 detail-batches 删 11 薄壳 22→11)", () => {
    const dynamic = ALL_PAGES.filter((p) => isDynamicPage(p));
    expect(dynamic.length).toBeGreaterThanOrEqual(11);
    // 抽查代表 slug(agent-analysis-* 已随 US-209 收敛删除;skills-name 已随 detail-batches 删除)
    const slugs = dynamic.map((p) => p.slug);
    for (const s of [
      "backtests-task-id",
      "paper-accounts-account-id",
      "chat-sessions-session-id",
      "strategies-strategy-id",
      "polymarket-events-event-id",
      "dex-pair-chain-pair-address",
      "research-tuning-tasks-task-id",
      "optimizations-optimization-id",
    ]) {
      expect(slugs).toContain(s);
    }
  });

  it("8 个 symbol 动态页({symbol})不误判为动态", () => {
    const symPages = ALL_PAGES.filter((p) => (p.rest ?? "").includes("{symbol}"));
    expect(symPages.length).toBe(8); // US-208 删 markets-symbol-path-* 3 条 14→11;US-209 删 serenity-scan-symbol 11→10;detail-batches 删 serenity-deep/history 10→8
    for (const p of symPages) expect(isDynamicPage(p)).toBe(false);
  });

  it("动态页 rest 都带非 symbol 占位符,静态页无占位符", () => {
    for (const p of ALL_PAGES) {
      const rest = p.rest ?? "";
      if (isDynamicPage(p)) {
        expect(rest).toMatch(/{[^}]+}/);
        expect(rest).not.toMatch(/\{(?:symbol|sym)\}/);
      }
    }
  });
});

describe("全清单 inferDomain(内容域全覆盖)", () => {
  it("所有 manifest 页都能归域(不抛错)", () => {
    for (const p of ALL_PAGES) {
      expect(() => inferDomain(p)).not.toThrow();
      expect(() => arrayKeysForDomain(inferDomain(p))).not.toThrow();
    }
  });
});

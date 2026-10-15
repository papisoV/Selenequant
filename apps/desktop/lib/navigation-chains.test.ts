import { describe, expect, it } from "vitest";
import { ALL_PAGES, type Scene } from "./page-manifest";
import {
  CHAINS,
  resolveChainPage,
  chainStepsOf,
  moreSlugsOf,
  toolSlugsOf,
} from "./navigation-chains";

const ALL_SLUGS = new Set(ALL_PAGES.map((p) => p.slug));

describe("navigation-chains 链定义", () => {
  it("每个场景都有链定义", () => {
    const scenes: Scene[] = ["watch", "research", "trade", "system"];
    for (const sc of scenes) {
      expect(CHAINS[sc].length).toBeGreaterThan(0);
    }
  });

  it("每链 steps 的 slug 全部存在于 ALL_PAGES", () => {
    for (const chains of Object.values(CHAINS)) {
      for (const chain of chains) {
        for (const slug of chain.steps) {
          expect(ALL_SLUGS.has(slug), `${chain.label} 含未注册 slug ${slug}`).toBe(true);
        }
      }
    }
  });

  it("链内无重复 slug", () => {
    for (const chains of Object.values(CHAINS)) {
      for (const chain of chains) {
        expect(new Set(chain.steps).size, `${chain.label} 含重复 slug`).toBe(chain.steps.length);
      }
    }
  });

  it("跨链无重复 slug(slug 全局唯一映射)", () => {
    const seen = new Set<string>();
    for (const chains of Object.values(CHAINS)) {
      for (const chain of chains) {
        for (const slug of chain.steps) {
          expect(seen.has(slug), `${slug} 在多个链中出现`).toBe(false);
          seen.add(slug);
        }
      }
    }
  });

  it("链内无 _ 后缀 slug(合法下划线名除外,如 vol_surface)", () => {
    const LEGAL = new Set(["vol_surface", "smart_money", "alpha_mine"]);
    for (const chains of Object.values(CHAINS)) {
      for (const chain of chains) {
        for (const slug of chain.steps) {
          if (LEGAL.has(slug)) continue;
          expect(slug.includes("_"), `${slug} 含下划线,疑为 _ 变体混入链`).toBe(false);
        }
      }
    }
  });
});

describe("resolveChainPage", () => {
  it("链内 slug 命中并返回 chainId + stepIndex", () => {
    const hit = resolveChainPage("kf");
    expect(hit).toEqual({ chainId: "price", stepIndex: 0 });
    expect(resolveChainPage("tuning")).toEqual({ chainId: "strategy-dev", stepIndex: 4 });
    expect(resolveChainPage("risk")).toEqual({ chainId: "live", stepIndex: 6 });
  });

  it("未入链 slug 返回 null(进'更多')", () => {
    expect(resolveChainPage("dex-pair-chain-pair-address")).toBeNull();
    expect(resolveChainPage("polymarket-markets")).toBeNull();
    expect(resolveChainPage("nope-not-a-page")).toBeNull();
  });
});

describe("chainStepsOf / moreSlugsOf", () => {
  it("chainStepsOf 返回场景所有链的 slug 平铺集合", () => {
    const watch = chainStepsOf("watch");
    const expected = CHAINS.watch.flatMap((c) => c.steps);
    expect([...watch].sort()).toEqual([...new Set(expected)].sort());
  });

  it("moreSlugsOf 返回未入链且未被 hub 收编页,且 all = 入链 + 更多 + 被收编 + 工具/对象类", () => {
    for (const sc of ["watch", "research", "trade", "system"] as Scene[]) {
      const inChains = chainStepsOf(sc);
      const more = moreSlugsOf(sc);
      // more 不含 _ 变体(已在 DEDUPE 列表,不在链中,必然在 more 里,但此处只查链外)
      for (const slug of more) expect(inChains.has(slug)).toBe(false);
      // 该场景所有页 = 入链 ∪ 更多 ∪ 被收编 ∪ 工具/对象类(fate 页,⌘K 可达;watch 更多只余候选)
      const sceneSlugs = ALL_PAGES.filter((p) => p.scene === sc).map((p) => p.slug);
      const absorbed = ALL_PAGES.filter((p) => p.scene === sc && p.absorbedBy).map((p) => p.slug);
      const fated = ALL_PAGES.filter((p) => p.scene === sc && p.fate && p.fate !== "candidate").map((p) => p.slug);
      const union = new Set([...inChains, ...more, ...absorbed, ...fated]);
      expect(union.size).toBe(new Set(sceneSlugs).size);
    }
  });

  it("watch 候审制:更多组只含 fate=candidate,工具组 = fate=tool", () => {
    const more = moreSlugsOf("watch");
    const bySlug = new Map(ALL_PAGES.map((p) => [p.slug, p]));
    expect(more.length).toBeGreaterThan(0);
    for (const slug of more) {
      expect(bySlug.get(slug)?.fate, `${slug} 应是 candidate`).toBe("candidate");
    }
    const tools = toolSlugsOf("watch");
    expect(tools.sort()).toEqual(["quant_tools", "vchart", "intelligence-alerts-rules"].sort());
    // 其他场景:更多 = 未入链未收编且非 detail(参数化详情页退侧栏,路由+⌘K 仍可达)
    const tradeMore = moreSlugsOf("trade");
    const tradeInChains = chainStepsOf("trade");
    const tradeExpected = ALL_PAGES.filter(
      (p) => p.scene === "trade" && !tradeInChains.has(p.slug) && !p.absorbedBy && p.fate !== "detail",
    ).map((p) => p.slug);
    expect(tradeMore.sort()).toEqual(tradeExpected.sort());
  });

  it("更多组显著瘦身:watch 入链数 < 全部页数", () => {
    const inChains = chainStepsOf("watch").size;
    const watchTotal = ALL_PAGES.filter((p) => p.scene === "watch").length;
    expect(inChains).toBeLessThan(watchTotal);
  });
});

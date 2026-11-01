import { describe, expect, it } from "vitest";
import { readdirSync } from "node:fs";
import { join } from "node:path";
import { ALL_PAGES, type Scene } from "./page-manifest";
import { resolveChainPage, DEDUPE_UNDERSCORE_SLUGS, CHAINS } from "./navigation-chains";

// 断页 allowlist:所有 app/**/page.tsx 路由中未在 ALL_PAGES 注册的 slug(排除 4 个合法非 manifest 页)
// 新增页面必须注册到 page-manifest.ts 并从这里移除;出现未注册新页会红
// US-209 收敛后仅剩 questions/new(立问页,questions 子页功能页)
const KNOWN_UNREGISTERED_SLUGS: string[] = ["new"];

// 合法非 manifest 页:landing(app/page.tsx→"") + 3 workspace 布局页(不走 PageRenderer)
// "(landing)" 是 Next.js route group,URL 即根 "/" —— landing 页从 app/page.tsx 迁入
// app/(landing)/page.tsx 后,walk 取到的末段变成 "(landing)",故一并列入。
const LEGAL_NON_MANIFEST_ROUTES = new Set(["", "(landing)", "workspace", "workspace/signal", "workspace/[layoutId]"]);

function listPageRoutes(): string[] {
  const root = join(process.cwd(), "app");
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, e.name);
      if (e.isDirectory()) walk(full);
      else if (e.name === "page.tsx") {
        let rel = full.slice(root.length + 1).replace(/\\/g, "/").replace(/\/page\.tsx$/, "");
        if (rel === "page.tsx") rel = ""; // root landing
        out.push(rel);
      }
    }
  };
  walk(root);
  return out;
}

describe("page-manifest 注册完整性", () => {
  it("intelligence-alerts-rules 已注册且字段完整;favorites 能力在 sidebar+⌘K(K9 删 route)", () => {
    const slugs = ALL_PAGES.map((p) => p.slug);
    expect(slugs).not.toContain("favorites"); // K9:r4 批删,自选能力由 useSymbolFavorites 提供
    expect(slugs).toContain("intelligence-alerts-rules");
    // slug 唯一
    expect(new Set(slugs).size).toBe(slugs.length);
    // scene 合法
    const scenes = new Set<Scene>(["watch", "research", "trade", "system", "workspace"]);
    for (const p of ALL_PAGES) expect(scenes.has(p.scene)).toBe(true);
    expect(ALL_PAGES.find((p) => p.slug === "intelligence-alerts-rules")).toMatchObject({
      title: "情报预警规则", scene: "watch", category: "数据", origin: "backend-insert",
    });
  });

  it("全量断页扫描 === KNOWN_UNREGISTERED_SLUGS(新增断页才红)", () => {
    const routeSlugs = listPageRoutes()
      .filter((r) => !LEGAL_NON_MANIFEST_ROUTES.has(r))
      .map((r) => r.split("/").pop()!);
    const registered = new Set(ALL_PAGES.map((p) => p.slug));
    const unregistered = [...new Set(routeSlugs)].filter((s) => !registered.has(s)).sort();
    expect(unregistered).toEqual(KNOWN_UNREGISTERED_SLUGS);
  });

  it("入链 slug 必须有物理路由页(反向扫描 —— 侧边栏 <Link> 会 404)", () => {
    // 上面的断页扫描只覆盖 route → manifest 一个方向：注册了但没有 app/<scene>/<slug>/page.tsx
    // 的 slug 它看不到，而侧边栏用 <Link href="/{scene}/{slug}"> 做真实导航，缺页即 404。
    // 这里补反方向：凡是进了导航链的 slug，都必须存在对应路由页。
    const routeDirs = new Set(
      listPageRoutes()
        .filter((r) => !LEGAL_NON_MANIFEST_ROUTES.has(r))
        .map((r) => r.split("/").slice(-2).join("/")),
    );
    const chained = new Set<string>();
    for (const [scene, chains] of Object.entries(CHAINS)) {
      for (const chain of chains) {
        for (const slug of chain.steps) chained.add(`${scene}/${slug}`);
      }
    }
    const missing = [...chained].filter((p) => !routeDirs.has(p)).sort();
    expect(missing, `入链但无路由页(点击 404): ${missing.join(", ")}`).toEqual([]);
  });
});

describe("sidebar 去重约束(_ 变体不占用链步骤)", () => {
  it("所有 _ 变体 slug 仍注册在 ALL_PAGES(路由完整性不破坏)", () => {
    const all = new Set(ALL_PAGES.map((p) => p.slug));
    for (const s of DEDUPE_UNDERSCORE_SLUGS) {
      expect(all.has(s), `${s} 应保留在 ALL_PAGES`).toBe(true);
    }
  });

  it("所有 _ 变体都 resolve 不到任何链步骤(只在'更多'里)", () => {
    for (const s of DEDUPE_UNDERSCORE_SLUGS) {
      expect(resolveChainPage(s), `${s} 不应是链步骤`).toBeNull();
    }
  });

  it("DEDUPE 列表覆盖全部含 _ 且有 - twin 的重复注册 slug", () => {
    const allSlugs = new Set(ALL_PAGES.map((p) => p.slug));
    const underscored = ALL_PAGES.filter((p) => p.slug.includes("_"));
    for (const p of underscored) {
      // 有同名 - twin(把 _ 换 - 后也存在于 ALL_PAGES)才叫重复变体
      const twinDash = p.slug.replace(/_/g, "-");
      if (allSlugs.has(twinDash)) {
        expect(DEDUPE_UNDERSCORE_SLUGS.has(p.slug), `${p.slug} 应有 - twin ${twinDash} 故必须进 DEDUPE`).toBe(true);
      }
    }
  });

  it("DEDUPE 里的 slug 都有同名 - twin(列表不漂移)", () => {
    const allSlugs = new Set(ALL_PAGES.map((p) => p.slug));
    for (const s of DEDUPE_UNDERSCORE_SLUGS) {
      const twinDash = s.replace(/_/g, "-");
      expect(allSlugs.has(twinDash), `${s} 的 - twin ${twinDash} 应存在`).toBe(true);
    }
  });
});

describe("下架页（隐藏≠删除）", () => {
  it("台股已下架：sidebar/⌘K 不呈现，但路由/组件保留（2026-10-01 拍板，受众太小）", () => {
    const tw = ALL_PAGES.find((p) => p.slug === "taiwan");
    expect(tw, "taiwan 必须仍在 ALL_PAGES（隐藏≠删除，路由 app/watch/taiwan 可达）").toBeDefined();
    // 下架走 absorbedBy 语义（与 liqmap/onchain 等同一机制），不再新造 hidden 字段
    expect(tw!.absorbedBy, "taiwan 应被 hub 收编（absorbedBy 非空）").toBeTruthy();
  });

  it("sidebar 页面组不含被 hub 收编的页", async () => {
    const { moreSlugsOf } = await import("./navigation-chains");
    for (const scene of ["watch", "research", "trade", "system", "workspace"] as Scene[]) {
      const slugs = new Set(moreSlugsOf(scene));
      const leaked = ALL_PAGES.filter((p) => p.scene === scene && p.absorbedBy && slugs.has(p.slug));
      expect(
        leaked.map((p) => p.slug),
        `${scene} 场景"更多"组不应含 absorbedBy 页`
      ).toEqual([]);
    }
  });
});

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * 研究面滚动契约锁(审计 2026-10-21 ④ / 2026-10-22 落地)。
 *
 * 唯一滚动容器 = 布局层 `.scene-layout`(globals.css: `overflow: hidden auto`)。
 * 页面根不再自带 `h-full overflow-y-auto`,否则会出现内外两层滚动,
 * 而内层绑定的是 `.scene-layout` 的高度,比内容短时滚动范围全靠底部 padding 撑 ——
 * 实测 today 的 pb-[300px] 产生 334px 滚动范围,其中 331px 是纯空白
 * (真实遮挡物只有 fixed 的 .feed-bar 28px 与右缘 agent-fab 44px)。
 *
 * 两类页面:
 *  - 文档流页(today/mem/questions/strategy-cockpit/strat/rsch):根节点只做宽度/留白,
 *    高度交给 `.scene-layout` 自然增长;
 *  - 填满框架页(desk/missions 已由 .scene-layout 直滚;pa/graph 是 flex 定高工作台),
 *    允许 `h-full`,但同样不许自带 overflow-y-auto 与超大底部 padding。
 */
const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..", "..", "..");

const read = (rel: string) => readFileSync(join(ROOT, rel), "utf8");

// 文档流页的组件根:不许自带滚动容器
const DOCUMENT_FLOW_ROOTS = [
  "apps/desktop/components/research/today-view.tsx",
  "apps/desktop/components/research/memory-surface-page.tsx",
  "apps/desktop/components/research/question-surface.tsx",
  "apps/desktop/components/research/strat-page.tsx",
  "apps/desktop/components/research/rsch-page.tsx",
  "apps/desktop/components/research/strategy-detail-view.tsx",
];

describe("research scroll contract", () => {
  it("文档流页根节点不自带 overflow-y-auto(避免内外双层滚动)", () => {
    for (const f of DOCUMENT_FLOW_ROOTS) {
      const src = read(f);
      const bad = src
        .split("\n")
        .map((text, i) => ({ ln: i + 1, text }))
        .filter((l) => l.text.includes("overflow-y-auto") && l.text.includes("h-full"));
      expect(bad, `${f} 仍有 h-full + overflow-y-auto 内滚: ${JSON.stringify(bad.map((b) => b.ln))}`).toEqual([]);
    }
  });

  it("没有任何页面根用 pb-[300px] 这类死区 padding", () => {
    // 300px 是历史遗留(叠加了抽屉展开态 + FAB 的旧几何);实测折叠态下场景底
    // 842 / feed 顶 872,连 feed-bar 都不遮挡,300px 全是白滚。
    for (const f of DOCUMENT_FLOW_ROOTS) {
      expect(read(f).includes("pb-[300px]"), `${f} 仍有 300px 死区 padding`).toBe(false);
    }
  });

  it("布局层仍且仅由 .scene-layout 承担纵向滚动", () => {
    const css = read("apps/desktop/app/globals.css");
    const m = css.match(/\.scene-layout\s*\{[^}]*\}/);
    expect(m, "globals.css 缺 .scene-layout 规则").not.toBeNull();
    expect(m![0]).toMatch(/overflow:\s*hidden auto/);
    expect(m![0]).toMatch(/min-height:\s*0/);
  });
});

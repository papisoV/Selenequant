import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * 轮询节奏统一锁(审计 2026-10-21 ④:全模块 8 种刷新间隔)。
 *
 * 锁两件事:
 * 1. 页面级轮询必须引用 research-poll 的统一档位常量,不许出现新字面量节拍;
 * 2. 手写 setInterval 轮询(desk/heartbeat 类)必须经 pollWhenVisible 隐藏页暂停,
 *    SWR refreshInterval 自带暂停,不受此约束。
 *
 * 任务型轮询(进度条 2-3s、事务等待结束后即停)不在锁内,允许字面量。
 */
const HERE = dirname(fileURLToPath(import.meta.url)); // apps/desktop/lib/hooks
const ROOT = join(HERE, "..", "..", "..", ".."); // 副本根（worktree 根）

// 页面级轮询的宿主文件(改名单 = 有意识的决定,测试跟着改)
const PAGE_LEVEL_FILES = [
  "apps/desktop/components/research/today-page.tsx",
  "apps/desktop/components/research/missions-page.tsx",
  "apps/desktop/components/research/desk-page.tsx",
  "apps/desktop/components/research/heartbeat-strip.tsx",
  // 2026-10-22:两处漏网的字面量节拍(mem 60000 / strat 15000)收进统一档位后入锁
  "apps/desktop/components/research/memory-surface-page.tsx",
  "apps/desktop/components/research/strat-page.tsx",
];

// 这些文件允许存在任务型 setInterval 字面量(等待结束即停,非页面刷新)
const TASK_POLL_EXEMPT = [
  "apps/desktop/components/research/cards/qd-strategy-card.tsx",
  "apps/desktop/components/research/question-surface.tsx",
  "apps/desktop/components/research/alpha-mining-panel.tsx",
  "apps/desktop/components/research/strategy-workbench.tsx",
];

const read = (rel: string) => readFileSync(join(ROOT, rel), "utf8");

describe("research poll cadence lock", () => {
  it("页面级轮询文件全部引用统一档位常量", () => {
    for (const f of PAGE_LEVEL_FILES) {
      const src = read(f);
      expect(
        /RESEARCH_POLL_(LIVE|NORMAL|SLOW)_MS/.test(src),
        `${f} 应引用 RESEARCH_POLL_*_MS 常量`
      ).toBe(true);
    }
  });

  it("页面级轮询文件不再有字面量 refreshInterval/POLL 间隔", () => {
    const literal = /refreshInterval:\s*\d|POLL(?:_INTERVAL)?_MS\s*=\s*\d/;
    for (const f of PAGE_LEVEL_FILES) {
      const bad = read(f)
        .split("\n")
        .map((ln, i) => ({ ln: i + 1, text: ln }))
        .filter((l) => literal.test(l.text));
      expect(bad, `${f} 出现字面量节拍: ${JSON.stringify(bad.map((b) => b.ln))}`).toEqual([]);
    }
  });

  it("手写 setInterval 页面轮询必须经 pollWhenVisible 隐藏页暂停", () => {
    for (const f of ["apps/desktop/components/research/desk-page.tsx", "apps/desktop/components/research/heartbeat-strip.tsx"]) {
      const src = read(f);
      expect(src.includes("pollWhenVisible"), `${f} 的 setInterval 应包 pollWhenVisible`).toBe(true);
      // 不允许裸 setInterval(fn, ...) 直接驱动页面轮询
      const bare = src
        .split("\n")
        .filter((ln) => /setInterval\(\s*(load|poll)\b/.test(ln) && !ln.includes("pollWhenVisible"));
      expect(bare, `${f} 存在裸 setInterval 轮询: ${bare.join(" | ")}`).toEqual([]);
    }
  });

  it("任务型轮询豁免名单与实际文件对得上(防名单腐化)", () => {
    for (const f of TASK_POLL_EXEMPT) {
      expect(() => read(f), `${f} 不存在 — 豁免名单腐化`).not.toThrow();
    }
  });
});

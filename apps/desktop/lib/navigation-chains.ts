/**
 * 场景链式导航定义 — sidebar 分场景聚合的单一来源
 *
 * 与 page-manifest.ts 的关系:
 *  - ALL_PAGES 仍是路由完整性的权威(221 页全部注册,隐藏≠删除)
 *  - 本模块只在 ALL_PAGES 之上定义"哪些页串成任务链",供 sidebar 分组渲染
 *  - 未列入任何链的页收进该场景的"更多 (N)"折叠组,路由 + ⌘K 仍可达
 */
import type { Scene } from "./page-manifest";
import { ALL_PAGES } from "./page-manifest";

export interface NavigationChain {
  id: string;
  label: string;
  steps: string[];
  /** workflow 链:渲染"流程"徽章 + 步骤序号。✓ 进度仅 strategy-dev 有 store 支撑
   *  (lib/store/workflow.ts);ai-org 等 7 页不在 wfDone 里,✓ 恒空(plan §6.5 已接受) */
  workflow?: boolean;
}

export const CHAINS: Record<Scene, NavigationChain[]> = {
  watch: [
    { id: "price", label: "行情链", steps: ["kf", "kfi", "mk", "derivatives-hub", "tac"] },
    { id: "onchain", label: "链上链", steps: ["onchain-hub", "dex", "options-hub"] },
    { id: "intel", label: "情报链", steps: ["us-radar", "sig"] },
    // A股看盘链 — 把 A股从"只能研究"提到 watch 场景(2026-10-13)。
    // ashare-board = 看盘 hub(涨停池/连板梯队/情绪周期/板块排名),ashare-factors-symbol = 个股因子。
    // ashare-deep/ashare-regime 无物理路由页(absorbedBy:"mk",经 mk hub 可达),故不入链(守卫 :59 要求链步骤必有路由页)。
    { id: "ashare-watch", label: "A股看盘", steps: ["ashare-board", "ashare-factors-symbol"] },
  ],
  research: [
    // 问句链正典:现在需要我吗→有什么变化→团队在研究什么→这件事怎么回事→对策略意味着什么→我们学到了什么→沉淀的记忆
    { id: "ai-org", label: "AI 组织", steps: ["today", "desk", "questions", "missions", "strategy-cockpit", "graph", "episodes", "mem"], workflow: true },
    { id: "rsch-hub", label: "研究入口", steps: ["rsch"] },
    // A股研究链:数据底座(财报PIT)→现状(风格监测)→策略(回测)→组合(调仓引擎)。
    // 三页原为后端派生页,只落"更多"组,⌘K 才可达;此处入链使其浮到侧边栏
    // (2026-10-08 产品落地审计 P0-1);ashare-portfolio 为 batch10 组合调仓引擎前端页,
    // ashare-factor-report 为 batch4 IC 报告前端页(P0-3/P1-1)
    { id: "ashare", label: "A股研究", steps: ["ashare-fundamentals", "ashare-style", "ashare-backtest", "ashare-portfolio", "ashare-factor-report"] },
    { id: "strategy-dev", label: "策略开发", steps: ["strat", "factors", "optimizer", "scanner", "tuning", "strategies-strategy-id"], workflow: true },
    { id: "alpha", label: "因子挖掘", steps: ["alpha_mine", "alpha-formulas", "alpha-vocab", "alpha-saved-strategies"] },
    { id: "backtest-tuning", label: "回测调优", steps: ["research-tuning-tasks", "research-tuning-results", "strategies", "strategies-deployments-list", "qevolve"] },
    { id: "risk", label: "风险分析", steps: ["serenity", "serenity-factors", "serenity-scores", "pa", "experiments", "experiment-evidence"] },
  ],
  trade: [
    { id: "paper", label: "模拟链", steps: ["backtests", "optimizations", "paper", "paper-positions", "paper-orders", "paper-fills", "paper-pnl"] },
    { id: "live", label: "实盘链", steps: ["pos", "trd", "trading-history", "trading-balances", "trading-kill-switch", "trading-latency-stats", "risk"] },
  ],
  system: [
    { id: "system", label: "系统链", steps: ["dash", "set", "notifications", "me-export"] },
    { id: "agent", label: "Agent链", steps: ["agent", "chat-sessions", "agent-trading-workspace"] },
    { id: "marketplace", label: "市场链", steps: ["marketplace", "skill", "mcp"] },
  ],
  workspace: [],
};

// 去重:同端点 -/_ 占位符变体只保留一条进链。slug 含 _ 的(如 dex-pair-chain-pair_address)
// 视为重复变体,不进任何链 steps,只落在"更多"里。列表 = 全部存在同名 - twin 的 _ 变体。
// 注意:vol_surface / smart_money / alpha_mine 等是合法下划线名(无 - twin),不在列表内。
// q 分支(透明性批合并)已删除 2 组 _ 双胞胎路由/注册(dex-pair-chain-pair_address、
// dex-tokens-token_address),连字符版承载同端点能力,故本集现为空——机制与守卫测试保留,
// 重引下划线变体会被 page-manifest.test 守卫拦下。
export const DEDUPE_UNDERSCORE_SLUGS: ReadonlySet<string> = new Set([]);

/** slug → {chainId, stepIndex} | null;未入链返回 null(进"更多") */
export function resolveChainPage(slug: string): { chainId: string; stepIndex: number } | null {
  for (const chains of Object.values(CHAINS)) {
    for (const chain of chains) {
      const stepIndex = chain.steps.indexOf(slug);
      if (stepIndex !== -1) return { chainId: chain.id, stepIndex };
    }
  }
  return null;
}

/** 场景的链 slug 全集(用于"更多"剔除) */
export function chainStepsOf(scene: Scene): Set<string> {
  return new Set(CHAINS[scene].flatMap((c) => c.steps));
}

/** 该场景未入任何链的 slug 列表(即"更多"组);已被 hub 收编的页不再出现。
 *  watch 场景 r5 候审制:只返回 fate==="candidate" 的页,其余归工具室/⌘K。
 *  其余场景:fate==="detail" 的参数化详情页不进侧栏(从父页列表点行进入),路由+⌘K 仍可达。 */
export function moreSlugsOf(scene: Scene): string[] {
  const inChains = chainStepsOf(scene);
  const base = ALL_PAGES.filter((p) => p.scene === scene && !inChains.has(p.slug) && !p.absorbedBy);
  if (scene === "watch") return base.filter((p) => p.fate === "candidate").map((p) => p.slug);
  return base.filter((p) => p.fate !== "detail").map((p) => p.slug);
}

/** ④ 专业工具页(全屏工作流);watch 场景 sidebar 用独立"工具"分组展示 */
export function toolSlugsOf(scene: Scene): string[] {
  return ALL_PAGES.filter((p) => p.scene === scene && p.fate === "tool").map((p) => p.slug);
}

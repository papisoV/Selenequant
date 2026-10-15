"use client";

/**
 * ⑤ 实验台 —— 研究回路脚下的仪器面(2026-10-07 补)。
 *
 * 来由:审计发现 landing 把「研究回路」讲透了,但广度能力零露面 ——
 * 49 内置模板 / 多交易所 / 8 个调参目标 / TG+宏观摄取 / CLI 在整页 0 命中
 * (记忆 project_landing-breadth-gap-audit-1007)。同 README 第 5 次同型
 * 「能力可以宽、叙事必须窄」的漏项。
 *
 * 设计:广度**不是第二条主张** —— 它是回路脚下的地面,故本节框成"回路的证据",
 * 不另立叙事。形制刻意区别于相邻两节(回测墙用卡片栅格、「不是什么」用对照表):
 * 本节用**一张非对称仪器平面**,三带形制各异:
 *   入口(源头)/ 台面(49 作密度条)/ 旋钮(调参目标)
 * 不枚举 49 个名字、不做 49 张卡、不用行表(回测墙 docstring 前车:行表把整页
 * 读成文档)。
 *
 * ⚠️ 数字口径:本节「内置模板 49」= 出厂固定目录(`factory.py` 里 49 行
 *    `_CatalogRow`,按 `StrategyCategory` 七族分组);与 ticker 的「策略 218」
 *    = 库中策略实例(会增长,2026-10-02 快照)是**两个名词**,不冲突 —— 后者
 *    由前者派生。此处显式写「内置模板」以免相邻滚动被读成互相矛盾。
 *
 * 数据逐字来自仓库代码(factory.py 的 StrategyCategory 计数 / exchange-sdk
 * 的 venue 目录 / scripts/llm_param_tuner.py 的 --objective choices),
 * 与 landing 其余部分同一纪律:能回源,不编造。
 */

import { Reveal, SectionMarker } from "../primitives";

/* ── 台面:49 内置模板,按代码里真实的 StrategyCategory 七族分组 ───────────── */
/* 逐族计数取自 packages/strategy-engine/selene_space_strategy/factory.py
   (StrategyCategory 枚举出现次数):TREND 16 · MOMENTUM 9 · REVERSION 9 ·
   BREAKOUT 5 · VOLATILITY 5 · TIMING 4 · AI 1 = 49。 */
const TEMPLATE_FAMILIES: { label: string; en: string; count: number }[] = [
  { label: "趋势", en: "trend", count: 16 },
  { label: "动量", en: "momentum", count: 9 },
  { label: "均值回归", en: "reversion", count: 9 },
  { label: "突破", en: "breakout", count: 5 },
  { label: "波动率", en: "volatility", count: 5 },
  { label: "择时", en: "timing", count: 4 },
  { label: "AI", en: "ai", count: 1 },
];
const TEMPLATE_TOTAL = TEMPLATE_FAMILIES.reduce((n, f) => n + f.count, 0);
const FAMILY_MAX = Math.max(...TEMPLATE_FAMILIES.map((f) => f.count));

/* ── 入口:研究吃什么 —— 三个真实源族(venue 目录取自 exchange-sdk / 各包) ── */
const SOURCES: { market: string; venues: string[]; note: string }[] = [
  { market: "加密", venues: ["Binance", "OKX"], note: "现货 / 永续" },
  { market: "美股", venues: ["Alpaca"], note: "行情 + 交易" },
  { market: "摄取", venues: ["Telegram", "宏观事件"], note: "→ 因子 / 特征" },
];

/* ── 旋钮:8 个调参目标(逐字取自 llm_param_tuner.py 的 --objective choices) ─ */
const OBJECTIVES = [
  "sharpe",
  "total_return",
  "annual_return",
  "profit_factor",
  "win_rate",
  "calmar_ratio",
  "sortino_ratio",
  "expectancy",
];

export function Bench() {
  return (
    <section id="bench" className="lp-section lp-hair-b relative">
      <div className="lp-container">
        <Reveal>
          <SectionMarker
            label="实验台"
            count={`${TEMPLATE_TOTAL} 内置模板 · 7 族 · ${SOURCES.length} 类市场 · ${OBJECTIVES.length} 调参目标`}
          />
          <h2 className="lp-h2 mt-8 max-w-[22ch] text-ink">
            研究回路脚下,
            <br className="hidden sm:block" />
            是一整套真仪器。
          </h2>
          <p className="lp-lead lp-measure mt-5">
            回路能记住结论,前提是有结论可记。结论跑在这套台面上:
            真的市场、真的模板、真的调参目标。下面不是能力清单,
            是回路每天要用的台面,每条都能在代码里核对。
          </p>
        </Reveal>

        {/* ── 仪器平面:三带,细线分隔,每带形制不同 ───────────────────── */}
        <Reveal delay={0.1}>
          <div className="mt-14 border-t border-[var(--lp-hair)]">
            {/* 带一 · 入口(源头 → 因子) */}
            <div className="grid grid-cols-1 gap-x-10 gap-y-5 border-b border-[var(--lp-hair)] py-7 sm:grid-cols-[9rem_minmax(0,1fr)]">
              <div className="flex items-baseline gap-3 sm:block">
                <span className="num font-mono text-[0.75rem] tracking-[0.03em] text-ink-3">
                  入口
                </span>
              </div>
              <div className="flex flex-wrap items-baseline gap-x-8 gap-y-4">
                {SOURCES.map((s) => (
                  <div key={s.market} className="flex items-baseline gap-2.5">
                    <span className="text-[0.9375rem] text-ink-2">
                      {s.market}
                    </span>
                    <span className="text-[0.9375rem] font-medium text-ink">
                      {s.venues.join(" · ")}
                    </span>
                    <span className="num font-mono text-[0.75rem] text-ink-3">
                      {s.note}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* 带二 · 台面(49 作密度条 —— 用"形"呈现"计数") */}
            <div className="grid grid-cols-1 gap-x-10 gap-y-5 border-b border-[var(--lp-hair)] py-7 sm:grid-cols-[9rem_minmax(0,1fr)]">
              <div className="flex items-baseline gap-3 sm:block">
                <span className="num font-mono text-[0.75rem] tracking-[0.03em] text-ink-3">
                  台面
                </span>
                <span className="num mt-1 block font-mono text-[0.75rem] text-ink-3">
                  内置模板 {TEMPLATE_TOTAL}
                </span>
              </div>
              <div className="flex flex-col gap-2.5">
                {TEMPLATE_FAMILIES.map((f) => (
                  <div key={f.en} className="flex items-center gap-4">
                    <span className="w-[4.5rem] shrink-0 text-right text-[0.8125rem] text-ink-2">
                      {f.label}
                    </span>
                    {/* 密度条:每族 count 个刻度,长度即计数 */}
                    <div
                      className="flex items-center gap-[3px]"
                      aria-hidden
                      style={{ width: `${(f.count / FAMILY_MAX) * 100}%` }}
                    >
                      {Array.from({ length: f.count }).map((_, i) => (
                        <span
                          key={i}
                          className="h-3.5 flex-1 rounded-[1px] bg-primary/55"
                        />
                      ))}
                    </div>
                    <span className="num shrink-0 font-mono text-[0.8125rem] tabular-nums text-ink-3">
                      {f.count}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* 带三 · 旋钮(调参目标) */}
            <div className="grid grid-cols-1 gap-x-10 gap-y-5 py-7 sm:grid-cols-[9rem_minmax(0,1fr)]">
              <div className="flex items-baseline gap-3 sm:block">
                <span className="num font-mono text-[0.75rem] tracking-[0.03em] text-ink-3">
                  旋钮
                </span>
              </div>
              <div className="flex flex-col gap-3">
                <p className="text-[0.9375rem] leading-relaxed text-ink-2">
                  Bayesian 搜索(Optuna)按下面任一目标自动调参:
                </p>
                <div className="flex flex-wrap items-center gap-x-2 gap-y-2">
                  {OBJECTIVES.map((o) => (
                    <span
                      key={o}
                      className="num rounded-[3px] border border-[var(--lp-hair)] px-2 py-0.5 font-mono text-[0.75rem] text-ink-2"
                    >
                      {o}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </Reveal>

        <Reveal delay={0.18}>
          <p className="lp-meta mt-8">
            模板、市场、调参目标都在仓库里可数。回测引擎跑过的那些
            (包括跑输的)在上一节。
          </p>
        </Reveal>
      </div>
    </section>
  );
}

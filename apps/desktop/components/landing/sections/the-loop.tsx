"use client";

/**
 * ② THE LOOP —— 核心演示(全页最重的一段)。
 *
 * 一条真实研究线:一个问题(funding 拥挤度)→ 四个真实实验(其中一次判决翻转)
 * → 一条引用全部证据的结论 → 结论沉淀为可被引用的记忆。
 *
 * 结构:实验挂在一条竖轴上,右侧是引用它们的结论。用连续的轴 + 留白表达结构,
 * 不把内容套进"面板套卡片"的盒子(2026-10-03 重构:原三层嵌套边框读起来像设置面板)。
 * 所有数据逐字来自真实研究库(见 lib/landing/real-data.ts)。
 */

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import {
  LOOP_QUESTION,
  LOOP_EVIDENCE,
  type RealEvidence,
} from "@/lib/landing/real-data";
import { Reveal, SectionMarker } from "../primitives";
import { EvidenceConvergence } from "../evidence-convergence";

const VERDICT_STYLE: Record<string, string> = {
  "NO-GO": "border-neg/45 bg-neg/10 text-neg",
  翻案: "border-research/50 bg-research/10 text-research",
  "veto 不成立": "border-[var(--lp-hair-strong)] bg-surface-3 text-ink-2",
  INCONCLUSIVE: "border-gold/45 bg-gold/10 text-gold",
};

/** 一条实验:挂在轴上,判决用颜色说话;hover 时与右侧连线/引用行联动。 */
function EvidenceNode({
  ev,
  index,
  active,
  onActive,
}: {
  ev: RealEvidence;
  index: number;
  active: boolean;
  onActive: (id: string | null) => void;
}) {
  const isReversal = ev.verdict === "翻案";
  const style =
    VERDICT_STYLE[ev.verdict] ?? VERDICT_STYLE["veto 不成立"];

  return (
    <Reveal delay={index * 0.07} y={12}>
      <div
        className="flex gap-4"
        onMouseEnter={() => onActive(ev.id)}
        onMouseLeave={() => onActive(null)}
      >
        {/* 轴节点 */}
        <div className="relative flex w-[26px] shrink-0 justify-center">
          <span className="absolute inset-y-0 w-px bg-[var(--lp-hair-strong)]" />
          <span
            className={`relative mt-[9px] inline-flex size-[13px] items-center justify-center rounded-full border transition-colors ${
              isReversal
                ? "border-research bg-research/25"
                : active
                  ? "border-research bg-research/20"
                  : "border-[var(--lp-hair-strong)] bg-surface-2"
            }`}
          >
            <span
              className={`size-[4px] rounded-full ${isReversal || active ? "bg-research" : "bg-ink-4"}`}
            />
          </span>
        </div>

        {/* 内容:靠留白分层,不用卡片 */}
        <div className="min-w-0 flex-1 pb-8">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
            <span
              className={`num rounded border px-2 py-0.5 text-[0.8125rem] font-medium transition-opacity ${style} ${
                active ? "opacity-100" : "opacity-90"
              }`}
            >
              {ev.verdict}
            </span>
            <span className="text-[1rem] font-semibold text-ink">
              {ev.episodeTitle}
            </span>
            <span className="num ml-auto text-[0.8125rem] text-ink-3">
              {ev.observedAt}
            </span>
          </div>
          <p className="mt-2 text-[0.9375rem] leading-relaxed text-ink-2">
            {ev.keyNumbers}
          </p>
          <div className="num mt-2 flex flex-wrap items-center gap-2 font-mono text-[0.75rem] text-ink-3">
            <span className="rounded bg-surface-3 px-1.5 py-px text-ink-3">
              {ev.mode}
            </span>
            <span className="truncate">{ev.sourceRef}</span>
          </div>
        </div>
      </div>
    </Reveal>
  );
}

export function TheLoop() {
  const q = LOOP_QUESTION;
  const rev = q.revisions[0];
  const [active, setActive] = useState<string | null>(null);

  return (
    <section id="the-loop" className="lp-section lp-hair-b relative">
      <div className="lp-container">
        <Reveal>
          <SectionMarker label="研究回路" count={`${LOOP_EVIDENCE.length} 条实验 · 全部 RECORDED`} />
          <h2 className="lp-h2 mt-8 max-w-[24ch] text-ink">
            一个判断,走了四次实验,
            <br className="hidden sm:block" />
            其中一次推翻了上一次的自己。
          </h2>
          <p className="lp-lead lp-measure mt-5">
            下面每个节点都是 Selene 真实跑过的一次实验,判决逐字来自研究库。
            右侧的结论,引用了全部四次实验结果。
          </p>
        </Reveal>

        <div className="mt-16 grid grid-cols-1 gap-14 lg:grid-cols-[minmax(0,1.12fr)_minmax(0,1fr)] lg:gap-20">
          {/* 左:实验链(直接落在页面上) */}
          <div>
            <div className="mb-7 flex items-baseline gap-3">
              <span className="text-[0.875rem] font-medium text-ink-2">
                实验链
              </span>
              <span className="h-px flex-1 bg-[var(--lp-hair)]" />
            </div>
            {LOOP_EVIDENCE.map((ev, i) => (
              <EvidenceNode
                key={ev.id}
                ev={ev}
                index={i}
                active={active === ev.id}
                onActive={setActive}
              />
            ))}
          </div>

          {/* 右:结论 + 引用全部证据 */}
          <Reveal delay={0.16}>
            <div className="lg:sticky lg:top-10">
              <div className="relative">
                {/* 汇聚图:四条真实证据 → 一条结论(hover 与左轴/引用行联动) */}
                <EvidenceConvergence active={active} onActive={setActive} />
                <div
                  className="absolute -inset-x-4 -inset-y-3 -z-10 rounded-xl"
                  style={{
                    background:
                      "radial-gradient(70% 60% at 50% 30%, rgba(34,211,238,0.1), transparent 70%)",
                  }}
                />
                <div className="rounded-lg border border-gold/35 bg-surface-2">
                  <div className="flex items-center gap-2.5 border-b border-gold/20 px-5 py-3.5">
                    <span className="num rounded bg-primary px-2 py-0.5 font-mono text-[0.8125rem] font-semibold text-primary-foreground">
                      v{rev.seq}
                    </span>
                    <span className="text-[0.8125rem] tracking-wide text-ink-3">
                      当前判断
                    </span>
                    <span className="num ml-auto text-[0.8125rem] text-ink-3">
                      {rev.createdAt}
                    </span>
                  </div>
                  <div className="px-5 py-5">
                    <h3 className="text-[1.125rem] font-semibold leading-snug text-ink">
                      {q.title}
                    </h3>
                    <p className="mt-3 text-[0.9375rem] leading-relaxed text-ink-2">
                      {rev.content}
                    </p>

                    <div className="mt-5 border-t border-[var(--lp-hair)] pt-4">
                      <div className="flex items-center gap-2">
                        <span className="text-[0.875rem] text-ink-3">它用了</span>
                        <span className="rounded border border-research/40 bg-research/[0.07] px-2 py-0.5 text-[0.8125rem] text-research">
                          全部 4 次实验结果
                        </span>
                      </div>
                      <ul className="mt-3 space-y-2">
                        {LOOP_EVIDENCE.map((ev) => (
                          <li
                            key={ev.id}
                            className={`num flex items-center gap-2 font-mono text-[0.75rem] transition-colors ${
                              active === ev.id
                                ? "text-research"
                                : "text-ink-3"
                            }`}
                          >
                            <ChevronRight className="size-3.5 shrink-0 text-research/60" />
                            <span>{ev.id}</span>
                            <span>→ {ev.verdict}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>
                </div>
              </div>

              {/* 下一站:记忆 */}
              <a
                href="#memory"
                className="group mt-4 flex items-center gap-2 rounded-lg border border-[var(--lp-hair)] px-5 py-4 transition-colors hover:border-accent/45"
              >
                <span className="text-[0.9375rem] text-ink-3">
                  这条结论现在的去向
                </span>
                <span className="ml-auto inline-flex items-center gap-1 text-[0.9375rem] font-medium text-accent">
                  进入记忆
                  <ChevronRight className="size-4 transition-transform group-hover:translate-x-0.5" />
                </span>
              </a>

              {/* 回路断点如实交代:决策已可记录(decisions=2,均未结案),
                  但执行环节仍为空(live_orders/live_fills/audit_trades = 0) */}
              <p className="mt-5 border-l-2 border-[var(--lp-hair-strong)] pl-4 text-[0.875rem] leading-relaxed text-ink-3">
                回路当前断在 Execution:实验做到结论、结论入了记忆、
                判断也已落成决策记录,但把结论变成一笔真实下单的执行记录,
                库里还是 0 条。下一段回路从这里接。
              </p>
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}

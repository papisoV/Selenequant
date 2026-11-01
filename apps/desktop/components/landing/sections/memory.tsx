"use client";

/**
 * ④ MEMORY —— 全页最锋利的一段。
 *
 * 主张:记忆不是存档,是改变行为的东西。证据 = 真实反向索引
 * (revision.cited_json 反查):这条知识被后续回答引用了几次。
 *
 * 版面:一张贯通的知识账本(不是三张等大卡片 —— 那是卡片栅格的反射)。
 * 每行左侧是知识正文,右侧是"被引用了几次 + 引它的是谁",把反向索引
 * 直接摆在纸面上。没有下游引用的一行,诚实显示为空(与产品军规⑤同源)。
 */

import { Check, CornerDownRight } from "lucide-react";
import { MEMORY_ENTRIES, type RealMemoryEntry } from "@/lib/landing/real-data";
import { Reveal, SectionMarker } from "../primitives";
import { MemoryFlow } from "../memory-flow";
import { useUiText } from "@/components/i18n/use-ui-text"

function MemoryRow({ entry, index }: { entry: RealMemoryEntry; index: number }) {
  const tr = useUiText()

  const changed = entry.usedCount > 0;
  return (
    <Reveal delay={index * 0.08} y={12}>
      <div className="grid grid-cols-1 gap-5 py-7 md:grid-cols-[minmax(0,1fr)_minmax(0,15rem)] md:gap-10">
        {/* 左:知识条目 */}
        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[0.75rem] text-ink-3">
              {tr(entry.kind)}
            </span>
            <span className="num font-mono text-[0.75rem] text-ink-3">
              {entry.revisionId}
            </span>
          </div>
          <p className="mt-3 text-[1.0625rem] leading-relaxed text-ink-2">
            {tr(entry.text)}
          </p>

          {entry.chain.length > 0 && (
            <div className="num mt-3.5 flex flex-wrap items-center gap-2">
              <span className="text-[0.75rem] text-ink-3">{tr("证据链")}</span>
              {entry.chain.map((c) => (
                <span
                  key={c.ev}
                  className="flex items-center gap-1.5 font-mono text-[0.75rem]"
                >
                  <span className="rounded bg-gold/10 px-1.5 py-px text-gold/90">
                    {c.ep}
                  </span>
                  <CornerDownRight className="size-3 text-ink-3" />
                  <span className="rounded bg-pos/10 px-1.5 py-px text-pos">
                    {c.ev}
                  </span>
                </span>
              ))}
            </div>
          )}
        </div>

        {/* 右:真实反向索引(这一段的主张证据) */}
        <div className="md:border-l md:border-[var(--lp-hair)] md:pl-10">
          {changed ? (
            <>
              <div className="flex items-baseline gap-1.5">
                <span className="num text-[2.25rem] font-semibold leading-none text-pos">
                  {entry.usedCount}
                </span>
                <span className="text-[0.875rem] text-ink-3">{tr("次被取回")}</span>
              </div>
              <p className="mt-2 text-[0.8125rem] text-ink-3">{tr("下游回答引用它:")}</p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {entry.usedBy.map((u) => (
                  <span
                    key={`${u.questionId}-${u.seq}`}
                    className="num inline-flex items-center gap-1 rounded border border-pos/35 bg-pos/[0.07] px-2 py-0.5 font-mono text-[0.75rem] text-pos"
                  >
                    <Check className="size-3" strokeWidth={2.5} />
                    {u.questionId.slice(0, 12)} · v{u.seq}
                  </span>
                ))}
              </div>
            </>
          ) : (
            <>
              <div className="flex items-baseline gap-1.5">
                <span className="num text-[2.25rem] font-semibold leading-none text-ink-3">
                  0
                </span>
                <span className="text-[0.875rem] text-ink-3">{tr("次被取回")}</span>
              </div>
              <p className="mt-2 text-[0.8125rem] leading-relaxed text-ink-3">
                {tr("已入账,尚无下游回答引用此条。")}
              </p>
            </>
          )}
        </div>
      </div>
    </Reveal>
  );
}

export function Memory() {
  const tr = useUiText()

  return (
    <section id="memory" className="lp-section lp-hair-b relative">
      <div className="lp-container">
        <Reveal>
          <SectionMarker label={tr("记忆")} count={tr("{n} 条已入账知识", { n: MEMORY_ENTRIES.length })} />
          <h2 className="lp-h2 mt-8 max-w-[22ch] text-ink">
            {tr("它不是记住了什么,")}
            <br className="hidden sm:block" />
            {tr("是因为记得而改变了什么。")}
          </h2>
          <p className="lp-lead lp-measure mt-5">
            {tr("每条知识右侧是真实的反向索引:它被哪些后续回答引用过。 「N 次被取回」不是比喻,是可数的引用次数。")}
          </p>
        </Reveal>

        {/* 回流可视化:知识 → 取回 → 下一次回答(真实反向索引画成流线) */}
        <MemoryFlow />

        <div className="mt-10">
          {MEMORY_ENTRIES.map((e, i) => (
            <div
              key={e.revisionId}
              className={i > 0 ? "border-t border-[var(--lp-hair)]" : ""}
            >
              <MemoryRow entry={e} index={i} />
            </div>
          ))}
        </div>

        <Reveal delay={0.26}>
          <p className="mt-8 max-w-[24ch] border-t border-[var(--lp-hair)] pt-10 text-[1.5rem] leading-relaxed text-ink-3 sm:max-w-[28ch]">
            {tr("把历史存起来,Git 和 Obsidian 也能做到。")}
            <span className="font-medium text-memory">
              {tr("被系统自动取回、并因此改变下一次判断")}
            </span>
            {tr(",才是分界。")}
          </p>
        </Reveal>
      </div>
    </section>
  );
}

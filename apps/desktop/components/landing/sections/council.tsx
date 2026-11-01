"use client";

/**
 * ② 调查会(COUNCIL)—— AI 自主研究,全页第二重的真实证据。
 *
 * 表达的唯一命题:"一个问题,三个 AI 视角各自独立去查,当场吵架,主持人裁决。"
 *
 * 三个视角不是三张一样的卡:材料纪律不同(经验派只信锚定的证据 / 记录派
 * 只看未锚定的缝隙 / 挑战派是魔鬼代言人),所以行为可见地不同 ——
 * 记录派引用 0 条(纪律使然),挑战派反过来指控自己人。
 *
 * 逐字取自真实事件(kind='investigate_perspective')。
 * 诚实边界:只展示"调查会本身";不展示"→ 人拍板 → 结案"闭环
 * (该通路当前走不通,见 lib/landing/real-data.ts 顶部注释)。
 */

import { Microscope, FileSearch, Swords, ChevronRight } from "lucide-react";
import { COUNCIL, type CouncilPerspective } from "@/lib/landing/real-data";
import { Reveal, SectionMarker } from "../primitives";
import { CouncilOrbit } from "../council-orbit";
import { useUiText } from "@/components/i18n/use-ui-text"

const PERSPECTIVE_STYLE: Record<
  CouncilPerspective["key"],
  { icon: typeof Microscope; accent: string; spine: string }
> = {
  empiricist: {
    icon: Microscope,
    accent: "text-research",
    spine: "bg-research/70",
  },
  recordist: {
    icon: FileSearch,
    accent: "text-memory",
    spine: "bg-memory/70",
  },
  challenger: {
    icon: Swords,
    accent: "text-neg",
    spine: "bg-neg/70",
  },
};

/** 一个视角 = 账本里的一行,不是一张卡。
 *  三视角是同一场调查的三个发言,所以读作一份连续笔录:左侧一条语义色脊线
 *  区分身份,右侧逐字发言。哪一行引用 0 条是纪律使然(记录派),如实写出来 ——
 *  它和引用 8 条的挑战派是同一张纸上的两行,不是两张可以并排比的卡。 */
function PerspectiveRow({
  p,
  index,
}: {
  p: CouncilPerspective;
  index: number;
}) {
  const tr = useUiText()

  const s = PERSPECTIVE_STYLE[p.key];
  const Icon = s.icon;
  return (
    <Reveal delay={index * 0.08} y={12}>
      <div className="relative">
        <span
          aria-hidden
          className={`absolute inset-y-0 left-0 w-[2px] ${s.spine}`}
        />
        <div className="grid grid-cols-1 gap-4 px-5 py-5 sm:px-6 lg:grid-cols-[minmax(0,15rem)_minmax(0,1fr)] lg:gap-8 lg:py-6">
          {/* 视角身份 */}
          <div>
            <div className="flex items-center gap-2.5">
              <Icon className={`size-4 shrink-0 ${s.accent}`} strokeWidth={1.9} />
              <span className="text-[1rem] font-semibold text-ink">
                {tr(p.label)}
              </span>
            </div>
            <p className="lp-meta mt-1.5">{tr(p.discipline)}</p>
            <div
              className={`num mt-3 font-mono text-[0.75rem] ${
                p.citedIds.length === 0 ? "text-ink-3" : s.accent
              }`}
            >
              {tr("引用")} {p.citedIds.length} {tr("条")}
            </div>
          </div>

          {/* 逐字发言 */}
          <div className="min-w-0">
            <blockquote className="text-[0.9375rem] leading-relaxed text-ink-2">
              {tr(p.quote)}
            </blockquote>
            <div className="num lp-cite-chips mt-3 flex flex-wrap gap-1.5">
              {p.citedIds.length === 0 ? (
                <span className="text-[0.75rem] text-ink-3">
                  {tr("按材料纪律,本轮无可引用证据")}
                </span>
              ) : (
                p.citedIds.map((id) => (
                  <span
                    key={id}
                    className="rounded border border-[var(--lp-hair)] px-1.5 py-px font-mono text-[0.75rem] text-ink-3"
                  >
                    {id}
                  </span>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </Reveal>
  );
}

export function Council() {
  const tr = useUiText()

  const c = COUNCIL;
  return (
    <section id="council" className="lp-section-tight lp-hair-b relative">
      <div className="lp-container">
        <Reveal>
          <SectionMarker
            label={tr("调查会")}
            count={tr("这一机制已真实跑过 {n} 次视角事件", { n: c.eventsRun })}
          />
          <h2 className="lp-h2 mt-8 max-w-[24ch] text-ink">
            {tr("AI 自己去查,")}
            <br className="hidden sm:block" />
            {tr("而且会当场反驳自己人。")}
          </h2>
          <p className="lp-lead lp-measure mt-5">
            {tr("一个问题交给三个视角:各自独立召回材料、各自独立分析,只有主持人能落正式回答。 下面这次调查逐字来自真实事件流。")}
          </p>
        </Reveal>

        {/* 被调查的问题 */}
        <Reveal delay={0.08}>
          <div className="mt-12 flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
            <span className="text-[0.8125rem] text-ink-3">{tr("你的问题")}</span>
            <span className="text-[1.25rem] font-semibold text-ink">
              {tr(c.questionTitle)}
            </span>
            <span className="num ml-auto font-mono text-[0.8125rem] text-ink-3">
              {c.questionId} · {c.openedAt}
            </span>
          </div>
        </Reveal>

        {/* 轨道全景:中心问题 + 三视角卫星(真实引用计数) */}
        <Reveal delay={0.12} className="mt-10 mb-4">
          <CouncilOrbit />
        </Reveal>

        {/* 三视角:一份连续笔录(不是三张并列的卡) */}
        <div className="mt-10 border-y border-[var(--lp-hair)]">
          {c.perspectives.map((p, i) => (
            <div
              key={p.key}
              className={i > 0 ? "border-t border-[var(--lp-hair)]" : ""}
            >
              <PerspectiveRow p={p} index={i} />
            </div>
          ))}
        </div>

        {/* 分歧与裁决 —— 主持人做的事 */}
        <Reveal delay={0.18}>
          <div className="mt-6 rounded-lg border border-gold/25 bg-surface-2 p-6">
            <div className="flex items-center gap-2.5">
              <span className="text-[0.9375rem] font-semibold text-ink">
                {tr("主持人逐条裁决")}
              </span>
              <span className="leading-none text-ink-3">·</span>
              <span className="text-[0.8125rem] text-ink-3">
                {tr("三视角的分歧被一条条裁定,不是投票")}
              </span>
            </div>
            <ul className="mt-5 flex flex-col gap-4">
              {c.rulings.map((r) => (
                <li key={r.dispute} className="flex flex-col gap-1.5 sm:flex-row sm:gap-4">
                  <span className="shrink-0 text-[0.875rem] text-ink-3 sm:w-56">
                    {tr(r.dispute)}
                  </span>
                  <span className="flex items-start gap-2 text-[0.9375rem] leading-relaxed text-ink-2">
                    <ChevronRight className="mt-0.5 size-4 shrink-0 text-gold/50" />
                    {tr(r.verdict)}
                  </span>
                </li>
              ))}
            </ul>

            <div className="mt-6 border-t border-[var(--lp-hair)] pt-5">
              <div className="num flex items-center gap-2 font-mono text-[0.8125rem] text-gold">
                <span className="rounded bg-primary px-1.5 py-0.5 font-semibold text-primary-foreground">
                  v1
                </span>
                {tr("正式回答")}
              </div>
              <p className="mt-3 text-[0.9375rem] leading-relaxed text-ink-2">
                {tr(c.revision)}
              </p>
            </div>
          </div>
        </Reveal>

        {/* 诚实边界:只到"回答",不画闭环 */}
        <Reveal delay={0.26}>
          <p className="lp-meta mt-5">
            {tr("注意最后那句")}
            <span className="text-ink">{tr("「任何前向含义均不可答」")}</span>
            {tr(":账本里不存在 BTC 1d 短视界的已验证证据,它就拒绝外推。")}
            <span className="text-ink-3">
              {" "}
              {tr("(此处到此为止:调查会 → 结案的通路尚未打通,不在本页展示。)")}
            </span>
          </p>
        </Reveal>
      </div>
    </section>
  );
}

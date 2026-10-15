"use client";

import { useApiPage } from "@/lib/hooks/use-api-page";
import type { MissionSummary } from "./missions-types";
import type { TodayResponse } from "./today-types";
import { capNeedsYou, eventLine, relTime } from "./today-parts";
import { errorHintText } from "./strategy-parts";
/**
 * TODAY 页 — 注意力分配器(slug "today",/research/today)。
 * 顶条七环研究生命周期 + 四段固定不重排:LAST HANDOFF / NEEDS YOU / SELENE IS WORKING / WORLD CHANGED。
 * 全部字段读 B4 GET /api/v1/research/today 投影,页面零生成物;
 * 拍板是门不是菜单:去 Mission 页完整证据面前决策。
 */

const GOLD = "text-gold";

/** 七环状态条 — 研究生命周期 Question→…→Memory,从 today 投影推导;接不通的环显示 — */
function ChainStrip({ data, memCount }: { data: TodayResponse; memCount: number | null }) {
  const working = data.working ?? [];
  const ny = data.needs_you ?? [];
  const world = data.world_changed ?? [];
  const openCount = ny.length + working.length;
  const rings: { label: string; n: number | null }[] = [
    { label: "问句", n: openCount },
    { label: "使命", n: openCount },
    { label: "调查", n: working.filter((m) => m.phase === "investigation").length },
    { label: "实验", n: working.filter((m) => m.phase === "validation").length },
    { label: "决策", n: ny.length },
    { label: "结果", n: world.filter((m) => m.phase === "resolved").length },
    { label: "记忆", n: memCount },
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 rounded-lg border border-border bg-surface-1 px-3.5 py-2">
      {rings.map((r, i) => (
        <span key={r.label} className="flex items-center gap-1.5">
          {i > 0 && <span className="text-[9px] text-ink-4">→</span>}
          <span className="flex items-baseline gap-1">
            <span className="text-[9px] tracking-[0.08em] text-ink-4">{r.label}</span>
            <span
              className={`num text-[10.5px] font-semibold ${
                r.n == null || r.n === 0 ? "text-ink-4" : "text-ink-2"
              }`}
            >
              {r.n == null ? "—" : r.n}
            </span>
          </span>
        </span>
      ))}
    </div>
  );
}

function SectionHead({
  title,
  hint,
  count,
}: {
  title: string;
  hint?: string;
  count?: string;
}) {
  return (
    <div className="flex items-baseline gap-2 border-b border-border-soft px-3.5 py-2">
      <span className="text-[11px] font-semibold tracking-wider text-ink">{title}</span>
      {hint && <span className="text-[9.5px] text-ink-4">{hint}</span>}
      {count && <span className="num ml-auto text-[9.5px] text-ink-3">{count}</span>}
    </div>
  );
}

function MissionRowLite({ m }: { m: MissionSummary }) {
  const r = relTime(m.updated_at);
  return (
    <div className="flex items-baseline gap-2 border-b border-border-soft py-[5px] text-[10.5px] last:border-b-0">
      <span className="text-ink-2">{m.title}</span>
      <span className="ml-auto shrink-0 text-[9px] text-ink-4" title={r.title}>
        {r.label}
      </span>
    </div>
  );
}

export function TodayView({
  data,
  loading,
  errorStatus,
  memCount,
}: {
  data: TodayResponse | null;
  loading: boolean;
  errorStatus?: number;
  /** 记忆环真值:GET /research/graph/memory 的 knowledge+clues;取不到时 null 显 — */
  memCount?: number | null;
}) {
  if (errorStatus !== undefined) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-ink-3">
        {errorHintText(errorStatus)}
      </div>
    );
  }
  if (loading && !data) {
    return <div className="p-4 text-[11px] text-ink-3">加载中…</div>;
  }
  if (!data) return null;

  const needsYou = data.needs_you ?? [];
  const working = data.working ?? [];
  const world = data.world_changed ?? [];
  const h = data.last_handoff;

  // 安静态塌缩:needs_you 空 → NEEDS YOU 段收成安静文案(空间位置表达语义)
  const quiet = needsYou.length === 0;
  // 容量规则:最多突出 3 条,其余收溢出行
  const { shown: nyShown, overflow: nyOverflow } = capNeedsYou(needsYou);

  const hWhen = h ? relTime(h.occurred_at) : null;
  // detail.note 是人话真值(如 "Approve REVALIDATE — 修正配置回声明意图后重验证");
  // kind 是内部状态机词(phase_transition),不进用户面
  const hNote =
    h && h.detail && typeof h.detail.note === "string" ? (h.detail.note as string) : null;

  return (
    <div className="h-full overflow-y-auto pb-[300px]">
      <div className="mx-auto flex max-w-[1120px] flex-col gap-2.5 px-4 pb-6 pt-3.5">
        {/* topline · 灵魂问句 */}
        <div className="flex items-center gap-2.5 text-[10px] text-ink-3">
          <span className="text-[11px] font-medium text-ink-2">TODAY · 现在什么值得我关注?</span>
        </div>

        {/* 七环状态条 — 研究生命周期计数 */}
        <ChainStrip data={data} memCount={memCount ?? null} />

        {/* ① LAST HANDOFF — 三行微叙事 */}
        <section className="overflow-hidden rounded-lg border border-border bg-surface-1">
          <SectionHead title="LAST HANDOFF" hint="上次交接之后发生了什么" count={hWhen ? `最近一次交接 · ${hWhen.label}` : undefined} />
          {h ? (
            <div className="flex flex-col px-3.5 pb-2.5 pt-1">
              <div className="flex items-baseline gap-2.5 border-b border-border-soft py-1">
                <span className="w-[86px] shrink-0 text-[9px] tracking-[0.1em] text-ink-4">YOU DID</span>
                <span className="text-[11px] leading-relaxed text-ink-2">
                  {hNote ? (
                    <b className="font-semibold text-ink">{hNote}</b>
                  ) : (
                    <span className="text-ink-4">—</span>
                  )}
                </span>
                <span className="num ml-auto shrink-0 text-[9px] text-ink-4" title={hWhen?.title}>
                  {hWhen?.label}
                </span>
              </div>
              <div className="flex items-baseline gap-2.5 py-1">
                <span className="w-[86px] shrink-0 text-[9px] tracking-[0.1em] text-ink-4">CURRENT</span>
                <span className="text-[11px] leading-relaxed text-ink-2">
                  {quiet ? (
                    <span>研究线交接完毕,暂无待你决策事项</span>
                  ) : (
                    <b className="font-semibold text-ink">{needsYou.length} 件新 Mission 待你拍板</b>
                  )}
                </span>
              </div>
            </div>
          ) : (
            <div className="px-3.5 pb-3 pt-1 text-[10.5px] text-ink-4">尚无交接记录 — 一切从今天开始。</div>
          )}
        </section>

        {/* ② NEEDS YOU — 视觉最重(有件时金色负责人介入态) */}
        <section
          className={`overflow-hidden rounded-lg border ${
            quiet
              ? "border-border bg-surface-1"
              : "border-gold/30 bg-[color-mix(in_srgb,var(--color-gold)_4%,var(--color-surface-1))]"
          }`}
        >
          <SectionHead title="NEEDS YOU" count={quiet ? "0 件 · 安静" : `${needsYou.length} 件待拍板`} />
          {quiet ? (
            <div className="flex flex-col gap-0.5 px-3.5 pb-3 pt-2">
              <span className="text-[11px] text-ink-3">现在不需要你 — 无待拍板事项。</span>
              {working.length > 0 && (
                <a
                  href={`/research/missions?id=${working[0].id}`}
                  className="w-fit text-[10px] text-ink-4 underline decoration-border-soft underline-offset-2 transition-colors hover:text-ink-2"
                >
                  Selene 正在调查 {working[0].title} — 去看看进展 →
                </a>
              )}
            </div>
          ) : (
            <div className="flex flex-col gap-2.5 px-3.5 py-3">
              {nyShown.map((m) => (
                <NeedsYouCard key={m.id} m={m} />
              ))}
              {nyOverflow > 0 && (
                <div className="text-[9.5px] text-ink-4">
                  其余 {nyOverflow} 条在研究线 Portfolio 查看。
                </div>
              )}
            </div>
          )}
        </section>

        {/* ③ SELENE IS WORKING — 诚实空/进度行,禁百分比 */}
        <section className="overflow-hidden rounded-lg border border-border bg-surface-1">
          <SectionHead title="SELENE IS WORKING" count={`${working.length} 项进行中`} />
          <div className="flex flex-col gap-0.5 px-3.5 pb-3 pt-2">
            {working.length === 0 ? (
              <>
                <span className="text-[11px] text-ink-3">办公室很安静,因为一切在等你。</span>
                {needsYou.length > 0 && (
                  <span className="text-[9.5px] leading-relaxed text-ink-4">{needsYou.length} 项调查在等你的决定。</span>
                )}
              </>
            ) : (
              working.map((m) => <MissionRowLite key={m.id} m={m} />)
            )}
          </div>
        </section>

        {/* ④ WORLD CHANGED — 视觉最轻 */}
        <section className="overflow-hidden rounded-lg border border-border bg-surface-1">
          <SectionHead title="WORLD CHANGED" count={`${world.length} 行 · 最近变化`} />
          <div className="flex flex-col px-3.5 pb-2.5 pt-1">
            {world.length === 0 ? (
              <span className="py-1 text-[10.5px] text-ink-4">世界安静 — 没有与你的研究线相关的变化。</span>
            ) : (
              world.map((m) => {
                const r = relTime(m.updated_at);
                return (
                  <div
                    key={m.id}
                    className="flex items-baseline gap-2 border-b border-border-soft py-[5px] text-[10.5px] last:border-b-0"
                  >
                    <span className="shrink-0 rounded bg-surface-4 px-1 py-px text-[9px] text-ink-3">{m.phase}</span>
                    <span className="text-ink-2">{eventLine(m)}</span>
                    <span className="num ml-auto shrink-0 text-[9px] text-ink-4" title={r.title}>
                      {r.label}
                    </span>
                  </div>
                );
              })
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

/** E1 接管卡 — 第一层=工作事件(问题→状态→结论→Review),第二层=细节 */
function NeedsYouCard({ m }: { m: MissionSummary }) {
  const r = relTime(m.updated_at);
  return (
    <div className="rounded-lg border border-gold/26 bg-surface-2 p-3.5">
      {/* 事件层:第一眼 */}
      <div className="flex items-center gap-2.5">
        <span className="h-2 w-2 shrink-0 rounded-sm bg-gold shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-gold)_14%,transparent)]" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 tracking-[-0.01em]">
            <span className={`text-[13.5px] font-semibold ${GOLD}`}>{m.title}</span>
            <span className="rounded border border-gold/28 bg-gold/12 px-1.5 py-px text-[9px] font-medium text-gold">
              待拍板
            </span>
          </div>
          <div className="mt-0.5 text-[11px] leading-relaxed text-ink-2">
            {m.question} — 等你裁决
          </div>
        </div>
        <a
          href={`/research/missions?id=${m.id}`}
          className="shrink-0 rounded-md bg-gold px-3 py-[5px] text-[10.5px] font-semibold text-[#161006] transition-colors hover:bg-[color-mix(in_srgb,var(--color-gold)_88%,#fff)]"
        >
          去 Mission 页拍板 →
        </a>
      </div>

      {/* 细节层:第二眼扫读 */}
      <div className="mt-2.5 border-t border-border-soft pt-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded bg-surface-4 px-1.5 py-px text-[9px] text-ink-3">{m.subject_type}</span>
          <span className="num text-[9.5px] text-ink-4">{m.id}</span>
          <span className="num ml-auto text-[9px] text-ink-4" title={r.title}>
            {r.label}
          </span>
        </div>
      </div>
    </div>
  );
}

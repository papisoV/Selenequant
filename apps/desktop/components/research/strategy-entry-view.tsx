"use client";

import { ArrowRight } from "lucide-react";
import { useRouter } from "next/navigation";
import type { MissionSummary } from "./missions-types";
import { PhaseBadge } from "./missions-parts";
import { ErrorHint } from "./strategy-parts";

/**
 * STRATEGY 入口 — 冷启动诚实态:驾驶舱由 Mission subject(strategy)解锁。
 * 数据来自 GET /research/missions(subject_type=strategy 过滤)。
 */
export function StrategyEntryView({
  missions,
  loading,
  errorStatus,
}: {
  missions: MissionSummary[] | null;
  loading: boolean;
  errorStatus?: number;
}) {
  const router = useRouter();
  const strategyMissions = (missions ?? []).filter((m) => m.subject_type === "strategy");

  if (errorStatus !== undefined && missions === null) {
    return (
      <div className="mx-auto flex w-full max-w-[820px] flex-col items-start gap-2 p-4">
        <h2 className="text-[15px] font-semibold text-ink">STRATEGY · 策略驾驶舱</h2>
        <ErrorHint status={errorStatus} />
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-[820px] flex-col gap-3 p-4">
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 rounded-full bg-accent animate-breathe motion-reduce:animate-none" />
        <h2 className="text-[15px] font-semibold text-ink">STRATEGY · 策略驾驶舱</h2>
        {missions !== null && (
          <span className="num ml-auto text-[11px] text-ink-3">{strategyMissions.length} 条策略线索</span>
        )}
      </div>
      <p className="text-[11px] leading-relaxed text-ink-3">
        驾驶舱围绕有调查记录的策略组织:出过回测异常、开过调查、有 episodes 可回放。
        全部策略(含新建)在策略工厂页。
      </p>

      {loading && missions === null ? (
        <div className="flex h-48 items-center justify-center text-[11px] text-ink-3">加载中…</div>
      ) : missions !== null && strategyMissions.length === 0 ? (
        <div className="rounded-lg border border-border-soft bg-surface-2/50 p-4 text-[11px] leading-relaxed text-ink-3">
          还没有策略进入研究视野 — 策略出异常回测或被 Mission 开案后,会出现在这里。
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {strategyMissions.map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => router.push(`/research/strategy-cockpit?id=${encodeURIComponent(m.subject_id)}`)}
              className="group rounded-lg border border-border bg-surface-2 p-3 text-left transition-colors hover:border-accent/50"
            >
              <div className="flex items-center gap-2">
                <PhaseBadge phase={m.phase} />
                <span className="num text-[10px] text-ink-3">{m.subject_id}</span>
                <ArrowRight className="ml-auto h-3 w-3 text-ink-4 transition-colors group-hover:text-accent" />
              </div>
              <h3 className="mt-1.5 text-[13px] font-semibold leading-snug text-ink">{m.title}</h3>
              <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-ink-2">{m.question}</p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

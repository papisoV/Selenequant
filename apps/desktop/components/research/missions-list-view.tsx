"use client";

import { ArrowRight, ChevronDown, ChevronRight } from "lucide-react";
import { useRouter } from "next/navigation";
import { Fragment, useState } from "react";
import type { MissionSummary } from "./missions-types";
import { PhaseBadge } from "./missions-parts";
import { ErrorHint } from "./strategy-parts";

function fmtDT(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

/** 行渲染 — 组内单行与独立行共用 */
function MissionRow({
  m,
  onOpen,
}: {
  m: MissionSummary;
  onOpen: (id: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(m.id)}
      className={`group rounded-lg border bg-surface-2 p-3 text-left transition-colors hover:border-accent/50 ${
        m.phase === "needs_input" ? "border-accent/40" : "border-border"
      }`}
    >
      <div className="flex items-center gap-2">
        <PhaseBadge phase={m.phase} />
        <span className="num text-[10px] text-ink-3">{m.id}</span>
        <span className="num ml-auto text-[10px] text-ink-4">{fmtDT(m.updated_at)}</span>
        <ArrowRight className="h-3 w-3 text-ink-4 transition-colors group-hover:text-accent" />
      </div>
      <h3 className="mt-1.5 text-[13px] font-semibold leading-snug text-ink">{m.title}</h3>
      <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-ink-2">{m.question}</p>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-ink-3">
        <span className="rounded bg-surface-3 px-1.5 py-px text-ink-2">{m.subject_type}</span>
        <span className="num">{m.subject_id}</span>
      </div>
    </button>
  );
}

const GROUP_FOLD_MIN = 3;

/**
 * Mission 列表 — Daily Brief 雏形:"打开第一眼看到团队研究了什么"。
 * 数据来自 GET /research/missions(投影 = 行内字段,无 context/outcome)。
 * 同 title ≥3 行(演示种子批量灌库的形态)折叠为组头 ×N,点击展开;单行 title 直接显示。
 */
export function MissionsListView({
  missions,
  loading,
  errorStatus,
}: {
  missions: MissionSummary[] | null;
  loading: boolean;
  errorStatus?: number;
}) {
  const router = useRouter();
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set());
  const awaiting = (missions ?? []).filter((m) => m.phase === "needs_input").length;

  // 按首次出现顺序分组,保持后端排序(最新在前)
  const groups: MissionSummary[][] = [];
  {
    const byTitle = new Map<string, MissionSummary[]>();
    for (const m of missions ?? []) {
      const arr = byTitle.get(m.title);
      if (arr) arr.push(m);
      else {
        const fresh: MissionSummary[] = [m];
        byTitle.set(m.title, fresh);
        groups.push(fresh);
      }
    }
  }

  const toggleGroup = (title: string) => {
    setOpenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(title)) next.delete(title);
      else next.add(title);
      return next;
    });
  };

  const openDetail = (id: string) =>
    router.push(`/research/missions?id=${encodeURIComponent(id)}`);

  if (errorStatus !== undefined && missions === null) {
    return (
      <div className="mx-auto flex w-full max-w-[820px] flex-col items-start gap-2 p-4">
        <h2 className="text-[15px] font-semibold text-ink">研究线 · Mission 一览</h2>
        <ErrorHint status={errorStatus} />
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-[820px] flex-col gap-3 p-4">
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 rounded-full bg-accent animate-breathe motion-reduce:animate-none" />
        <h2 className="text-[15px] font-semibold text-ink">研究线 · Mission 一览</h2>
        {missions !== null && (
          <span className="num ml-auto text-[11px] text-ink-3">
            {missions.length} 条 · {awaiting} 待拍板
          </span>
        )}
      </div>

      {loading && missions === null ? (
        <div className="flex h-48 items-center justify-center text-[11px] text-ink-3">加载中…</div>
      ) : missions !== null && missions.length === 0 ? (
        <div className="rounded-lg border border-border-soft bg-surface-2/50 p-4 text-[11px] leading-relaxed text-ink-3">
          暂无 Mission。调查由触发源(策略异常 / 市场事件)自动开案,拍板后流入验证与执行。
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {groups.map((g) => {
            const first = g[0];
            if (g.length < GROUP_FOLD_MIN) {
              return (
                <Fragment key={first.title}>
                  {g.map((m) => (
                    <MissionRow key={m.id} m={m} onOpen={openDetail} />
                  ))}
                </Fragment>
              );
            }
            const open = openGroups.has(first.title);
            const newest = g[0]; // 列表最新在前
            return (
              <div key={first.title} className="flex flex-col gap-2">
                <button
                  type="button"
                  onClick={() => toggleGroup(first.title)}
                  className="group flex items-center gap-2 rounded-lg border border-border bg-surface-2/60 px-3 py-2.5 text-left transition-colors hover:border-accent/50"
                >
                  {open ? (
                    <ChevronDown className="h-3.5 w-3.5 shrink-0 text-ink-4" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-4" />
                  )}
                  <PhaseBadge phase={newest.phase} />
                  <span className="text-[12.5px] font-semibold text-ink">{first.title}</span>
                  <span className="num rounded bg-surface-3 px-1.5 py-px text-[10px] text-ink-3">
                    ×{g.length}
                  </span>
                  <span className="num ml-auto text-[10px] text-ink-4">
                    最近 {fmtDT(newest.updated_at)}
                  </span>
                </button>
                {open &&
                  g.map((m) => <MissionRow key={m.id} m={m} onOpen={openDetail} />)}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

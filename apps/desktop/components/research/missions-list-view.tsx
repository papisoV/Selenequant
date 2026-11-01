"use client";

import { ArrowRight, ChevronDown, ChevronRight } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { SkeletonRows } from "@/components/shared/skeleton";
import type { MissionSummary } from "./missions-types";
import { PhaseBadge } from "./missions-parts";
import { ErrorHint } from "./strategy-parts";
import { useUiText } from "@/components/i18n/use-ui-text"
import { formatDateTimeShort } from "@/lib/format"
import { useUrlFilter } from "@/lib/hooks/use-url-filter"

/** 行渲染 — 组内单行与独立行共用 */
function MissionRow({
  m,
  onOpen,
}: {
  m: MissionSummary;
  onOpen: (id: string) => void;
}) {
  const tr = useUiText();
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
        <span className="num ml-auto text-[10px] text-ink-4">{formatDateTimeShort(m.updated_at)}</span>
        <ArrowRight className="h-3 w-3 text-ink-4 transition-colors group-hover:text-accent" />
      </div>
      <h2 className="mt-1.5 text-[13px] font-semibold leading-snug text-ink">{m.title}</h2>
      <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-ink-2">{m.question}</p>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-ink-3">
        <span className="rounded bg-surface-3 px-1.5 py-px text-ink-2">{m.subject_type}</span>
        <span className="num">{m.subject_id}</span>
        {(m.duplicates ?? 0) > 0 && (
          <span
            data-testid="mission-duplicates"
            className="rounded border border-border bg-surface-3 px-1.5 py-px text-ink-3"
            title={tr("同一标的下的同一问句被重复开案的条数")}
          >
            {tr("同问句 ×{n}", { n: (m.duplicates ?? 0) + 1 })}
          </span>
        )}
      </div>
    </button>
  );
}

type PhaseFilter = "active" | "investigation" | "needs_input" | "validation" | "resolved" | "parked" | "all";

const PHASE_TABS: { key: PhaseFilter; label: string }[] = [
  { key: "active", label: "进行中" },
  { key: "investigation", label: "调查中" },
  { key: "needs_input", label: "待拍板" },
  { key: "validation", label: "验证中" },
  { key: "resolved", label: "已结案" },
  { key: "parked", label: "已停放" },
  { key: "all", label: "全部" },
];

const PHASE_KEYS = PHASE_TABS.map((t) => t.key);

function matchesPhase(m: MissionSummary, f: PhaseFilter): boolean {
  if (f === "all") return true;
  // 「进行中」= 除 parked/resolved 外的三态。默认档位:实测库里 38 条里
  // 27 条是 parked,全量平铺就是一堵墙,真正在跑的会被淹掉。
  if (f === "active") return m.phase !== "parked" && m.phase !== "resolved";
  return m.phase === f;
}

/**
 * Mission 列表 — Daily Brief 雏形:"打开第一眼看到团队研究了什么"。
 * 数据来自 GET /research/missions(投影 = 行内字段,无 context/outcome)。
 *
 * 管理面(2026-10-25):
 *  - 默认只看「进行中」(investigation/needs_input/validation),parked 与
 *    resolved 收进各自的 chip;
 *  - 搜索匹配 title + subject_id + question;
 *  - 重复开案改用后端 `duplicates`/`duplicate_ids`(按标的+问句折叠)。
 *    前端原先按 title 分组,对这批数据完全失明:同一个问句被不同来源起了
 *    不同的名字("X PA 心跳诊断" / "PA diagnosis X 1h"),永远凑不齐 3 条。
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
  const tr = useUiText()

  const router = useRouter();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // 筛选进 URL:刷新/分享/前进后退都回到同一屏。键名避开详情页的 `id`。
  const [phaseFilter, setPhaseFilter] = useUrlFilter<PhaseFilter>(
    "phase",
    "active",
    PHASE_KEYS,
  );
  const [search, setSearch] = useUrlFilter<string>("search", "");

  const all = useMemo(() => missions ?? [], [missions]);
  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return all.filter(
      (m) =>
        matchesPhase(m, phaseFilter) &&
        (!needle ||
          m.title.toLowerCase().includes(needle) ||
          m.subject_id.toLowerCase().includes(needle) ||
          m.question.toLowerCase().includes(needle)),
    );
  }, [all, phaseFilter, search]);

  const awaiting = visible.filter((m) => m.phase === "needs_input").length;
  const parkedCount = all.filter((m) => m.phase === "parked").length;
  const resolvedCount = all.filter((m) => m.phase === "resolved").length;

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const openDetail = (id: string) =>
    router.push(`/research/missions?id=${encodeURIComponent(id)}`);

  if (errorStatus !== undefined && missions === null) {
    return (
      <div className="mx-auto flex w-full max-w-[820px] flex-col items-start gap-2 p-4">
        <h1 className="text-[15px] font-semibold text-ink">{tr("研究线 · Mission 一览")}</h1>
        <ErrorHint status={errorStatus} />
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-[820px] flex-col gap-3 p-4">
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 rounded-full bg-accent animate-breathe motion-reduce:animate-none" />
        <h1 className="text-[15px] font-semibold text-ink">{tr("研究线 · Mission 一览")}</h1>
        {missions !== null && (
          <span className="num ml-auto text-[11px] text-ink-3">
            {visible.length} / {all.length} {tr("条")}{awaiting > 0 ? ` · ${awaiting} ${tr("待拍板")}` : ""}
          </span>
        )}
      </div>

      {missions !== null && all.length > 0 && (
        <div
          data-testid="mission-filters"
          className="flex flex-col gap-2 rounded-lg border border-border bg-surface-1 px-3 py-2"
        >
          <div className="flex flex-wrap items-center gap-1">
            {PHASE_TABS.map((t) => {
              const count =
                t.key === "all"
                  ? all.length
                  : t.key === "active"
                    ? all.filter((m) => matchesPhase(m, "active")).length
                    : all.filter((m) => m.phase === t.key).length;
              return (
                <button
                  key={t.key}
                  type="button"
                  data-testid={`mission-phase-${t.key}`}
                  onClick={() => setPhaseFilter(t.key)}
                  className={`rounded px-2 py-1 text-[10.5px] ${
                    phaseFilter === t.key
                      ? "bg-accent text-accent-foreground"
                      : "text-ink-3 hover:text-ink"
                  }`}
                >
                  {tr(t.label)}
                  <span className="num ml-1 opacity-70">{count}</span>
                </button>
              );
            })}
          </div>
          <div className="flex items-center gap-2">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={tr("按标题 / 标的 / 问句搜索")}
              data-testid="mission-search"
              className="w-full rounded border border-border bg-surface-2 px-2 py-1 text-[11px] text-ink placeholder:text-ink-4 focus:border-accent/50 focus:outline-none"
            />
            {(phaseFilter !== "active" || search.trim() !== "") && (
              <button
                type="button"
                data-testid="mission-clear-filters"
                onClick={() => {
                  setPhaseFilter("active");
                  setSearch("");
                }}
                className="shrink-0 text-[10px] text-ink-4 hover:text-accent"
              >
                {tr("清除")}
              </button>
            )}
          </div>
          {parkedCount > 0 && phaseFilter !== "parked" && (
            <p className="text-[9px] text-ink-4">
              {tr("另有 {n} 条已停放", { n: parkedCount })}
              {resolvedCount > 0
                ? tr("、{n} 条已结案", { n: resolvedCount })
                : ""}
              {tr("未显示")}
            </p>
          )}
        </div>
      )}

      {loading && missions === null ? (
        <SkeletonRows rows={6} />
      ) : missions !== null && all.length === 0 ? (
        <div className="rounded-lg border border-border-soft bg-surface-2/50 p-4 text-[11px] leading-relaxed text-ink-3">
          {tr("暂无 Mission。调查由触发源(策略异常 / 市场事件)自动开案,拍板后流入验证与执行。")}
        </div>
      ) : visible.length === 0 ? (
        <div
          data-testid="mission-no-match"
          className="rounded-lg border border-border-soft bg-surface-2/50 p-4 text-[11px] text-ink-3"
        >
          {tr("没有符合筛选的 Mission。")}
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {visible.map((m) => {
            const dupIds = m.duplicate_ids ?? [];
            if (dupIds.length === 0) {
              return <MissionRow key={m.id} m={m} onOpen={openDetail} />;
            }
            const open = expanded.has(m.id);
            return (
              <div key={m.id} className="flex flex-col gap-2">
                <div className="relative">
                  <MissionRow m={m} onOpen={openDetail} />
                  <button
                    type="button"
                    onClick={() => toggle(m.id)}
                    data-testid="mission-duplicates-toggle"
                    className="absolute bottom-2 right-2 flex items-center gap-1 rounded border border-border bg-surface-1 px-1.5 py-0.5 text-[9px] text-ink-3 hover:border-accent/50 hover:text-accent"
                  >
                    {open ? (
                      <ChevronDown className="h-3 w-3" />
                    ) : (
                      <ChevronRight className="h-3 w-3" />
                    )}
                    {tr("另外 {n} 条同问句", { n: dupIds.length })}
                  </button>
                </div>
                {open && (
                  <ul
                    data-testid="mission-duplicate-ids"
                    className="ml-3 space-y-1 border-l border-border-soft pl-3"
                  >
                    {dupIds.map((id) => (
                      <li key={id}>
                        <button
                          type="button"
                          onClick={() => openDetail(id)}
                          className="num text-[10px] text-ink-3 hover:text-accent"
                        >
                          {id}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

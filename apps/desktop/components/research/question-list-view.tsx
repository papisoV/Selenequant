"use client";

/**
 * Question 面默认态(无 ?q)—— 问题列表:open 在前,最近结案折叠。
 * 复访入口 + 立问入口;诚实空态(没有就显示为空,不造假内容)。
 *
 * 管理面(2026-10-25):筛选(状态/来源/文本)+ 多选批量停放。
 * 起因是实测口径 —— 51 个问题里有 27 个是链路中段遗留,逐个点开再停放
 * 要 27 次往返;批量操作把它变成一次。**串行**执行且逐条报错,不做整体
 * 回滚:中途失败时已成功的那几条是真的停放了,回滚反而会骗人。
 */

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronRight } from "lucide-react";

import { SkeletonRows } from "@/components/shared/skeleton";

import {
  closeQuestion,
  listDueQuestions,
  listQuestions,
  reopenQuestion,
  type DueQuestion,
  type Question,
} from "@/lib/api/research-graph";
import { useUiText } from "@/components/i18n/use-ui-text"
import { formatMonthDay } from "@/lib/format"
import { useUrlFilter } from "@/lib/hooks/use-url-filter"

const TERMINAL_LABEL: Record<string, string> = {
  sufficiently_answered: "已查清楚",
  falsified: "已被证伪",
  parked: "先放一放",
  superseded: "已换问法",
};

type StatusFilter = "active" | "open" | "parked" | "terminal" | "all";
type SourceFilter = "all" | "selene" | "director" | "user";

const STATUS_TABS: { key: StatusFilter; label: string }[] = [
  { key: "active", label: "未结案" },
  { key: "open", label: "调查中" },
  { key: "parked", label: "已停放" },
  { key: "terminal", label: "已结案" },
  { key: "all", label: "全部" },
];

const SOURCE_TABS: { key: SourceFilter; label: string }[] = [
  { key: "all", label: "全部来源" },
  { key: "selene", label: "Selene 提议" },
  { key: "user", label: "你写的" },
  { key: "director", label: "系统立案" },
];

// URL 兜底:地址栏被手改成非法值时回落到默认档,而不是渲染空列表。
const STATUS_KEYS = STATUS_TABS.map((t) => t.key);
const SOURCE_KEYS = SOURCE_TABS.map((t) => t.key);

function matchesStatus(q: Question, filter: StatusFilter): boolean {
  switch (filter) {
    case "active":
      return q.status === "open" || q.status === "parked";
    case "open":
      return q.status === "open";
    case "parked":
      return q.status === "parked";
    case "terminal":
      return q.status !== "open" && q.status !== "parked";
    case "all":
      return true;
  }
}

/**
 * 只有 open / parked 可以再停放/再打开(与后端 ``_REVISIT_ALLOWED_STATUSES``
 * 同口径)。终态问题不在管理范围内 —— 它们的历史不该被批量操作改写。
 */
function isManageable(q: Question): boolean {
  return q.status === "open" || q.status === "parked";
}

function QuestionRow({
  q,
  badge,
  revisitBadge,
  selected,
  onToggleSelect,
}: {
  q: Question;
  badge?: string;
  revisitBadge?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: string, next: boolean) => void;
}) {
  const manageable = isManageable(q) && onToggleSelect !== undefined;
  const tr = useUiText();
  return (
    <li className="flex items-start gap-2" data-testid="question-row-wrap">
      {manageable && (
        <input
          type="checkbox"
          checked={!!selected}
          onChange={(e) => onToggleSelect(q.id, e.target.checked)}
          aria-label={tr("选择「{title}」", { title: q.title })}
          data-testid="question-select"
          className="mt-4 h-3.5 w-3.5 shrink-0 accent-accent"
        />
      )}
      <Link
        href={`/research/questions?q=${encodeURIComponent(q.id)}`}
        className="block min-w-0 flex-1 rounded-lg border border-border bg-surface-2 px-4 py-3 hover:border-accent/40"
        data-testid="question-row"
      >
        <div className="flex items-center gap-2">
          <span className="text-xs text-ink">{q.title}</span>
          {revisitBadge && (
            <span
              data-testid="revisit-due-badge"
              className="rounded border border-accent/40 bg-accent/5 px-2 py-0.5 text-[9px] text-accent"
            >
              {tr("该复访")}
            </span>
          )}
          {badge && (
            <span className="rounded border border-accent/40 bg-accent/5 px-2 py-0.5 text-[9px] text-accent">
              {badge}
            </span>
          )}
          <span className="ml-auto font-mono text-[10px] text-ink-4">
            {formatMonthDay(q.opened_at)}
          </span>
        </div>
        <div className="mt-1 flex items-center gap-2 text-[10px] text-ink-3">
          <span>v{q.revision_count}</span>
          <span>·</span>
          <span>{formatMonthDay(q.updated_at)} {tr("更新")}</span>
          {q.status === "parked" && (
            <span className="rounded border border-border bg-surface-3 px-1.5 py-0.5 text-[9px]">
              {tr("已停放")}
            </span>
          )}
          <span className="ml-auto text-[9px] text-ink-4">
            {q.created_by === "selene" ? tr("Selene 提议") : q.created_by === "user" ? tr("你写的") : tr("系统立案")}
          </span>
        </div>
      </Link>
    </li>
  );
}

export function QuestionListView() {
  const tr = useUiText()

  const [questions, setQuestions] = useState<Question[] | null>(null);
  const [due, setDue] = useState<DueQuestion[] | null>(null);
  const [dueTotal, setDueTotal] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showClosed, setShowClosed] = useState(false);

  // 筛选状态进 URL:刷新、分享链接、前进后退都还原同一屏。
  // 搜索键用 `search` 而不是 `q` —— 本页上层 QuestionSurface 的 `q` 是
  // 「打开哪个问题的详情」,同页共用同名键会互相踩。
  const [statusFilter, setStatusFilter] = useUrlFilter<StatusFilter>(
    "status",
    "active",
    STATUS_KEYS,
  );
  const [sourceFilter, setSourceFilter] = useUrlFilter<SourceFilter>(
    "source",
    "all",
    SOURCE_KEYS,
  );
  const [search, setSearch] = useUrlFilter<string>("search", "");

  const [selected, setSelected] = useState<Set<string>>(new Set());
  // 批量停放进度:`done / total`,跑完保留结果直到用户手动关掉
  const [batch, setBatch] = useState<{
    running: boolean;
    done: number;
    total: number;
    failures: { id: string; title: string; reason: string }[];
  } | null>(null);
  const [reloading, setReloading] = useState(false);
  // 批量停放是批量不可逆动作(一次改 N 条状态):先展开确认行,第二次点才真跑。
  const [parkConfirmOpen, setParkConfirmOpen] = useState(false);

  // 选择集变化(增删/清空)时收起确认行——否则「已选 3 个」点开确认后
  // 再取消选择,确认行会带着过期的数量留在条上。
  useEffect(() => {
    setParkConfirmOpen(false);
  }, [selected]);

  async function reload() {
    setReloading(true);
    try {
      const d = await listQuestions({ limit: 200 });
      setQuestions(d.questions);
      setError(null);
    } catch {
      setError("后端不可用 — 确认 local-api 已启动(localhost:8088)");
    } finally {
      setReloading(false);
    }
  }

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const d = await listQuestions({ limit: 200 });
        if (alive) setQuestions(d.questions);
      } catch {
        if (alive)
          setError("后端不可用 — 确认 local-api 已启动(localhost:8088)");
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // v025 主动复访:到期清单是附加增强 — 失败静默降级,不拦列表主功能
  // (旧后端无 /revisit/due 时 404,同样静默)
  useEffect(() => {
    let alive = true;
    listDueQuestions(new Date().toISOString(), 100)
      .then((r) => {
        if (!alive) return;
        setDue(r?.due ?? []);
        // 后端 limit 会截断列表,计数只在 total 里是诚实的。
        setDueTotal(r?.total ?? r?.due?.length ?? 0);
      })
      .catch(() => {
        if (alive) {
          setDue(null);
          setDueTotal(null);
        }
      });
    return () => {
      alive = false;
    };
  }, []);

  const all = questions ?? [];
  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return all.filter(
      (q) =>
        matchesStatus(q, statusFilter) &&
        (sourceFilter === "all" || q.created_by === sourceFilter) &&
        (!needle || q.title.toLowerCase().includes(needle)),
    );
  }, [all, statusFilter, sourceFilter, search]);

  const open = visible.filter((q) => q.status === "open");
  const parked = visible.filter((q) => q.status === "parked");
  const closed = visible.filter(
    (q) => q.status !== "open" && q.status !== "parked",
  );

  // 归来条数据重建:调查会提议的 open 子问题(created_by=selene + 有父)。
  // 拍板后子问题离开 open,条自然消失 —— 不需要单独的已读状态。
  // 这两个条是"该你动手"的提示,不受筛选影响,始终按全量算。
  const proposed = all.filter(
    (q) => q.status === "open" && q.created_by === "selene" && q.parent_question_id,
  );
  const proposedIds = new Set(proposed.map((q) => q.id));
  const dueIds = new Set((due ?? []).map((d) => d.id));

  const selectableIds = visible.filter(isManageable).map((q) => q.id);
  const selectedVisible = selectableIds.filter((id) => selected.has(id));
  const allVisibleSelected =
    selectableIds.length > 0 && selectedVisible.length === selectableIds.length;

  function toggleSelect(id: string, next: boolean) {
    setSelected((prev) => {
      const copy = new Set(prev);
      if (next) copy.add(id);
      else copy.delete(id);
      return copy;
    });
  }

  function toggleAllVisible() {
    setSelected((prev) => {
      const copy = new Set(prev);
      if (allVisibleSelected) {
        for (const id of selectableIds) copy.delete(id);
      } else {
        for (const id of selectableIds) copy.add(id);
      }
      return copy;
    });
  }

  /**
   * 批量停放:串行,逐条记失败,不整体回滚。
   * 只有全部成功才清空选择 —— 失败项留在选中态,用户能直接重试,
   * 不用再翻一遍列表找回来。
   */
  async function parkSelected() {
    const targets = all.filter((q) => selected.has(q.id) && isManageable(q));
    if (targets.length === 0) return;
    setBatch({ running: true, done: 0, total: targets.length, failures: [] });
    const failures: { id: string; title: string; reason: string }[] = [];
    let done = 0;
    for (const q of targets) {
      try {
        await closeQuestion(q.id, {
          terminal_status: "parked",
          note: "批量停放(管理面)",
        });
        done += 1;
      } catch (e) {
        failures.push({
          id: q.id,
          title: q.title,
          reason: e instanceof Error ? e.message : String(e),
        });
        done += 1; // 进度按"处理过的条数"算,否则卡在 N/M 不动像是死了
      }
      setBatch({
        running: true,
        done,
        total: targets.length,
        failures: [...failures],
      });
    }
    setBatch({ running: false, done, total: targets.length, failures });
    // 失败项保留选中,成功项移除
    setSelected((prev) => {
      const next = new Set(prev);
      for (const q of targets) {
        if (!failures.some((f) => f.id === q.id)) next.delete(q.id);
      }
      return next;
    });
    await reload();
  }

  async function reopenOne(id: string) {
    try {
      await reopenQuestion(id, { reason: "从管理面恢复" });
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const filtersActive =
    statusFilter !== "active" || sourceFilter !== "all" || search.trim() !== "";

  return (
    <div className="mx-auto w-full max-w-[920px] p-6 pb-24">
      <div className="flex items-baseline gap-3">
        <h1 className="text-sm font-semibold text-ink">{tr("问题")}</h1>
        <span className="font-mono text-[10px] text-ink-4">
          {visible.length} / {all.length}
        </span>
        <Link
          href="/research/questions/new"
          className="text-xs text-accent hover:underline"
          data-testid="new-question-link"
        >
          {tr("立一个新问题 →")}
        </Link>
      </div>

      {error && <p className="mt-3 text-xs text-destructive">{tr(error)}</p>}
      {!error && questions === null && <SkeletonRows rows={8} />}

      {questions !== null && (
        <>
          {/* ── 管理面筛选条 ─────────────────────────────────────────────
              默认「未结案」= open + parked。27 个遗留问题停在链路中段,
              全量平铺会淹掉真正在跑的那几个。 */}
          <div
            data-testid="question-filters"
            className="mt-4 space-y-2 rounded-lg border border-border bg-surface-1 px-3 py-2.5"
          >
            <div className="flex flex-wrap items-center gap-1">
              {STATUS_TABS.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  data-testid={`status-tab-${t.key}`}
                  onClick={() => setStatusFilter(t.key)}
                  className={`rounded px-2 py-1 text-[10.5px] ${
                    statusFilter === t.key
                      ? "bg-accent text-accent-foreground"
                      : "text-ink-3 hover:text-ink"
                  }`}
                >
                  {tr(t.label)}
                </button>
              ))}
              <span className="mx-1 h-3 w-px bg-border" />
              {SOURCE_TABS.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  data-testid={`source-tab-${t.key}`}
                  onClick={() => setSourceFilter(t.key)}
                  className={`rounded px-2 py-1 text-[10.5px] ${
                    sourceFilter === t.key
                      ? "bg-surface-3 text-ink"
                      : "text-ink-4 hover:text-ink-3"
                  }`}
                >
                  {tr(t.label)}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={tr("按标题搜索")}
                data-testid="question-search"
                className="w-full rounded border border-border bg-surface-2 px-2 py-1 text-[11px] text-ink placeholder:text-ink-4 focus:border-accent/50 focus:outline-none"
              />
              {filtersActive && (
                <button
                  type="button"
                  data-testid="clear-filters"
                  onClick={() => {
                    setStatusFilter("active");
                    setSourceFilter("all");
                    setSearch("");
                  }}
                  className="shrink-0 text-[10px] text-ink-4 hover:text-accent"
                >
                  {tr("清除")}
                </button>
              )}
            </div>
            {selectableIds.length > 0 && (
              <div className="flex items-center gap-2 border-t border-border-soft pt-2">
                <label className="flex items-center gap-1.5 text-[10.5px] text-ink-3">
                  <input
                    type="checkbox"
                    checked={allVisibleSelected}
                    onChange={toggleAllVisible}
                    data-testid="question-select-all"
                    className="h-3.5 w-3.5 accent-accent"
                  />
                  {tr("全选本页 {n} 个可管理的", { n: selectableIds.length })}
                </label>
                <span className="ml-auto text-[9px] text-ink-4">
                  {tr("终态问题不可批量操作")}
                </span>
              </div>
            )}
          </div>

          {/* v025 主动复访条:到期该回看的问题置顶 — 「历史主动找上门来」的
              列表层形态。同构于归来条(pending-decisions-bar),空/失败=不渲染 */}
          {(due?.length ?? 0) > 0 && (
            <div
              data-testid="revisit-due-bar"
              className="mt-4 rounded-lg border border-accent/25 bg-surface-2 px-4 py-3"
            >
              <p className="text-[11.5px] text-ink-2">
                <b className="font-semibold text-ink">
                  {dueTotal ?? due!.length} {tr("个问题到了你定的复访时点")}
                </b>
                {tr(",回看一眼,再决定继续还是改期")}
              </p>
              <ul className="mt-1.5 space-y-1">
                {due!.map((d) => (
                  <li key={d.id}>
                    <Link
                      href={`/research/questions?q=${encodeURIComponent(d.id)}`}
                      className="text-xs text-ink hover:text-accent"
                    >
                      {d.title} →
                    </Link>
                    <span className="ml-2 font-mono text-[10px] text-ink-4">
                      {formatMonthDay(d.next_revisit_at)} {tr("到期")}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 归来条(持久化重建):有 Selene 提议的 open 子问题时置顶 */}
          {proposed.length > 0 && (
            <div
              data-testid="pending-decisions-bar"
              className="mt-4 rounded-lg border border-accent/25 bg-surface-2 px-4 py-3"
            >
              <p className="text-[11.5px] text-ink-2">
                <b className="font-semibold text-ink">
                  {tr("Selene 提议了")} {proposed.length} {tr("个新问题")}
                </b>
                {tr(",待你拍板")}
              </p>
              <ul className="mt-1.5 space-y-1">
                {proposed.map((q) => (
                  <li key={q.id}>
                    <Link
                      href={`/research/questions?q=${encodeURIComponent(q.id)}`}
                      className="text-xs text-ink hover:text-accent"
                    >
                      {q.title} →
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <section className="mt-4">
            <p className="text-[10px] tracking-[0.08em] text-ink-3">
              {tr("调查中 ·")} {open.length}
            </p>
            {open.length === 0 ? (
              <p className="mt-2 text-[11px] text-ink-4" data-testid="empty-open">
                {tr("没有调查中的问题 — 立一个,或等 Selene 提议。")}
              </p>
            ) : (
              <ul className="mt-2 space-y-2" data-testid="open-questions">
                {open.map((q) => (
                  <QuestionRow
                    key={q.id}
                    q={q}
                    selected={selected.has(q.id)}
                    onToggleSelect={toggleSelect}
                    revisitBadge={dueIds.has(q.id)}
                    badge={
                      proposedIds.has(q.id)
                        ? tr("待拍板") +
                          (q.parent_question_id
                            ? tr(" · 源自{title}", {
                                title:
                                  all.find((p) => p.id === q.parent_question_id)
                                    ?.title ?? "",
                              })
                            : "")
                        : undefined
                    }
                  />
                ))}
              </ul>
            )}
          </section>

          {parked.length > 0 && (
            <section className="mt-5">
              <p className="text-[10px] tracking-[0.08em] text-ink-3">
                {tr("已停放 · {n}", { n: parked.length })}
              </p>
              <ul className="mt-2 space-y-2" data-testid="parked-questions">
                {parked.map((q) => (
                  <li
                    key={q.id}
                    className="flex items-start gap-2"
                    data-testid="question-row-wrap"
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(q.id)}
                      onChange={(e) => toggleSelect(q.id, e.target.checked)}
                      aria-label={tr("选择「{title}」", { title: q.title })}
                      data-testid="question-select"
                      className="mt-4 h-3.5 w-3.5 shrink-0 accent-accent"
                    />
                    <div className="block min-w-0 flex-1 rounded-lg border border-border bg-surface-1 px-4 py-3">
                      <div className="flex items-center gap-2">
                        <Link
                          href={`/research/questions?q=${encodeURIComponent(q.id)}`}
                          className="min-w-0 truncate text-xs text-ink-2 hover:text-accent"
                          data-testid="question-row"
                        >
                          {q.title}
                        </Link>
                        <span className="rounded border border-border bg-surface-3 px-2 py-0.5 font-mono text-[9px] text-ink-3">
                          {tr("先放一放")}
                        </span>
                        <span className="ml-auto font-mono text-[10px] text-ink-4">
                          {formatMonthDay(q.opened_at)}
                        </span>
                      </div>
                      <div className="mt-1 flex items-center gap-2 text-[10px] text-ink-3">
                        <span>v{q.revision_count}</span>
                        <span>·</span>
                        <span>{formatMonthDay(q.updated_at)} {tr("更新")}</span>
                        <button
                          type="button"
                          data-testid="reopen-question"
                          onClick={() => reopenOne(q.id)}
                          className="ml-auto rounded border border-border px-1.5 py-0.5 text-[9px] text-ink-3 hover:border-accent/40 hover:text-accent"
                        >
                          {tr("恢复调查")}
                        </button>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {closed.length > 0 && (
            <section className="mt-5">
              <button
                type="button"
                data-testid="closed-toggle"
                onClick={() => setShowClosed((v) => !v)}
                className="flex w-full items-center gap-2 rounded-lg border border-border bg-surface-1 px-4 py-2 text-left hover:border-accent/40"
              >
                <span className="text-ink-3">
                  {showClosed ? (
                    <ChevronDown className="h-3 w-3" aria-hidden />
                  ) : (
                    <ChevronRight className="h-3 w-3" aria-hidden />
                  )}
                </span>
                <span className="text-[11px] text-ink-2">
                  {tr("已结案 ·")} {closed.length}
                </span>
              </button>
              {showClosed && (
                <ul className="mt-2 space-y-2" data-testid="closed-questions">
                  {closed.map((q) => (
                    <li key={q.id}>
                      <Link
                        href={`/research/questions?q=${encodeURIComponent(q.id)}`}
                        className="block rounded-lg border border-border bg-surface-1 px-4 py-3 hover:border-accent/40"
                      >
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-ink-2">{q.title}</span>
                          <span className="rounded border border-border bg-surface-3 px-2 py-0.5 font-mono text-[9px] text-ink-3">
                            {tr(TERMINAL_LABEL[q.status] ?? q.status)}
                          </span>
                          <span className="ml-auto font-mono text-[10px] text-ink-4">
                            {formatMonthDay(q.opened_at)}
                          </span>
                        </div>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {visible.length === 0 && (
            <p
              data-testid="no-match"
              className="mt-6 text-[11px] text-ink-4"
            >
              {tr("没有符合筛选的问题。")}
              {filtersActive && (
                <button
                  type="button"
                  onClick={() => {
                    setStatusFilter("active");
                    setSourceFilter("all");
                    setSearch("");
                  }}
                  className="ml-1 text-accent hover:underline"
                >
                  {tr("清除筛选")}
                </button>
              )}
            </p>
          )}
        </>
      )}

      {/* 批量操作条:选中才浮出。失败明细留在条里,不弹 toast ——
          用户需要能照着 id 去查是哪几条。
          batch 非空也要留:全部成功时选择会被清空,若只看选择数就会
          在「完成 N / N」出现的同一帧卸载,用户永远看不到结果。 */}
      {(selectedVisible.length > 0 || batch !== null) && (
        <div
          data-testid="batch-park-bar"
          className="fixed inset-x-0 bottom-6 z-30 mx-auto w-full max-w-[920px] px-6"
        >
          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-accent/40 bg-surface-1 px-4 py-2.5 shadow-lg">
            {selectedVisible.length > 0 ? (
              <>
                <span className="text-[11.5px] text-ink">
                  {tr("已选 {n} 个", { n: selectedVisible.length })}
                </span>
                {parkConfirmOpen ? (
                  <>
                    <span className="text-[11px] text-ink-2">
                      {tr("确认停放 {n} 个？停车后可在这页的「已停放」里恢复调查。").replace("{n}", String(selectedVisible.length))}
                    </span>
                    <button
                      type="button"
                      data-testid="batch-park-confirm"
                      disabled={batch?.running}
                      onClick={() => { setParkConfirmOpen(false); void parkSelected(); }}
                      className="rounded bg-accent px-2.5 py-1 text-[11px] font-medium text-accent-foreground disabled:opacity-50"
                    >
                      {batch?.running ? tr("停放中…") : tr("确认停放 {n} 个").replace("{n}", String(selectedVisible.length))}
                    </button>
                    <button
                      type="button"
                      data-testid="batch-park-cancel"
                      disabled={batch?.running}
                      onClick={() => setParkConfirmOpen(false)}
                      className="text-[10.5px] text-ink-4 hover:text-ink-2 disabled:opacity-50"
                    >
                      {tr("先不停放")}
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      type="button"
                      data-testid="batch-park"
                      disabled={batch?.running}
                      onClick={() => setParkConfirmOpen(true)}
                      className="rounded bg-accent px-2.5 py-1 text-[11px] font-medium text-accent-foreground disabled:opacity-50"
                    >
                      {batch?.running
                        ? tr("停放中…")
                        : tr("停放 {n} 个", { n: selectedVisible.length })}
                    </button>
                    <button
                      type="button"
                      data-testid="batch-clear"
                      disabled={batch?.running}
                      onClick={() => setSelected(new Set())}
                      className="text-[10.5px] text-ink-4 hover:text-ink-2 disabled:opacity-50"
                    >
                      {tr("取消选择")}
                    </button>
                  </>
                )}
              </>
            ) : (
              // 全部成功后选择清空:此时条里只剩结果,不能再显示「停放 0 个」
              <span className="text-[11.5px] text-ink">{tr("批量停放结果")}</span>
            )}
            {batch && (
              <span
                data-testid="batch-progress"
                className="ml-auto font-mono text-[10.5px] text-ink-3"
              >
                {tr("完成")} {batch.done} / {batch.total}
                {batch.failures.length > 0
                  ? ` · ${tr("失败")} ${batch.failures.length}`
                  : batch.running
                    ? ""
                    : ` · ${tr("全部成功")}`}
              </span>
            )}
            {batch && batch.failures.length > 0 && (
              <ul
                data-testid="batch-failures"
                className="w-full space-y-0.5 border-t border-border-soft pt-1.5"
              >
                {batch.failures.map((f) => (
                  <li key={f.id} className="text-[10px] text-destructive">
                    {f.title} <span className="text-ink-4">— {f.reason}</span>
                  </li>
                ))}
              </ul>
            )}
            {batch && !batch.running && (
              <button
                type="button"
                data-testid="batch-dismiss"
                onClick={() => setBatch(null)}
                className="w-full text-left text-[9px] text-ink-4 hover:text-accent"
              >
                {tr("收起结果")}
              </button>
            )}
          </div>
        </div>
      )}

      {reloading && (
        <p className="sr-only" data-testid="reloading">
          {tr("刷新中")}
        </p>
      )}
    </div>
  );
}

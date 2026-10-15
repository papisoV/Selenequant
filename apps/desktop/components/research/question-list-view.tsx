"use client";

/**
 * Question 面默认态(无 ?q)—— 问题列表:open 在前,最近结案折叠。
 * 复访入口 + 立问入口;诚实空态(没有就显示为空,不造假内容)。
 */

import { useEffect, useState } from "react";
import Link from "next/link";

import { listDueQuestions, listQuestions, type DueQuestion, type Question } from "@/lib/api/research-graph";

const TERMINAL_LABEL: Record<string, string> = {
  sufficiently_answered: "已查清楚",
  falsified: "已被证伪",
  parked: "先放一放",
  superseded: "已换问法",
};

function formatDay(iso: string): string {
  return iso.slice(5, 10);
}

function QuestionRow({ q, badge, revisitBadge }: { q: Question; badge?: string; revisitBadge?: boolean }) {
  return (
    <li>
      <Link
        href={`/research/questions?q=${encodeURIComponent(q.id)}`}
        className="block rounded-lg border border-border bg-surface-2 px-4 py-3 hover:border-accent/40"
        data-testid="question-row"
      >
        <div className="flex items-center gap-2">
          <span className="text-xs text-ink">{q.title}</span>
          {revisitBadge && (
            <span
              data-testid="revisit-due-badge"
              className="rounded border border-accent/40 bg-accent/5 px-2 py-0.5 text-[9px] text-accent"
            >
              该复访
            </span>
          )}
          {badge && (
            <span className="rounded border border-accent/40 bg-accent/5 px-2 py-0.5 text-[9px] text-accent">
              {badge}
            </span>
          )}
          <span className="ml-auto font-mono text-[10px] text-ink-4">
            {formatDay(q.opened_at)}
          </span>
        </div>
        <div className="mt-1 flex items-center gap-2 text-[10px] text-ink-3">
          <span>v{q.revision_count}</span>
          <span>·</span>
          <span>{formatDay(q.updated_at)} 更新</span>
        </div>
      </Link>
    </li>
  );
}

export function QuestionListView() {
  const [questions, setQuestions] = useState<Question[] | null>(null);
  const [due, setDue] = useState<DueQuestion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showClosed, setShowClosed] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const d = await listQuestions({ limit: 100 });
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
    listDueQuestions(new Date().toISOString())
      .then((r) => {
        if (alive) setDue(r?.due ?? []);
      })
      .catch(() => {
        if (alive) setDue(null);
      });
    return () => {
      alive = false;
    };
  }, []);

  const open = (questions ?? []).filter((q) => q.status === "open");
  const closed = (questions ?? []).filter((q) => q.status !== "open");
  // 归来条数据重建:调查会提议的 open 子问题(created_by=selene + 有父)。
  // 拍板后子问题离开 open,条自然消失 —— 不需要单独的已读状态。
  const proposed = open.filter(
    (q) => q.created_by === "selene" && q.parent_question_id,
  );
  const proposedIds = new Set(proposed.map((q) => q.id));
  const dueIds = new Set((due ?? []).map((d) => d.id));

  return (
    <main className="mx-auto h-full max-w-[920px] overflow-y-auto p-6 pb-[120px]">
      <div className="flex items-baseline gap-3">
        <h1 className="text-sm font-semibold text-ink">问题</h1>
        <Link
          href="/research/questions/new"
          className="text-xs text-accent hover:underline"
          data-testid="new-question-link"
        >
          立一个新问题 →
        </Link>
      </div>

      {error && <p className="mt-3 text-xs text-destructive">{error}</p>}
      {!error && questions === null && (
        <p className="mt-3 text-[11px] text-ink-3">加载中…</p>
      )}

      {questions !== null && (
        <>
          {/* v025 主动复访条:到期该回看的问题置顶 — 「历史主动找上门来」的
              列表层形态。同构于归来条(pending-decisions-bar),空/失败=不渲染 */}
          {(due?.length ?? 0) > 0 && (
            <div
              data-testid="revisit-due-bar"
              className="mt-4 rounded-lg border border-accent/25 bg-surface-2 px-4 py-3"
            >
              <p className="text-[11.5px] text-ink-2">
                <b className="font-semibold text-ink">
                  {due!.length} 个问题到了你定的复访时点
                </b>
                ,回看一眼,再决定继续还是改期
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
                      {formatDay(d.next_revisit_at)} 到期
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
                  Selene 提议了 {proposed.length} 个新问题
                </b>
                ,待你拍板
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
              调查中 · {open.length}
            </p>
            {open.length === 0 ? (
              <p className="mt-2 text-[11px] text-ink-4" data-testid="empty-open">
                没有调查中的问题 — 立一个,或等 Selene 提议。
              </p>
            ) : (
              <ul className="mt-2 space-y-2" data-testid="open-questions">
                {open.map((q) => (
                  <QuestionRow
                    key={q.id}
                    q={q}
                    revisitBadge={dueIds.has(q.id)}
                    badge={
                      proposedIds.has(q.id)
                        ? `待拍板${
                            q.parent_question_id
                              ? ` · 源自${
                                  (questions ?? []).find(
                                    (p) => p.id === q.parent_question_id,
                                  )?.title ?? ""
                                }`
                              : ""
                          }`
                        : undefined
                    }
                  />
                ))}
              </ul>
            )}
          </section>

          {closed.length > 0 && (
            <section className="mt-5">
              <button
                type="button"
                data-testid="closed-toggle"
                onClick={() => setShowClosed((v) => !v)}
                className="flex w-full items-center gap-2 rounded-lg border border-border bg-surface-1 px-4 py-2 text-left hover:border-accent/40"
              >
                <span className="text-ink-3">{showClosed ? "▾" : "▸"}</span>
                <span className="text-[11px] text-ink-2">
                  已结案 · {closed.length}
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
                            {TERMINAL_LABEL[q.status] ?? q.status}
                          </span>
                          <span className="ml-auto font-mono text-[10px] text-ink-4">
                            {formatDay(q.opened_at)}
                          </span>
                        </div>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}
        </>
      )}
    </main>
  );
}

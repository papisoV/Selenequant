"use client";

/**
 * Question 面 —— v5 渐进披露(2026-09-19 挂壳裁定 + v5 视觉稿)。
 *
 * 路由:/research/questions?q=<id>(research 场景,DashboardLayout 由
 * app/research/layout.tsx 祖先自动挂载——壳不是遮的,是 layout 继承)。
 *
 * 第一屏 5 元素(v5 减法三刀):
 *  1 标题卡(状态徽 + 状态带) 2 NOW BAR(调查进行中 + 让 Selene 继续查 + 自己写一版)
 *  3 当前判断卡(Figure,带「它用了」) 4 一行折叠条(旧版退底,点开才见)
 *  5 一行结案轻条(查清楚了/换个问法/先放一放)
 *
 * 深度不丢:链全展开 + 撰写面板(含账本)收进第二层——点「自己写一版」才出现,
 * 不写作时零占位。grade chip 删除(v3 悬案③裁掉);术语口语化:
 * 账本→「之前的回答与假设」、依据→「它用了」。
 *
 * 数据纪律(J1 生死线,不随布局重写而变):
 * - 账本候选唯一来源是 recallLedger 响应,渲染点在 QuestionAnswerForm 内
 *   (data-testid=recall-ledger)。撰写面板默认不渲染 = "resolve 前零条目"
 *   天然成立:不开面板,页面 DOM 里没有任何候选文本。
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { QuestionAnswerForm } from "@/components/research/question-answer-form";
import { QuestionListView } from "@/components/research/question-list-view";
import {
  closeQuestion,
  getQuestion,
  investigateQuestion,
  reopenQuestion,
  setRevisit,
  succeedQuestion,
  type CitationMigration,
  type CitedRef,
  type CouncilNewQuestion,
  type InvestigateFinishedDetail,
  type QuestionDetail,
  type QuestionEvent,
  type Revision,
} from "@/lib/api/research-graph";
import { EpisodeDrawer } from "@/components/research/episode-drawer";
import { PromoteDialog } from "@/components/research/promote-dialog";

const EVENT_LABEL: Record<string, string> = {
  created: "问题创建",
  closed: "问题关闭",
  reopened: "问题重开",
  superseded: "被取代",
  forked: "分叉",
  superseding: "取代了旧问题",
  investigate_perspective: "视角发言",
  investigate_finished: "调查完成",
  revisit_scheduled: "设了复访时点",
  revisit_cleared: "清了复访时点",
};

const POLL_INTERVAL_MS = 5000;
// 轮询 10 分钟上限:防服务端重启丢任务后前端空转(账本 investigate_running=false 会收敛,上限只是兜底)
const POLL_MAX_MS = 10 * 60 * 1000;

// 引证据 = cited 含证据节点(ev- 证据 / ep- 情景记录)。
// 与 desk /changes 的 cites_evidence 同一规则;引自己的旧 revision(rev-)不算。
const citesEvidence = (cited: string[]) =>
  cited.some((id) => id.startsWith("ev-") || id.startsWith("ep-"));

// 探针徽章:cited 的 ev- 对象里至少一个 RECORDED(实测)。
// mode/episode_id 来源 = detail.cited_refs(后端批量查 research_evidence);
// episode_id 用于点徽章打开 episode 抽屉。
const citesProbe = (cited: string[], citedRefs: CitedRef[]) =>
  cited.some((id) => citedRefs.find((r) => r.id === id)?.mode === "RECORDED");

// 本版 cited 命中的探针 episode(去重):抽屉按 episode 打开,ev-→ep- 歧义
// 在 cited_refs 层已解决(episode_id 由后端给出)。
const probeEpisodeIds = (cited: string[], citedRefs: CitedRef[]): string[] => {
  const ids = cited
    .map((id) => citedRefs.find((r) => r.id === id))
    .filter(
      (r): r is CitedRef => !!r && r.mode === "RECORDED" && !!r.episode_id,
    )
    .map((r) => r.episode_id as string);
  return [...new Set(ids)];
};

// 术语口语化(v5):terminal_status → 人话
const TERMINAL_LABEL: Record<string, string> = {
  sufficiently_answered: "已查清楚",
  falsified: "已被证伪",
  parked: "先放一放",
  superseded: "已换问法",
};

// 调查会归来条:三视角固定顺序(与后端 PERSPECTIVES 一致),缺席灰显
const PERSPECTIVE_LABELS = [
  { key: "empiricist", label: "经验派" },
  { key: "recordist", label: "记录派" },
  { key: "challenger", label: "挑战派" },
] as const;

/**
 * P3 视角流:三派 chip(未到 ⋯ / 到达 ✓✗)+ 点开看全文。
 * 数据源 = detail.events 里的 investigate_perspective 事件(逐个到达,
 * 轮询时逐个亮起);running 态 chip 用 ⋯ 占位。
 */
function PerspectiveStream({
  events,
  running,
}: {
  events: QuestionEvent[];
  running: boolean;
}) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  // 每派最新一条事件(重复调查取最新;无事件 = 未到)
  const latest = new Map<string, QuestionEvent>();
  for (const e of events) {
    if (e.kind !== "investigate_perspective") continue;
    const key = e.detail?.perspective;
    if (typeof key === "string") latest.set(key, e);
  }

  return (
    <div className="flex items-start gap-1.5 flex-wrap">
      {PERSPECTIVE_LABELS.map((p) => {
        const ev = latest.get(p.key);
        const content =
          ev && typeof ev.detail?.content === "string"
            ? (ev.detail.content as string)
            : "";
        const isOpen = openKey === p.key && content;
        const chipClass = ev
          ? ev.detail?.status === "ok"
            ? "border-accent/40 bg-accent/5 text-accent"
            : "border-border bg-surface-3 text-ink-4"
          : running
            ? "border-border bg-surface-3 text-ink-4 animate-pulse"
            : "border-border bg-surface-3 text-ink-4";
        const chipText = ev
          ? `${p.label} ${ev.detail?.status === "ok" ? "✓" : "✗"}`
          : `${p.label} ${running ? "⋯" : "—"}`;
        return (
          <span key={p.key} className="text-[10px]">
            <button
              type="button"
              disabled={!content}
              onClick={() => setOpenKey((v) => (v === p.key ? null : p.key))}
              className={`rounded border px-2 py-0.5 ${chipClass} ${
                content ? "cursor-pointer hover:border-accent/60" : "cursor-default"
              }`}
              data-testid={`perspective-chip-${p.key}`}
            >
              {chipText}
            </button>
            {isOpen && (
              <span
                className="mt-1 block max-w-[420px] whitespace-pre-wrap rounded border border-border-soft bg-surface-1 p-2 text-[11px] leading-relaxed text-ink-2"
                data-testid={`perspective-full-${p.key}`}
              >
                {content}
              </span>
            )}
          </span>
        );
      })}
    </div>
  );
}

/** A2 引用迁移行:本轮调查新增/丢弃了哪些引用 —— "上一段研究改变了这一次判断"的可见证据 */
function CitationMigrationRow({ migration }: { migration: CitationMigration }) {
  const { added, dropped, retained } = migration;
  if (added.length === 0 && dropped.length === 0) return null;
  const chip = (label: string, ids: string[], cls: string) =>
    ids.length > 0 && (
      <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] ${cls}`}>
        {label}
        <span className="num font-medium">{ids.length}</span>
      </span>
    );
  return (
    <div
      className="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-border-soft pt-2.5"
      data-testid="citation-migration-row"
    >
      <span className="text-[10px] tracking-[0.06em] text-ink-4">引用迁移</span>
      {chip("新增", added, "border-success/50 bg-success/10 text-success")}
      {chip("丢弃", dropped, "border-destructive/50 bg-destructive/10 text-destructive")}
      {chip("保留", retained, "border-border bg-surface-3 text-ink-3")}
    </div>
  );
}

function CouncilReturnBar({
  newQuestions,
  citationMigration,
  asOf,
  events,
  running,
  onDismiss,
}: {
  newQuestions: CouncilNewQuestion[];
  citationMigration: CitationMigration | null;
  asOf: string | null;
  events: QuestionEvent[];
  running: boolean;
  onDismiss?: () => void;
}) {
  const nq = newQuestions;

  return (
    <div
      data-testid="council-return-bar"
      className="rounded-lg border border-accent/25 bg-surface-2 px-4 py-3"
    >
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-[10px] tracking-[0.08em] text-ink-3">
          {running ? "调查会进行中" : "调查会归来"}
        </span>
        {asOf && (
          <span
            data-testid="return-asof-badge"
            className="rounded border border-gold/40 bg-gold/10 px-1.5 py-0.5 text-[9px] text-gold"
          >
            回放 {asOf.slice(0, 10)}
          </span>
        )}
        <PerspectiveStream events={events} running={running} />
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="ml-auto text-[10px] text-ink-4 hover:text-ink-2"
          >
            收起
          </button>
        )}
      </div>
      {!running && citationMigration && (
        <CitationMigrationRow migration={citationMigration} />
      )}
      {nq.length > 0 && (
        <div className="mt-2.5 border-t border-border-soft pt-2.5">
          <p className="text-[11px] text-ink-2">
            Selene 提议 {nq.length} 个新问题,待你拍板:
          </p>
          <ul className="mt-1.5 space-y-1">
            {nq.map((q) => (
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
          <p className="mt-1.5 text-[10px] text-ink-4">
            拍板前它们不会自动开查或结案。
          </p>
        </div>
      )}
    </div>
  );
}

function formatDay(iso: string): string {
  return iso.slice(5, 10);
}

// N1 第二刀:revision 的 rationale 带 as_of_replay:{iso} 前缀条目 = 该版出自回放轮;
// 取出日期部分(YYYY-MM-DD)供 chip 渲染
const asOfReplayDate = (rationale: string[]): string | null => {
  const hit = rationale.find((r) => r.startsWith("as_of_replay:"));
  return hit ? hit.slice("as_of_replay:".length).slice(0, 10) : null;
};

function formatTime(iso: string): string {
  return iso.slice(11, 16);
}

// v025 主动复访工具:预设时点(UTC ISO,与后端 _now() 同族 — due() 是
// TEXT 字典序比较);自定义日期取本地日历日的 UTC 终点(那天过完才到期)
const isoDaysFromNow = (days: number): string =>
  new Date(Date.now() + days * 86400_000).toISOString();

const localDateToIso = (yyyyMmDd: string): string => {
  const [y, m, d] = yyyyMmDd.split("-").map(Number);
  return new Date(Date.UTC(y, (m ?? 1) - 1, d ?? 1, 23, 59, 59)).toISOString();
};
// ↑ 与 lib/research/date-iso.ts 同式两份(此处保留本地绑定,避免大范围改动;
//   answer-form 用 lib 导出。两处若再增第三处,统一收敛到 lib)。

const revisitIsoToDateInput = (iso: string): string => iso.slice(0, 10);

/**
 * 复访行(v025):open/parked 才显示(终态后端 400)。
 * 未设 = quiet 一行 + 三个预设键 + 自定义日期;已设 = 时点 + 改/清;
 * 已到期(<= now)= accent 高亮 + 「已到期」。
 */
function RevisitBar({
  questionId,
  nextRevisitAt,
  onDone,
  onError,
}: {
  questionId: string;
  nextRevisitAt: string | null | undefined;
  onDone: () => void;
  onError: (msg: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [customOpen, setCustomOpen] = useState(false);
  const [customDate, setCustomDate] = useState("");

  const isOverdue =
    !!nextRevisitAt && nextRevisitAt <= new Date().toISOString();

  const run = async (value: string | null) => {
    setBusy(true);
    try {
      await setRevisit(questionId, value);
      onDone();
    } catch {
      onError("复访计划设置失败 — open/parked 状态才可设,请重试");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      data-testid="revisit-bar"
      className={`mt-3 flex items-center gap-2 flex-wrap rounded-lg border px-4 py-2 ${
        isOverdue
          ? "border-accent/40 bg-accent/5"
          : "border-border bg-surface-2"
      }`}
    >
      <span
        className={`text-[10.5px] ${
          isOverdue ? "font-semibold text-accent" : "text-ink-3"
        }`}
        data-testid="revisit-status"
      >
        {nextRevisitAt
          ? isOverdue
            ? `已到期(${formatDay(nextRevisitAt)}) — 该回看这个判断了`
            : `${formatDay(nextRevisitAt)} 复访`
          : "未设复访时点"}
      </span>
      {nextRevisitAt ? (
        <>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            data-testid="revisit-clear"
            onClick={() => run(null)}
          >
            清除
          </Button>
          <span className="text-[10px] text-ink-4">
            改期 = 点下面任一时点
          </span>
        </>
      ) : (
        <>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            data-testid="revisit-1w"
            onClick={() => run(isoDaysFromNow(7))}
          >
            1 周后
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            data-testid="revisit-1m"
            onClick={() => run(isoDaysFromNow(30))}
          >
            1 月后
          </Button>
          <Button
            size="sm"
            variant="ghost"
            data-testid="revisit-custom-toggle"
            disabled={busy}
            onClick={() => setCustomOpen((v) => !v)}
          >
            选个日子
          </Button>
        </>
      )}
      {customOpen && !nextRevisitAt && (
        <div className="flex w-full items-center gap-2">
          <input
            type="date"
            data-testid="revisit-custom-date"
            value={customDate}
            onChange={(e) => setCustomDate(e.target.value)}
            className="rounded border border-border bg-surface-1 px-2.5 py-1.5 text-xs text-ink focus:border-accent/50 focus:outline-none"
          />
          <Button
            size="sm"
            disabled={!customDate || busy}
            data-testid="revisit-custom-submit"
            onClick={() => run(localDateToIso(customDate))}
          >
            设为复访日
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setCustomOpen(false)}
          >
            取消
          </Button>
        </div>
      )}
    </div>
  );
}

export function QuestionSurface() {
  const params = useSearchParams();
  const router = useRouter();
  const qid = params.get("q");

  const [detail, setDetail] = useState<QuestionDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [formTick, setFormTick] = useState(0);
  const [investigating, setInvestigating] = useState(false);
  const [investigateError, setInvestigateError] = useState<string | null>(null);
  // N1 第二刀:council 回放调查 — 日期非空时本次调查以该日为认知边界(独立于 answer-form replayDate)
  const [councilReplayDate, setCouncilReplayDate] = useState("");
  // v5 渐进披露:第二层(链全展开 + 撰写面板)默认关
  const [showHistory, setShowHistory] = useState(false);
  const [showCompose, setShowCompose] = useState(false);
  // P3 归来条:新问题链接从 investigate_finished 事件取,页面在时一直显示(手动收起)
  const [returnQuestions, setReturnQuestions] = useState<CouncilNewQuestion[]>([]);
  // A2:本轮引用迁移(新增/丢弃/保留),同源 investigate_finished 事件
  const [citationMigration, setCitationMigration] = useState<CitationMigration | null>(null);
  // N1 第二刀:归来条回放标记 — finished detail 带 as_of 时本次调查是回放轮
  const [returnAsOf, setReturnAsOf] = useState<string | null>(null);
  // 拍板三键:换个问法(内联输入,预填旧标题)/ 先放一放 / 重开
  const [redirectOpen, setRedirectOpen] = useState(false);
  const [redirectTitle, setRedirectTitle] = useState("");
  const [redirecting, setRedirecting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  // 刀1:点探针徽章打开 episode 抽屉(单 episode;引用多个探针时点切换)
  const [drawerEpisodeId, setDrawerEpisodeId] = useState<string | null>(null);
  // 桥:立为支柱对话框 + 成功反馈
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [promoted, setPromoted] = useState<{ mission_id: string; pillar_id: string } | null>(
    null,
  );

  useEffect(() => {
    if (!qid) return;
    let alive = true;
    setDetail(null);
    setDetailError(null);
    setShowHistory(false); // 换问题重置第二层
    setShowCompose(false);
    setReturnQuestions([]);
    (async () => {
      try {
        const d = await getQuestion(qid);
        if (!alive) return;
        setDetail(d);
        // P3:中途刷新页面后,靠服务端真相源恢复轮询(否则进行中态会静默丢失)
        if (d.investigate_running) setInvestigating(true);
      } catch {
        if (alive)
          setDetailError("后端不可用 — 确认 local-api 已启动(localhost:8088)");
      }
    })();
    return () => {
      alive = false;
    };
  }, [qid, formTick]);

  const runInvestigate = async () => {
    if (!qid || investigating) return;
    setInvestigating(true);
    setInvestigateError(null);
    try {
      // P3:POST 只触发后台任务,立即 202;进度与结果靠下面的轮询
      // N1 第二刀:councilReplayDate 非空 → as_of 回放调查(认知边界=那天 23:59:59 本地)
      const asOf = councilReplayDate ? localDateToIso(councilReplayDate) : undefined;
      await investigateQuestion(qid, asOf ? { as_of: asOf } : undefined);
      const d = await getQuestion(qid); // 立刻刷新一次,进入 running 态
      setDetail(d);
    } catch {
      setInvestigating(false);
      setInvestigateError(
        "触发调查失败 — 确认设置里已配置 LLM(keyring selenespace.llm)",
      );
    }
  };

  // P3 轮询:investigating 期间每 5s 拉 detail,视角逐个亮起;
  // investigate_finished 事件落地 = 结束(ok → 刷新归来条;failed → 错误文案)
  useEffect(() => {
    if (!investigating || !qid) return;
    const startedAt = Date.now();
    const timer = setInterval(async () => {
      if (Date.now() - startedAt > POLL_MAX_MS) {
        setInvestigating(false);
        setInvestigateError("调查超时 — 后台可能仍在跑,稍后刷新页面查看结果");
        return;
      }
      try {
        const d = await getQuestion(qid);
        setDetail(d);
        if (!d.investigate_running) {
          const finished = [...d.events]
            .reverse()
            .find((e) => e.kind === "investigate_finished");
          const f = finished?.detail as InvestigateFinishedDetail | undefined;
          if (f?.status === "ok") {
            setReturnQuestions(f.new_questions ?? []);
            setCitationMigration(f.citation_migration ?? null);
            setReturnAsOf(f.as_of ?? null);
            setFormTick((t) => t + 1); // 拉取新 revision 落当前判断卡
          } else if (f?.status === "failed") {
            setInvestigateError(
              `自动调查失败${f.error ? ` — ${f.error}` : ""}`,
            );
          }
          setInvestigating(false);
        }
      } catch {
        // 单次轮询失败忽略,下个周期重试;连续失败由 10 分钟上限兜底
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [investigating, qid, setFormTick]);

  // 换个问法 = succeed:原问题 superseded,新问题接续跳转(拍板权在用户)
  const runSucceed = async () => {
    if (!qid || redirecting || !redirectTitle.trim()) return;
    setRedirecting(true);
    setActionError(null);
    try {
      const { question_id } = await succeedQuestion(qid, {
        title: redirectTitle.trim(),
      });
      // 切换到新问题:换 q 参数会触发 useEffect 重新拉详情
      router.push(`/research/questions?q=${encodeURIComponent(question_id)}`);
    } catch {
      setActionError("换问法失败 — 请重试");
      setRedirecting(false);
    }
  };

  // 先放一放 = close(parked)
  const runPark = async () => {
    if (!qid) return;
    setActionError(null);
    try {
      await closeQuestion(qid, { terminal_status: "parked" });
      setFormTick((t) => t + 1);
    } catch {
      setActionError("结案失败 — 请重试");
    }
  };

  // 重开:closed → open(终态可逆,历史不回退)
  const runReopen = async () => {
    if (!qid) return;
    setActionError(null);
    try {
      await reopenQuestion(qid, { reason: "用户重开" });
      setFormTick((t) => t + 1);
    } catch {
      setActionError("重开失败 — 请重试");
    }
  };

  if (!qid) {
    return <QuestionListView />;
  }

  if (detailError) {
    return (
      <main className="mx-auto flex h-full max-w-2xl flex-col justify-center gap-2 p-6">
        <p className="text-xs text-destructive">{detailError}</p>
        <Link
          href="/research/questions/new"
          className="text-xs text-accent hover:underline"
        >
          立一个新问题 →
        </Link>
      </main>
    );
  }

  if (!detail) {
    return (
      <main className="mx-auto h-full max-w-2xl p-6">
        <p className="text-[11px] text-ink-3">加载中…</p>
      </main>
    );
  }

  const isOpen = detail.question.status === "open";
  const isParked = detail.question.status === "parked";
  const latest = detail.revisions.at(-1) ?? null;
  const earlier = detail.revisions.slice(0, -1);
  const citedByLatest = new Set(latest?.cited ?? []);
  const latestProbeEpisodes = latest
    ? probeEpisodeIds(latest.cited, detail.cited_refs)
    : [];

  return (
    <main className="mx-auto h-full max-w-[920px] overflow-y-auto p-6 pb-[120px]">
      {/* ═══ 第一屏 ═══ */}

      {/* ① 标题卡:状态徽 + 标题 + 状态带(结案后显示去向) */}
      <section
        data-testid="section-current-answer"
        className="rounded-lg border border-border bg-surface-2 p-4"
      >
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant="outline">
            {isOpen
              ? "调查中"
              : TERMINAL_LABEL[detail.question.status] ?? detail.question.status}
          </Badge>
          <span className="font-mono text-[10px] text-ink-3">
            {detail.question.id}
          </span>
          <span className="ml-auto font-mono text-[10px] text-ink-3">
            opened {formatDay(detail.question.opened_at)}
          </span>
        </div>
        <h1 className="mt-2 text-[17px] font-semibold leading-snug text-ink">
          {detail.question.title}
        </h1>
        <div className="mt-3 border-t border-border-soft pt-3 text-[11px] text-ink-3">
          {isOpen
            ? "调查中 — 回答会随着证据与经验一版版变好"
            : `已结案:${TERMINAL_LABEL[detail.question.status] ?? detail.question.status}`}
        </div>
      </section>

      {/* ①.5 复访行(v025):open/parked 才显示;主动复访=历史找上门的详情层入口 */}
      {(isOpen || isParked) && (
        <RevisitBar
          questionId={detail.question.id}
          nextRevisitAt={detail.question.next_revisit_at}
          onDone={() => setFormTick((t) => t + 1)}
          onError={setActionError}
        />
      )}

      {/* ② NOW BAR:继续查 = 真触发 investigate;自己写一版 = 展开撰写面板 */}
      {isOpen && (
        <div
          data-testid="investigate-bar"
          className="mt-3 flex items-center gap-3 flex-wrap rounded-lg border border-border bg-surface-2 px-4 py-2.5"
        >
          <span className="text-[11.5px] text-ink-2">
            <b className="font-semibold text-ink">调查进行中</b>
            {detail.question.revision_count > 0
              ? ` — Selene 已给出 ${detail.question.revision_count} 版回答`
              : " — 还没有回答,让它先查一轮"}
          </span>
          <div className="ml-auto flex items-center gap-2">
            {councilReplayDate && (
              <span
                data-testid="council-replay-bar"
                className="flex items-center gap-1 rounded border border-gold/40 bg-gold/10 px-2 py-0.5 text-[10px] text-gold"
              >
                回放 {councilReplayDate}
                <button
                  type="button"
                  data-testid="council-replay-clear"
                  onClick={() => setCouncilReplayDate("")}
                  className="cursor-pointer text-gold/70 hover:text-gold"
                  aria-label="清除回放日期"
                >
                  ✕
                </button>
              </span>
            )}
            <input
              type="date"
              data-testid="council-replay-date"
              value={councilReplayDate}
              onChange={(e) => setCouncilReplayDate(e.target.value)}
              className="rounded border border-border bg-surface-1 px-2 py-1 text-[11px] text-ink-2"
              title="回放调查:以该日为认知边界重跑本轮(留空 = 正常调查)"
            />
            <Button size="sm" onClick={runInvestigate} disabled={investigating}>
              {investigating ? "调查中…" : "让 Selene 继续查"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShowCompose((v) => !v)}
            >
              自己写一版
            </Button>
          </div>
          {investigating && (
            <p className="w-full text-[10px] text-ink-4">
              三视角 + 主持人,通常 2~5 分钟 — 页面可离开,回来仍会显示结果(视角逐个亮起于下方)。
            </p>
          )}
          {investigateError && (
            <p className="w-full text-xs text-destructive">{investigateError}</p>
          )}
        </div>
      )}

      {/* ②.5 调查会归来条:进行中(视角逐个亮起)/归来(新问题拍板 + 引用迁移)共用,平时零占位 */}
      {(investigating || returnQuestions.length > 0 || citationMigration) && detail && (
        <div className="mt-3">
          <CouncilReturnBar
            newQuestions={returnQuestions}
            citationMigration={citationMigration}
            asOf={returnAsOf}
            events={detail.events}
            running={investigating}
            onDismiss={
              investigating
                ? undefined
                : () => {
                    setReturnQuestions([]);
                    setCitationMigration(null);
                    setReturnAsOf(null);
                  }
            }
          />
        </div>
      )}

      {/* ③ 当前判断卡(Figure):最新一版,带「它用了」 */}
      <div
        className="mt-3 rounded-lg border border-accent/25 bg-surface-2 p-4"
        data-testid="current-answer"
      >
        <div className="flex items-center gap-2">
          <Link
            href={`/research/graph?focus=${latest?.id ?? detail.question.id}`}
            className="ml-auto text-[10px] text-ink-3 hover:text-accent"
          >
            在图谱中查看 →
          </Link>
        </div>
        {latest ? (
          <>
            <div className="flex items-center gap-2">
              <span className="rounded bg-accent px-2 py-0.5 font-mono text-[10px] font-semibold text-accent-foreground">
                v{latest.seq}
              </span>
              <span className="text-[9px] tracking-[0.08em] text-ink-3">
                当前判断
              </span>
              <Badge
                variant="outline"
                className={
                  latest.actor === "selene"
                    ? "border-accent/40 text-accent"
                    : "border-gold/40 text-gold"
                }
              >
                {latest.actor}
              </Badge>
              {asOfReplayDate(latest.rationale) && (
                <span
                  data-testid="revision-asof-badge"
                  className="rounded border border-gold/40 bg-gold/10 px-1.5 py-0.5 text-[9px] text-gold"
                >
                  回放 {asOfReplayDate(latest.rationale)}
                </span>
              )}
              <span className="ml-auto font-mono text-[10px] text-ink-4">
                {formatDay(latest.created_at)} {formatTime(latest.created_at)}
              </span>
            </div>
            <p className="mt-2 whitespace-pre-wrap text-[13px] leading-relaxed text-ink">
              {latest.content}
            </p>
            {latest.cited.length > 0 && (
              <div className="mt-3 border-t border-border-soft pt-2.5">
                <span className="text-[9px] text-ink-4">它用了</span>
                <span className="ml-2 rounded border border-accent/35 bg-accent/5 px-2 py-0.5 font-mono text-[9px] text-accent">
                  上一版的判断
                </span>
                {citesEvidence(latest.cited) && (
                  <span
                    data-testid="cites-evidence-badge"
                    className="ml-2 rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3"
                  >
                    引证据
                  </span>
                )}
                {latestProbeEpisodes.length > 0 && (
                  <button
                    type="button"
                    data-testid="probe-badge"
                    onClick={() =>
                      setDrawerEpisodeId((v) =>
                        v === latestProbeEpisodes[0]
                          ? null
                          : latestProbeEpisodes[0],
                      )
                    }
                    className="ml-1.5 cursor-pointer rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-accent hover:border-accent/50"
                  >
                    探针 {drawerEpisodeId ? "▾" : "▸"}
                  </button>
                )}
              </div>
            )}
            {drawerEpisodeId && (
              <EpisodeDrawer
                episodeId={drawerEpisodeId}
                onClose={() => setDrawerEpisodeId(null)}
              />
            )}
          </>
        ) : (
          <p className="text-xs text-ink-2">
            还没有回答 — 让 Selene 先查一轮,或自己写第一版。
          </p>
        )}
      </div>

      {/* ④ 旧版退一行折叠条(Ground):点开才见链 */}
      {earlier.length > 0 && (
        <button
          type="button"
          data-testid="history-toggle"
          onClick={() => setShowHistory((v) => !v)}
          className="mt-3 flex w-full items-center gap-2 rounded-lg border border-border bg-surface-1 px-4 py-2 text-left hover:border-accent/40"
        >
          <span className="text-ink-3">{showHistory ? "▾" : "▸"}</span>
          <span className="text-[11px] text-ink-2">
            更早的回答 · {earlier.length} 版
          </span>
          <span className="text-[10px] text-pos">
            {citedByLatest.size > 0 ? `✓ v${latest?.seq} 用了它` : ""}
          </span>
        </button>
      )}

      {/* ⑤ 结案轻条:一行三键(功能=Approve/Redirect/Park,状态机不变) */}
      {isOpen && (
        <div
          data-testid="section-actions"
          className="mt-3 flex items-center gap-2 flex-wrap rounded-lg border border-border bg-surface-2 px-4 py-2"
        >
          <span className="text-[10.5px] text-ink-3">这个问题查清楚了?</span>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setShowCompose(true);
              setShowHistory(true);
              requestAnimationFrame(() =>
                document
                  .getElementById("next-answer")
                  ?.scrollIntoView({ behavior: "smooth" }),
              );
            }}
          >
            自己写终版
          </Button>
          <Button
            size="sm"
            variant="outline"
            data-testid="redirect-btn"
            onClick={() => {
              setRedirectOpen((v) => !v);
              setRedirectTitle(detail.question.title); // 预填旧标题,改问法而非从零写
            }}
          >
            换个问法
          </Button>
          {/* 立为支柱:仅当已有回答时显示(桥的入口;无 revision 无处可提) */}
          {(detail.revisions?.length ?? 0) > 0 && (
            <Button
              size="sm"
              variant="outline"
              data-testid="promote-btn"
              onClick={() => setPromoteOpen(true)}
            >
              立为支柱
            </Button>
          )}
          <Button size="sm" variant="ghost" data-testid="park-btn" onClick={runPark}>
            先放一放
          </Button>
          {redirectOpen && (
            <div className="flex w-full items-center gap-2">
              <input
                data-testid="redirect-input"
                value={redirectTitle}
                onChange={(e) => setRedirectTitle(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && runSucceed()}
                placeholder="新问题的问法…"
                className="min-w-0 flex-1 rounded border border-border bg-surface-1 px-2.5 py-1.5 text-xs text-ink placeholder:text-ink-4 focus:border-accent/50 focus:outline-none"
              />
              <Button
                size="sm"
                disabled={!redirectTitle.trim() || redirecting}
                data-testid="redirect-submit"
                onClick={runSucceed}
              >
                {redirecting ? "接续中…" : "以新问题接续"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setRedirectOpen(false)}
              >
                取消
              </Button>
            </div>
          )}
        </div>
      )}

      {/* 已结案:重开行(状态轴可逆;状态本身已在标题卡显示) */}
      {!isOpen && (
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-border bg-surface-2 px-4 py-2">
          <span className="text-[10.5px] text-ink-3">想继续查这个问法?</span>
          <Button
            size="sm"
            variant="ghost"
            data-testid="reopen-btn"
            onClick={runReopen}
          >
            重开
          </Button>
        </div>
      )}
      {actionError && <p className="mt-2 text-xs text-destructive">{actionError}</p>}

      {/* 桥:立为支柱成功反馈(旧支柱退不退役仍是用户的另一颗按钮) */}
      {promoted && (
        <p className="mt-2 text-xs text-pos" data-testid="promote-success">
          已立为支柱 {promoted.pillar_id} · mission{" "}
          <a
            className="text-accent hover:underline"
            href={`/research/missions?id=${encodeURIComponent(promoted.mission_id)}`}
          >
            {promoted.mission_id}
          </a>
        </p>
      )}

      {promoteOpen && (
        <PromoteDialog
          questionId={detail.question.id}
          revisionSeq={detail.revisions?.[detail.revisions.length - 1]?.seq ?? 1}
          onClose={() => setPromoteOpen(false)}
          onPromoted={(res) => {
            setPromoteOpen(false);
            setPromoted({ mission_id: res.mission_id, pillar_id: res.pillar_id });
          }}
        />
      )}

      {/* ═══ 第二层:点开才渲染(v5 减法刀②——不写作时零占位) ═══ */}

      {showHistory && earlier.length > 0 && (
        <section className="mt-5 space-y-3" data-testid="section-timeline">
          {[...earlier].reverse().map((rev: Revision) => (
            <div
              key={rev.id}
              data-testid="ledger-item-history"
              className="rounded-lg border border-border bg-surface-1 p-3.5"
            >
              <div className="flex items-center gap-2">
                <span className="rounded border border-border bg-surface-3 px-2 py-0.5 font-mono text-[10px] text-ink-3">
                  v{rev.seq}
                </span>
                <Badge
                  variant="outline"
                  className={
                    rev.actor === "selene"
                      ? "border-accent/40 text-accent"
                      : "border-gold/40 text-gold"
                  }
                >
                  {rev.actor}
                </Badge>
                {asOfReplayDate(rev.rationale) && (
                  <span
                    data-testid="revision-asof-badge-history"
                    className="rounded border border-gold/40 bg-gold/10 px-1.5 py-0.5 text-[9px] text-gold"
                  >
                    回放 {asOfReplayDate(rev.rationale)}
                  </span>
                )}
                <span className="ml-auto font-mono text-[10px] text-ink-4">
                  {formatDay(rev.created_at)} {formatTime(rev.created_at)}
                </span>
              </div>
              <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-ink-2">
                {rev.content}
              </p>
              <div className="mt-2 border-t border-border-soft pt-2 text-[9px] text-ink-4">
                {rev.cited.length > 0
                  ? `它用了 ${rev.cited.length} 条记录`
                  : "无 — 第一版"}
                {citesEvidence(rev.cited) && (
                  <span
                    data-testid="cites-evidence-badge-history"
                    className="ml-2 rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3"
                  >
                    引证据
                  </span>
                )}
                {citesProbe(rev.cited, detail.cited_refs) && (
                  <button
                    type="button"
                    data-testid="probe-badge-history"
                    onClick={() => {
                      const eps = probeEpisodeIds(rev.cited, detail.cited_refs);
                      if (eps.length > 0)
                        setDrawerEpisodeId((v) => (v === eps[0] ? null : eps[0]));
                    }}
                    className="ml-1.5 cursor-pointer rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-accent hover:border-accent/50"
                  >
                    探针 {drawerEpisodeId ? "▾" : "▸"}
                  </button>
                )}
                {citedByLatest.has(rev.id) && (
                  <span className="ml-2 text-pos">✓ 被 v{latest?.seq} 引用</span>
                )}
              </div>
            </div>
          ))}
          <div className="rounded-lg border border-border bg-surface-1 p-3.5">
            <p className="text-[11px] text-ink-3">时间线</p>
            <ol className="mt-2 space-y-1.5">
              {detail.events.map((e) => (
                <li key={e.id} className="text-xs text-ink-2">
                  <span className="font-mono text-[10px] text-ink-4">
                    {e.occurred_at.slice(0, 16).replace("T", " ")}
                  </span>
                  <span className="ml-2">
                    {EVENT_LABEL[e.kind] ?? e.kind}
                    {typeof e.detail?.note === "string"
                      ? ` — ${e.detail.note as string}`
                      : ""}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        </section>
      )}

      {/* 撰写面板:点「自己写一版」才出现;J1 账本(recall 单源)在表单内 */}
      {showCompose && isOpen && (
        <div className="mt-5" data-testid="compose-panel">
          <QuestionAnswerForm
            key={formTick}
            questionId={detail.question.id}
            onSubmitted={() => {
              setFormTick((t) => t + 1);
              setShowCompose(false);
            }}
          />
        </div>
      )}
    </main>
  );
}

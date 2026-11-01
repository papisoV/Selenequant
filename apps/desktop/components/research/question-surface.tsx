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

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SkeletonRows } from "@/components/shared/skeleton";
import { QuestionAnswerForm } from "@/components/research/question-answer-form";
import { QuestionListView } from "@/components/research/question-list-view";
import { RevisionStructured } from "@/components/research/revision-structured";
import {
  cancelInvestigation,
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
  type MarketMaterialDetail,
  type QuestionDetail,
  type QuestionEvent,
  type Revision,
} from "@/lib/api/research-graph";
import { EpisodeDrawer } from "@/components/research/episode-drawer";
import { PromoteDialog } from "@/components/research/promote-dialog";
import { useUiText } from "@/components/i18n/use-ui-text"
import { formatMonthDay } from "@/lib/format"
import { formatPending, type PendingMessage } from "@/lib/i18n/types"

// 键是后端 question 状态/事件 kind 枚举,值是中文展示文案 —— 表不动,渲染点过 tr()。
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

// 「上一版的判断」徽章只在真引用了旧 revision(rev-)时出现,否则纯证据引用会挂着错误标签。
const citesRevision = (cited: string[]) =>
  cited.some((id) => id.startsWith("rev-"));

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
  const tr = useUiText()
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
          ? `${tr(p.label)} ${ev.detail?.status === "ok" ? "✓" : "✗"}`
          : `${tr(p.label)} ${running ? "⋯" : "—"}`;
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
  const tr = useUiText()

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
      <span className="text-[10px] tracking-[0.06em] text-ink-4">{tr("引用迁移")}</span>
      {chip(tr("新增"), added, "border-success/50 bg-success/10 text-success")}
      {chip(tr("丢弃"), dropped, "border-destructive/50 bg-destructive/10 text-destructive")}
      {chip(tr("保留"), retained, "border-border bg-surface-3 text-ink-3")}
    </div>
  );
}

// 行情材料行(诚实化):本轮到底注入了没有。旧版失败时后端静默返回 None、
// 前端什么都不显示 —— "没有行情"与"有行情但没人提"在界面上不可区分。
// null = 旧版后端没这个字段,不渲染(不冒充"没有行情")。
function MarketMaterialRow({
  material,
}: {
  material: MarketMaterialDetail | null;
}) {
  const tr = useUiText();
  if (!material) return null;
  const injected = material.state === "ok";
  return (
    <div
      data-testid="market-material-row"
      data-state={material.state}
      className="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-border-soft pt-2.5"
    >
      <span className="text-[10px] tracking-[0.06em] text-ink-4">{tr("行情材料")}</span>
      <span
        className={`rounded border px-1.5 py-0.5 text-[10px] ${
          injected
            ? "border-border bg-surface-3 text-ink-3"
            : "border-gold/40 bg-gold/10 text-gold"
        }`}
      >
        {injected ? tr("已注入") : tr("无行情")}
      </span>
      <span className="text-[10px] text-ink-4">{material.reason}</span>
    </div>
  );
}

function CouncilReturnBar({
  newQuestions,
  citationMigration,
  asOf,
  marketMaterial,
  events,
  running,
  onDismiss,
}: {
  newQuestions: CouncilNewQuestion[];
  citationMigration: CitationMigration | null;
  asOf: string | null;
  marketMaterial: MarketMaterialDetail | null;
  events: QuestionEvent[];
  running: boolean;
  onDismiss?: () => void;
}) {
  const tr = useUiText()

  const nq = newQuestions;

  return (
    <div
      data-testid="council-return-bar"
      className="rounded-lg border border-accent/25 bg-surface-2 px-4 py-3"
    >
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-[10px] tracking-[0.08em] text-ink-3">
          {running ? tr("调查会进行中") : tr("调查会归来")}
        </span>
        {asOf && (
          <span
            data-testid="return-asof-badge"
            className="rounded border border-gold/40 bg-gold/10 px-1.5 py-0.5 text-[9px] text-gold"
          >
            {tr("回放")} {asOf.slice(0, 10)}
          </span>
        )}
        <PerspectiveStream events={events} running={running} />
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="ml-auto text-[10px] text-ink-4 hover:text-ink-2"
          >
            {tr("收起")}
          </button>
        )}
      </div>
      {!running && (
        <MarketMaterialRow material={marketMaterial} />
      )}
      {!running && citationMigration && (
        <CitationMigrationRow migration={citationMigration} />
      )}
      {nq.length > 0 && (
        <div className="mt-2.5 border-t border-border-soft pt-2.5">
          <p className="text-[11px] text-ink-2">
            {tr("Selene 提议")} {nq.length} {tr("个新问题,待你拍板:")}
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
            {tr("拍板前它们不会自动开查或结案。")}
          </p>
        </div>
      )}
    </div>
  );
}

function formatDay(iso: string): string {
  return formatMonthDay(iso);
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
  const tr = useUiText()

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
      onError(tr("复访计划设置失败 — open/parked 状态才可设,请重试"));
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
            ? tr("已到期({d}) — 该回看这个判断了", { d: formatDay(nextRevisitAt) })
            : tr("{d} 复访", { d: formatDay(nextRevisitAt) })
          : tr("未设复访时点")}
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
            {tr("清除")}
          </Button>
          <span className="text-[10px] text-ink-4">
            {tr("改期 = 点下面任一时点")}
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
            {tr("1 周后")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            data-testid="revisit-1m"
            onClick={() => run(isoDaysFromNow(30))}
          >
            {tr("1 月后")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            data-testid="revisit-custom-toggle"
            disabled={busy}
            onClick={() => setCustomOpen((v) => !v)}
          >
            {tr("选个日子")}
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
            {tr("设为复访日")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setCustomOpen(false)}
          >
            {tr("取消")}
          </Button>
        </div>
      )}
    </div>
  );
}

export function QuestionSurface() {
  const tr = useUiText()

  const params = useSearchParams();
  const router = useRouter();
  const qid = params.get("q");

  const [detail, setDetail] = useState<QuestionDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [formTick, setFormTick] = useState(0);
  const [investigating, setInvestigating] = useState(false);
  const [investigateError, setInvestigateError] = useState<PendingMessage | null>(null);
  // N1 第二刀:council 回放调查 — 日期非空时本次调查以该日为认知边界(独立于 answer-form replayDate)
  const [councilReplayDate, setCouncilReplayDate] = useState("");
  // 人工介入:本轮调查的关注点/约束,随下一次"让 Selene 继续查"一起发出
  const [councilGuidance, setCouncilGuidance] = useState("");
  const [cancelling, setCancelling] = useState(false);
  // v5 渐进披露:第二层(链全展开 + 撰写面板)默认关
  const [showHistory, setShowHistory] = useState(false);
  const [showCompose, setShowCompose] = useState(false);
  // 回放日期是"把下次调查变成历史回放"的开关,不是普通参数 —— 摊在
  // 主 CTA 旁边时,误填一次就会让整轮调查回到过去,而且界面上看不出。
  // 改成默认折叠:展开才出现日期框,填了之后由金色回放条常驻显示。
  const [showReplay, setShowReplay] = useState(false);
  // P3 归来条:新问题链接从 investigate_finished 事件取,页面在时一直显示(手动收起)
  const [returnQuestions, setReturnQuestions] = useState<CouncilNewQuestion[]>([]);
  // A2:本轮引用迁移(新增/丢弃/保留),同源 investigate_finished 事件
  const [citationMigration, setCitationMigration] = useState<CitationMigration | null>(null);
  // N1 第二刀:归来条回放标记 — finished detail 带 as_of 时本次调查是回放轮
  const [returnAsOf, setReturnAsOf] = useState<string | null>(null);
  // 诚实化:本轮行情材料注入结果(state=ok/no_symbol/klines_missing/…)
  const [returnMaterial, setReturnMaterial] = useState<MarketMaterialDetail | null>(null);
  // 拍板三键:换个问法(内联输入,预填旧标题)/ 先放一放 / 重开
  const [redirectOpen, setRedirectOpen] = useState(false);
  const [redirectTitle, setRedirectTitle] = useState("");
  const [redirecting, setRedirecting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  // 叫停 / 先放一放 都是会终止进行中工作的动作:先展开确认,第二次点才真跑。
  const [cancelConfirmOpen, setCancelConfirmOpen] = useState(false);
  const [parkConfirmOpen, setParkConfirmOpen] = useState(false);
  // 刀1:点探针徽章打开 episode 抽屉(单 episode;引用多个探针时点切换)
  const [drawerEpisodeId, setDrawerEpisodeId] = useState<string | null>(null);
  // 桥:立为支柱对话框 + 成功反馈
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [promoted, setPromoted] = useState<{ mission_id: string; pillar_id: string } | null>(
    null,
  );

  // 这一段只在"换问题"时重置;formTick 刷新(调查结束拉新 revision)不能清,
  // 否则会把它自己刚写进来的归来条状态抹掉 —— 轮询先 setReturnQuestions/
  // setReturnMaterial,紧接着 setFormTick,重跑的 effect 立刻把它们清空,
  // 归来条就永远闪不出来。
  const lastFetchedQid = useRef(qid);
  useEffect(() => {
    if (!qid) return;
    const switchedQuestion = lastFetchedQid.current !== qid;
    lastFetchedQid.current = qid;
    let alive = true;
    if (switchedQuestion) {
      setDetail(null);
      setDetailError(null);
      setShowHistory(false); // 换问题重置第二层
      setShowCompose(false);
      setReturnQuestions([]);
      setReturnMaterial(null);
    }
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
      const guidance = councilGuidance.trim() || undefined;
      const payload =
        asOf || guidance
          ? { ...(asOf ? { as_of: asOf } : {}), ...(guidance ? { guidance } : {}) }
          : undefined;
      await investigateQuestion(qid, payload);
      const d = await getQuestion(qid); // 立刻刷新一次,进入 running 态
      setDetail(d);
    } catch {
      setInvestigating(false);
      setInvestigateError("触发调查失败 — 确认设置里已配置 LLM(keyring selenespace.llm)");
    }
  };

  // 人工叫停:服务端 200=cancelled/finished,202=cancelling(慢取消兜底)。
  // 无论哪种,本地立即退出 investigating 态;cancelling 时轮询已停,靠刷新收敛。
  const runCancel = async () => {
    if (!qid || cancelling) return;
    setCancelConfirmOpen(false);
    setCancelling(true);
    setInvestigateError(null);
    try {
      await cancelInvestigation(qid);
      setInvestigating(false);
      const d = await getQuestion(qid);
      setDetail(d);
    } catch {
      setInvestigateError("叫停失败 — 调查可能已结束,刷新页面查看最新状态");
    } finally {
      setCancelling(false);
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
            setReturnMaterial(f.market_material ?? null);
            setFormTick((t) => t + 1); // 拉取新 revision 落当前判断卡
          } else if (f?.status === "failed") {
            setInvestigateError(
              f.error ? { source: "自动调查失败 — {err}", params: { err: f.error } } : "自动调查失败",
            );
          } else if (f?.status === "cancelled") {
            // 人为叫停(可能是别的窗口点的):收敛即可,不报错
            setInvestigateError("本轮调查已被叫停");
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

  // 先放一放 = close(parked)。停放后可在问题列表「已停放」里恢复,
  // 但进行中的调查会就此中断 —— 所以先确认一步。
  const runPark = async () => {
    if (!qid) return;
    setActionError(null);
    setParkConfirmOpen(false);
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
      <div className="mx-auto flex h-full max-w-2xl flex-col justify-center gap-2 p-6">
        <p className="text-xs text-destructive">{tr(detailError)}</p>
        <Link
          href="/research/questions/new"
          className="text-xs text-accent hover:underline"
        >
          {tr("立一个新问题 →")}
        </Link>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="mx-auto h-full max-w-2xl p-6">
        <SkeletonRows rows={7} />
      </div>
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
    <div className="mx-auto w-full max-w-[920px] p-6">
      {/* ═══ 第一屏 ═══ */}

      {/* ① 标题卡:状态徽 + 标题 + 状态带(结案后显示去向) */}
      <section
        data-testid="section-current-answer"
        className="rounded-lg border border-border bg-surface-2 p-4"
      >
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant="outline">
            {isOpen
              ? tr("调查中")
              : tr(TERMINAL_LABEL[detail.question.status] ?? detail.question.status)}
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
            ? tr("调查中 — 回答会随着证据与经验一版版变好")
            : tr("已结案:{label}", { label: tr(TERMINAL_LABEL[detail.question.status] ?? detail.question.status) })}
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
            <b className="font-semibold text-ink">{tr("调查进行中")}</b>
            {detail.question.revision_count > 0
              ? tr(" — Selene 已给出 {n} 版回答", { n: detail.question.revision_count })
              : tr(" — 还没有回答,让它先查一轮")}
          </span>
          <div className="ml-auto flex items-center gap-2">
            {councilReplayDate && (
              <span
                data-testid="council-replay-bar"
                className="flex items-center gap-1 rounded border border-gold/40 bg-gold/10 px-2 py-0.5 text-[10px] text-gold"
              >
                {tr("回放")} {councilReplayDate}
                <button
                  type="button"
                  data-testid="council-replay-clear"
                  onClick={() => setCouncilReplayDate("")}
                  className="cursor-pointer text-gold/70 hover:text-gold"
                  aria-label={tr("清除回放日期")}
                >
                  ✕
                </button>
              </span>
            )}
            <input
              type="text"
              data-testid="council-guidance"
              value={councilGuidance}
              onChange={(e) => setCouncilGuidance(e.target.value)}
              maxLength={2000}
              placeholder={tr("本轮关注点(可选)")}
              className="w-44 rounded border border-border bg-surface-1 px-2 py-1 text-[11px] text-ink-2 placeholder:text-ink-4"
              title={tr("人工介入:三视角与主持人都会先看到这条指示(不是证据,不会进引用)")}
            />
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShowReplay((v) => !v)}
              data-testid="council-replay-toggle"
              aria-expanded={showReplay}
            >
              {tr("回放调查")}{" "}
              {showReplay ? (
                <ChevronDown className="inline h-3 w-3" aria-hidden />
              ) : (
                <ChevronRight className="inline h-3 w-3" aria-hidden />
              )}
            </Button>
            {showReplay && (
              <input
                type="date"
                data-testid="council-replay-date"
                value={councilReplayDate}
                onChange={(e) => setCouncilReplayDate(e.target.value)}
                aria-label={tr("回放调查日期")}
                className="rounded border border-border bg-surface-1 px-2 py-1 text-[11px] text-ink-2"
                title={tr("回放调查:以该日为认知边界重跑本轮(留空 = 正常调查)")}
              />
            )}
            <Button size="sm" onClick={runInvestigate} disabled={investigating}>
              {investigating ? tr("调查中…") : tr("让 Selene 继续查")}
            </Button>
            {investigating && (
              cancelConfirmOpen ? (
                <>
                  <span className="text-[10px] text-ink-3">
                    {tr("叫停会中断本轮调查,已完成的部分保留")}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => { setCancelConfirmOpen(false); void runCancel(); }}
                    disabled={cancelling}
                    data-testid="council-cancel-confirm"
                  >
                    {cancelling ? tr("叫停中…") : tr("确认叫停")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setCancelConfirmOpen(false)}
                    disabled={cancelling}
                    data-testid="council-cancel-abort"
                  >
                    {tr("继续查")}
                  </Button>
                </>
              ) : (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setCancelConfirmOpen(true)}
                  disabled={cancelling}
                  data-testid="council-cancel"
                >
                  {cancelling ? tr("叫停中…") : tr("叫停")}
                </Button>
              )
            )}
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShowCompose((v) => !v)}
            >
              {tr("自己写一版")}
            </Button>
          </div>
          {investigating && (
            <p className="w-full text-[10px] text-ink-4">
              {tr("三视角 + 主持人,通常 2~5 分钟 — 页面可离开,回来仍会显示结果(视角逐个亮起于下方)。")}
            </p>
          )}
          {investigateError && (
            <p className="w-full text-xs text-destructive">{formatPending(investigateError, tr)}</p>
          )}
        </div>
      )}

      {/* ②.5 调查会归来条:进行中(视角逐个亮起)/归来(新问题拍板 + 引用迁移
          + 行情材料有无)共用,平时零占位 */}
      {(investigating ||
        returnQuestions.length > 0 ||
        citationMigration ||
        returnMaterial) &&
        detail && (
        <div className="mt-3">
          <CouncilReturnBar
            newQuestions={returnQuestions}
            citationMigration={citationMigration}
            asOf={returnAsOf}
            marketMaterial={returnMaterial}
            events={detail.events}
            running={investigating}
            onDismiss={
              investigating
                ? undefined
                : () => {
                    setReturnQuestions([]);
                    setCitationMigration(null);
                    setReturnAsOf(null);
                    setReturnMaterial(null);
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
            {tr("在图谱中查看 →")}
          </Link>
        </div>
        {latest ? (
          <>
            <div className="flex items-center gap-2">
              <span className="rounded bg-accent px-2 py-0.5 font-mono text-[10px] font-semibold text-accent-foreground">
                v{latest.seq}
              </span>
              <span className="text-[9px] tracking-[0.08em] text-ink-3">
                {tr("当前判断")}
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
                  {tr("回放")} {asOfReplayDate(latest.rationale)}
                </span>
              )}
              <span className="ml-auto font-mono text-[10px] text-ink-4">
                {formatDay(latest.created_at)} {formatTime(latest.created_at)}
              </span>
            </div>
            <p className="mt-2 whitespace-pre-wrap text-[13px] leading-relaxed text-ink">
              {latest.content}
            </p>
            <RevisionStructured
              structured={latest.structured}
              testIdPrefix="revision-structured"
            />
            {latest.cited.length > 0 && (
              <div className="mt-3 border-t border-border-soft pt-2.5">
                <span className="text-[9px] text-ink-4">{tr("它用了")}</span>
                {citesRevision(latest.cited) && (
                  <span className="ml-2 rounded border border-accent/35 bg-accent/5 px-2 py-0.5 font-mono text-[9px] text-accent">
                    {tr("上一版的判断")}
                  </span>
                )}
                {citesEvidence(latest.cited) && (
                  <span
                    data-testid="cites-evidence-badge"
                    className="ml-2 rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3"
                  >
                    {tr("引证据")}
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
                    {tr("探针")}{" "}
                    {drawerEpisodeId ? (
                      <ChevronDown className="inline h-3 w-3" aria-hidden />
                    ) : (
                      <ChevronRight className="inline h-3 w-3" aria-hidden />
                    )}
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
            {tr("还没有回答 — 让 Selene 先查一轮,或自己写第一版。")}
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
          <span className="text-ink-3">
            {showHistory ? (
              <ChevronDown className="h-3 w-3" aria-hidden />
            ) : (
              <ChevronRight className="h-3 w-3" aria-hidden />
            )}
          </span>
          <span className="text-[11px] text-ink-2">
            {tr("更早的回答 ·")} {earlier.length} {tr("版")}
          </span>
          <span className="text-[10px] text-pos">
            {citedByLatest.size > 0 ? tr("✓ v{seq} 用了它", { seq: latest?.seq ?? "" }) : ""}
          </span>
        </button>
      )}

      {/* ⑤ 结案轻条:一行三键(功能=Approve/Redirect/Park,状态机不变) */}
      {isOpen && (
        <div
          data-testid="section-actions"
          className="mt-3 flex items-center gap-2 flex-wrap rounded-lg border border-border bg-surface-2 px-4 py-2"
        >
          <span className="text-[10.5px] text-ink-3">{tr("这个问题查清楚了?")}</span>
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
            {tr("自己写终版")}
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
            {tr("换个问法")}
          </Button>
          {/* 立为支柱:仅当已有回答时显示(桥的入口;无 revision 无处可提) */}
          {(detail.revisions?.length ?? 0) > 0 && (
            <Button
              size="sm"
              variant="outline"
              data-testid="promote-btn"
              onClick={() => setPromoteOpen(true)}
            >
              {tr("立为支柱")}
            </Button>
          )}
          {parkConfirmOpen ? (
            <>
              <span className="text-[10.5px] text-ink-3">
                {tr("先放一放 = 停车,之后可在问题列表的「已停放」里恢复")}
              </span>
              <Button size="sm" variant="ghost" data-testid="park-confirm" onClick={runPark}>
                {tr("确认停放")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                data-testid="park-cancel"
                onClick={() => setParkConfirmOpen(false)}
              >
                {tr("再想想")}
              </Button>
            </>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              data-testid="park-btn"
              onClick={() => setParkConfirmOpen(true)}
            >
              {tr("先放一放")}
            </Button>
          )}
          {redirectOpen && (
            <div className="flex w-full items-center gap-2">
              <input
                data-testid="redirect-input"
                value={redirectTitle}
                onChange={(e) => setRedirectTitle(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && runSucceed()}
                placeholder={tr("新问题的问法…")}
                className="min-w-0 flex-1 rounded border border-border bg-surface-1 px-2.5 py-1.5 text-xs text-ink placeholder:text-ink-4 focus:border-accent/50 focus:outline-none"
              />
              <Button
                size="sm"
                disabled={!redirectTitle.trim() || redirecting}
                data-testid="redirect-submit"
                onClick={runSucceed}
              >
                {redirecting ? tr("接续中…") : tr("以新问题接续")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setRedirectOpen(false)}
              >
                {tr("取消")}
              </Button>
            </div>
          )}
        </div>
      )}

      {/* 已结案:重开行(状态轴可逆;状态本身已在标题卡显示) */}
      {!isOpen && (
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-border bg-surface-2 px-4 py-2">
          <span className="text-[10.5px] text-ink-3">{tr("想继续查这个问法?")}</span>
          <Button
            size="sm"
            variant="ghost"
            data-testid="reopen-btn"
            onClick={runReopen}
          >
            {tr("重开")}
          </Button>
        </div>
      )}
      {actionError && <p className="mt-2 text-xs text-destructive">{tr(actionError)}</p>}

      {/* 桥:立为支柱成功反馈(旧支柱退不退役仍是用户的另一颗按钮) */}
      {promoted && (
        <p className="mt-2 text-xs text-pos" data-testid="promote-success">
          {tr("已立为支柱")} {promoted.pillar_id} · mission{" "}
          <Link
            className="text-accent hover:underline"
            href={`/research/missions?id=${encodeURIComponent(promoted.mission_id)}`}
          >
            {promoted.mission_id}
          </Link>
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
                    {tr("回放")} {asOfReplayDate(rev.rationale)}
                  </span>
                )}
                <span className="ml-auto font-mono text-[10px] text-ink-4">
                  {formatDay(rev.created_at)} {formatTime(rev.created_at)}
                </span>
              </div>
              <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-ink-2">
                {rev.content}
              </p>
              <RevisionStructured
                structured={rev.structured}
                testIdPrefix="revision-structured-history"
              />
              <div className="mt-2 border-t border-border-soft pt-2 text-[9px] text-ink-4">
                {rev.cited.length > 0
                  ? tr("它用了 {n} 条记录", { n: rev.cited.length })
                  : tr("无 — 第一版")}
                {citesEvidence(rev.cited) && (
                  <span
                    data-testid="cites-evidence-badge-history"
                    className="ml-2 rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3"
                  >
                    {tr("引证据")}
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
                    {tr("探针")}{" "}
                    {drawerEpisodeId ? (
                      <ChevronDown className="inline h-3 w-3" aria-hidden />
                    ) : (
                      <ChevronRight className="inline h-3 w-3" aria-hidden />
                    )}
                  </button>
                )}
                {citedByLatest.has(rev.id) && (
                  <span className="ml-2 text-pos">{tr("✓ 被 v")}{latest?.seq} {tr("引用")}</span>
                )}
              </div>
            </div>
          ))}
          <div className="rounded-lg border border-border bg-surface-1 p-3.5">
            <p className="text-[11px] text-ink-3">{tr("时间线")}</p>
            <ol className="mt-2 space-y-1.5">
              {detail.events.map((e) => (
                <li key={e.id} className="text-xs text-ink-2">
                  <span className="font-mono text-[10px] text-ink-4">
                    {e.occurred_at.slice(0, 16).replace("T", " ")}
                  </span>
                  <span className="ml-2">
                    {tr(EVENT_LABEL[e.kind] ?? e.kind)}
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
    </div>
  );
}

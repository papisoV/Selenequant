"use client";

import Link from "next/link";
import { SkeletonRows } from "@/components/shared/skeleton";
import { useApiPage } from "@/lib/hooks/use-api-page";
import type { MissionSummary } from "./missions-types";
import type {
  AgentActivityEvent,
  AgentActivityResponse,
  NeedsYouCardItem,
  TodayResponse,
} from "./today-types";
import { capNeedsYou, eventLine, relTime } from "./today-parts";
import { errorHintText } from "./strategy-parts";
import type { SensorSnapshot } from "@/lib/api/research-graph";
import { useUiText } from "@/components/i18n/use-ui-text"
/**
 * TODAY 页 — 注意力分配器(slug "today",/research/today)。
 * 顶条七环研究生命周期 + 四段固定不重排:LAST HANDOFF / NEEDS YOU / SELENE IS WORKING / WORLD CHANGED。
 * 全部字段读 B4 GET /api/v1/research/today 投影,页面零生成物;
 * 拍板是门不是菜单:去 Mission 页完整证据面前决策。
 */

const GOLD = "text-gold";

/** 七环状态条 — 研究生命周期 Question→…→Memory,从 today 投影推导;接不通的环显示 — */
function ChainStrip({ data, memCount }: { data: TodayResponse; memCount: number | null }) {
  const tr = useUiText();
  const working = data.working ?? [];
  const ny = data.needs_you ?? [];
  const world = data.world_changed ?? [];
  // 决策环数的是"要裁决几件"(折叠前的行数),不是窗口里能放几张卡。
  const needsCount =
    data.needs_you_total ?? ny.reduce((n, m) => n + 1 + (m.duplicates ?? 0), 0);
  const openCount = needsCount + working.length;
  // "问句"与"使命"是两个不同的东西(问题锚 vs 承载它的 mission),必须读两张
  // 表。曾经两环共用 openCount,七环里出现两个一模一样的数字,看着像计数坏了。
  // 后端没给 counts 时显 —,不回退到 mission 计数 —— 回退正好会重新制造那个
  // 假象。
  const openQuestions = data.counts?.open_questions ?? null;
  const rings: { label: string; n: number | null }[] = [
    { label: "问句", n: openQuestions },
    { label: "使命", n: openCount },
    { label: "调查", n: working.filter((m) => m.phase === "investigation").length },
    { label: "实验", n: working.filter((m) => m.phase === "validation").length },
    { label: "决策", n: needsCount },
    { label: "结果", n: world.filter((m) => m.phase === "resolved").length },
    { label: "记忆", n: memCount },
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 rounded-lg border border-border bg-surface-1 px-3.5 py-2">
      {rings.map((r, i) => (
        <span key={r.label} className="flex items-center gap-1.5">
          {i > 0 && <span className="text-[9px] text-ink-4">→</span>}
          <span className="flex items-baseline gap-1">
            <span className="text-[9px] tracking-[0.08em] text-ink-4">{tr(r.label)}</span>
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
  const tr = useUiText();
  const r = relTime(m.updated_at, Date.now(), tr);
  return (
    <div className="flex items-baseline gap-2 border-b border-border-soft py-[5px] text-[10.5px] last:border-b-0">
      <span className="text-ink-2">{tr(m.title)}</span>
      <span className="ml-auto shrink-0 text-[9px] text-ink-4" title={r.title}>
        {r.label}
      </span>
    </div>
  );
}

/** 与后端 PERSPECTIVES 一致的三视角口语名(与 question-surface 同一套) */
const PERSPECTIVE_LABEL: Record<string, string> = {
  empiricist: "经验派",
  recordist: "记录派",
  challenger: "挑战派",
};

const EVENT_KIND_LABEL: Record<string, string> = {
  investigate_perspective: "视角发言",
  investigate_finished: "调查完成",
  created: "立问",
  closed: "关闭",
  reopened: "重开",
  // mission_activity 侧的 kind。缺一张表的话这行会直接渲染裸 kind 字符串,
  // 用户读到 "worker_analysis" —— 等于没渲染。
  mission_opened: "立 Mission",
  phase_transition: "阶段转移",
  outcome_recorded: "Outcome 入账",
  user_note: "你的留言",
  worker_started: "开始调查",
  worker_analysis: "分析已出",
  worker_failed: "本轮失败",
  worker_budget_exhausted: "预算用尽",
  evidence_appended: "证据入账",
  experiment_completed: "实验完成",
  pa_diagnosis_record: "PA 诊断",
  pa_followup: "PA 跟进",
  revisit_due: "复访到期",
  drift_check: "漂移检查",
  revalidate_complete: "复验完成",
  subject_reclassified: "标的归类",
  pillar_opened: "支柱开启",
  status_set: "状态设定",
};

function elapsedLabel(startedAt: string, now = Date.now()): string {
  const t = new Date(startedAt).getTime();
  if (Number.isNaN(t)) return "—";
  const sec = Math.max(0, Math.floor((now - t) / 1000));
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  return `${Math.floor(sec / 3600)}h${Math.floor((sec % 3600) / 60)}m`;
}

/** 正在跑的调查会 — 每个视角一行,回答"agent 现在具体在哪一步" */
function RunningInvestigationRow({
  run,
}: {
  run: AgentActivityResponse["running"][number];
}) {
  const tr = useUiText();
  const started = relTime(run.started_at);
  return (
    <Link
      href={`/research/questions?q=${encodeURIComponent(run.question_id)}`}
      className="flex items-baseline gap-2 border-b border-border-soft py-[5px] text-[10.5px] last:border-b-0 hover:text-ink"
      title={tr("开始于 {time}", { time: started.title })}
    >
      <span
        className="mt-[3px] h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-accent"
        aria-hidden
      />
      <span className="min-w-0 truncate text-ink-2">{run.question_title}</span>
      <span className="num ml-auto shrink-0 text-[9px] text-ink-4">
        {tr("调查会 · {t}", { t: elapsedLabel(run.started_at) })}
      </span>
    </Link>
  );
}

/** 最近动作流 — 一行一条,视角发言带摘要;点击回到活动所属的对象 */
function ActivityRow({ ev }: { ev: AgentActivityEvent }) {
  const when = relTime(ev.occurred_at);
  const p = ev.detail.perspective;
  const perspective = typeof p === "string" ? PERSPECTIVE_LABEL[p] ?? p : null;
  const kindLabel = EVENT_KIND_LABEL[ev.kind] ?? ev.kind;
  const excerpt =
    typeof ev.detail.excerpt === "string" && ev.detail.excerpt
      ? ev.detail.excerpt.replace(/\s+/g, " ")
      : null;
  // 两路来源回不同的详情页;mission 侧没有 question_id,链到 mission 详情
  const href = ev.mission_id
    ? `/research/missions?id=${encodeURIComponent(ev.mission_id)}`
    : ev.question_id
      ? `/research/questions?q=${encodeURIComponent(ev.question_id)}`
      : null;
  // 拆成 body + 两种外壳,而不是 `const Tag = href ? "a" : "div"`:
  // next/link 的 href 类型比 <a> 窄,联合标签会让 props 推断失真。
  const body = (
    <>
      <span className="flex items-baseline gap-2">
        <span
          className={`shrink-0 rounded px-1 py-px text-[9px] ${
            ev.detail.status === "failed"
              ? "bg-surface-4 text-ink-4"
              : "bg-surface-4 text-ink-3"
          }`}
        >
          {perspective ?? kindLabel}
        </span>
        <span className="min-w-0 truncate text-ink-2">
          {ev.question_title ?? ev.question_id ?? ev.mission_id}
        </span>
        <span className="num ml-auto shrink-0 text-[9px] text-ink-4">{when.label}</span>
      </span>
      {excerpt && (
        <span className="truncate pl-1 text-[9.5px] text-ink-4">{excerpt}</span>
      )}
    </>
  );
  const cls =
    "flex flex-col gap-0.5 border-b border-border-soft py-[5px] text-[10.5px] last:border-b-0 hover:text-ink";
  return href ? (
    <Link href={href} className={cls} title={when.title}>
      {body}
    </Link>
  ) : (
    <div className={cls} title={when.title}>
      {body}
    </div>
  );
}

export function TodayView({
  data,
  loading,
  errorStatus,
  memCount,
  activity,
  sensor,
}: {
  data: TodayResponse | null;
  loading: boolean;
  errorStatus?: number;
  /** 记忆环真值:GET /research/graph/memory 的 knowledge+clues;取不到时 null 显 — */
  memCount?: number | null;
  /** Selene 实时活动聚合;取不到时 null(该段退回只看 mission phase) */
  activity?: AgentActivityResponse | null;
  /** ① 传感器快照;取不到时 null(状态条不渲染 —— 不编造"没开") */
  sensor?: SensorSnapshot | null;
}) {
  const tr = useUiText()

  if (errorStatus !== undefined) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-ink-3">
        {errorHintText(errorStatus, tr)}
      </div>
    );
  }
  if (loading && !data) {
    return <SkeletonRows rows={6} />;
  }
  if (!data) return null;

  const needsYou = data.needs_you ?? [];
  const working = data.working ?? [];
  const world = data.world_changed ?? [];
  const h = data.last_handoff;
  // 实时活动:跑了多久/最近说了什么/心跳在不在跑。取不到聚合端点时三段一起退场,
  // 不编造"正在工作"的假象 —— mission phase 仍是可信的回退读面。
  const running = activity?.running ?? [];
  const recentActivity = (activity?.recent_events ?? []).slice(0, 5);
  const hb = activity?.heartbeat;

  // 安静态塌缩:needs_you 空 → NEEDS YOU 段收成安静文案(空间位置表达语义)
  const quiet = needsYou.length === 0;
  // 容量规则:先按「标的 + 问句」折叠,再最多突出 3 组,其余收溢出行
  const { shown: nyShown, overflow: nyOverflow, totalGroups: nyGroups } = capNeedsYou(needsYou);
  // 段头数字读全量行数(折叠前),与下方"还有 N 组"分开口径:
  // "26 件待拍板"是人要裁决的件数,"3 组"是窗口里能看见的卡片数。
  const nyTotal = data.needs_you_total ?? needsYou.reduce((n, m) => n + 1 + (m.duplicates ?? 0), 0);

  const hWhen = h ? relTime(h.occurred_at, Date.now(), tr) : null;
  // detail.note 是人话真值(如 "Approve REVALIDATE — 修正配置回声明意图后重验证");
  // kind 是内部状态机词(phase_transition),不进用户面
  const hNote =
    h && h.detail && typeof h.detail.note === "string" ? (h.detail.note as string) : null;

  return (
    <div className="pb-4">
      <div className="mx-auto flex max-w-[1120px] flex-col gap-2.5 px-4 pb-6 pt-3.5">
        {/* topline · 灵魂问句 */}
        <div className="flex items-center gap-2.5 text-[10px] text-ink-3">
          <h1 className="text-[11px] font-medium text-ink-2">{tr("TODAY · 现在什么值得我关注?")}</h1>
        </div>

        {/* 七环状态条 — 研究生命周期计数 */}
        <ChainStrip data={data} memCount={memCount ?? null} />

        {/* ① LAST HANDOFF — 三行微叙事 */}
        <section className="overflow-hidden rounded-lg border border-border bg-surface-1">
          <SectionHead title="LAST HANDOFF" hint={tr("上次交接之后发生了什么")} count={hWhen ? tr("最近一次交接 · {label}", { label: hWhen.label }) : undefined} />
          {h ? (
            <div className="flex flex-col px-3.5 pb-2.5 pt-1">
              <div className="flex items-baseline gap-2.5 border-b border-border-soft py-1">
                <span className="w-[86px] shrink-0 text-[9px] tracking-[0.1em] text-ink-4">YOU DID</span>
                <span className="min-w-0 flex-1 text-[11px] leading-relaxed text-ink-2">
                  {hNote ? (
                    <b className="font-semibold text-ink">{hNote}</b>
                  ) : (
                    <span className="text-ink-4">—</span>
                  )}
                  {h.mission_title && (
                    <span className="ml-1.5 text-[10px] text-ink-4">· {h.mission_title}</span>
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
                    <span>{tr("研究线交接完毕,暂无待你决策事项")}</span>
                  ) : (
                    <b className="font-semibold text-ink">{nyTotal} {tr("件新 Mission 待你拍板")}</b>
                  )}
                </span>
              </div>
            </div>
          ) : (
            <div className="px-3.5 pb-3 pt-1 text-[10.5px] text-ink-4">{tr("尚无交接记录 — 一切从今天开始。")}</div>
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
          <SectionHead title="NEEDS YOU" count={quiet ? tr("0 件 · 安静") : `${nyTotal} ${tr("件待拍板")} · ${nyGroups} ${tr("组")}`} />
          {quiet ? (
            <div className="flex flex-col gap-0.5 px-3.5 pb-3 pt-2">
              <span className="text-[11px] text-ink-3">{tr("现在不需要你 — 无待拍板事项。")}</span>
              {working.length > 0 && (
                <Link
                  href={`/research/missions?id=${working[0].id}`}
                  className="w-fit text-[10px] text-ink-4 underline decoration-border-soft underline-offset-2 transition-colors hover:text-ink-2"
                >
                  {tr("Selene 正在调查")} {tr(working[0].title)} {tr("— 去看看进展 →")}
                </Link>
              )}
            </div>
          ) : (
            <div className="flex flex-col gap-2.5 px-3.5 py-3">
              {nyShown.map((m) => (
                <NeedsYouCard key={m.id} m={m} />
              ))}
              {nyOverflow > 0 && (
                <div className="text-[9.5px] text-ink-4">
                  {tr("还有 {n} 组在", { n: nyOverflow })}{" "}
                  {/* 曾写「研究线 Portfolio」——那个路由不存在(manifest 里
                      portfolio 已 absorbedBy: "pos",指向持仓页),且这句没有
                      链接,用户既点不进去也找不到。完整列表就在 Mission 一览。 */}
                  <Link
                    href="/research/missions"
                    className="text-ink-3 underline decoration-border-soft underline-offset-2 transition-colors hover:text-ink-1"
                  >
                    {tr("研究线 · Mission 一览")}
                  </Link>
                  {" "}
                  {tr("查看。")}
                </div>
              )}
            </div>
          )}
        </section>

        {/* ③ SELENE IS WORKING — 实时调查会 + 最近动作,禁百分比 */}
        <section className="overflow-hidden rounded-lg border border-border bg-surface-1">
          <SectionHead
            title="SELENE IS WORKING"
            count={
              running.length > 0
                ? tr("{n} 个调查会进行中", { n: running.length })
                : tr("{n} 项进行中", { n: working.length })
            }
          />
          <div className="flex flex-col gap-0.5 px-3.5 pb-1 pt-2">
            {running.map((r) => (
              <RunningInvestigationRow key={r.question_id} run={r} />
            ))}
            {running.length === 0 &&
              (working.length === 0 ? (
                <>
                  <span className="text-[11px] text-ink-3">{tr("办公室很安静,因为一切在等你。")}</span>
                  {nyTotal > 0 && (
                    <span className="text-[9.5px] leading-relaxed text-ink-4">{nyTotal} {tr("项调查在等你的决定。")}</span>
                  )}
                </>
              ) : (
                working.map((m) => <MissionRowLite key={m.id} m={m} />)
              ))}
          </div>
          {recentActivity.length > 0 && (
            <div className="border-t border-border-soft px-3.5 pb-2 pt-1.5" data-testid="recent-activity">
              <div className="mb-0.5 text-[9px] tracking-[0.08em] text-ink-4">
                {tr("最近动作")}
              </div>
              {recentActivity.map((ev) => (
                <ActivityRow key={`${ev.source}-${ev.id}`} ev={ev} />
              ))}
            </div>
          )}
          {hb?.enabled && (
            <div className="border-t border-border-soft px-3.5 pb-2 pt-1.5 text-[9.5px] text-ink-4">
              {tr("PA 心跳")}
              {hb.busy ? (
                <span className="ml-1.5 text-gold">{tr("诊断中")}</span>
              ) : (
                <span className="ml-1.5">
                  {tr("空闲 · 今日 {n} 次", { n: hb.attempts_today ?? 0 })}
                </span>
              )}
            </div>
          )}
          {sensor && (
            <SensorStrip sensor={sensor} />
          )}
        </section>

        {/* ④ WORLD CHANGED — 视觉最轻 */}
        <section className="overflow-hidden rounded-lg border border-border bg-surface-1">
          <SectionHead title="WORLD CHANGED" count={tr("{n} 行 · 最近变化", { n: world.length })} />
          <div className="flex flex-col px-3.5 pb-2.5 pt-1">
            {world.length === 0 ? (
              <span className="py-1 text-[10.5px] text-ink-4">{tr("世界安静 — 没有与你的研究线相关的变化。")}</span>
            ) : (
              world.map((m) => {
                const r = relTime(m.updated_at, Date.now(), tr);
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
function NeedsYouCard({ m }: { m: NeedsYouCardItem }) {
  const tr = useUiText()
  const r = relTime(m.updated_at, Date.now(), tr);
  const dups = m.duplicates ?? 0;
  return (
    <div className="rounded-lg border border-gold/26 bg-surface-2 p-3.5">
      {/* 事件层:第一眼 */}
      <div className="flex items-center gap-2.5">
        <span className="h-2 w-2 shrink-0 rounded-sm bg-gold shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-gold)_14%,transparent)]" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 tracking-[-0.01em]">
            <span className={`text-[13.5px] font-semibold ${GOLD}`}>{tr(m.title)}</span>
            <span className="rounded border border-gold/28 bg-gold/12 px-1.5 py-px text-[9px] font-medium text-gold">
              {tr("待拍板")}
            </span>
            {dups > 0 && (
              <span
                className="num rounded border border-border-soft bg-surface-4 px-1.5 py-px text-[9px] text-ink-3"
                title={tr("同一问句另有 {dups} 条重复开案(共 {total} 条),已折叠成这一张卡", { dups, total: dups + 1 })}
              >
                ×{dups + 1}
              </span>
            )}
          </div>
          <div className="mt-0.5 text-[11px] leading-relaxed text-ink-2">
            {m.question} {tr("— 等你裁决")}
          </div>
        </div>
        <Link
          href={`/research/missions?id=${m.id}`}
          className="shrink-0 rounded-md bg-gold px-3 py-[5px] text-[10.5px] font-semibold text-[#161006] transition-colors hover:bg-[color-mix(in_srgb,var(--color-gold)_88%,#fff)]"
        >
          {tr("去 Mission 页拍板 →")}
        </Link>
      </div>

      {/* 细节层:第二眼扫读 */}
      <div className="mt-2.5 border-t border-border-soft pt-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded bg-surface-4 px-1.5 py-px text-[9px] text-ink-3">{m.subject_type}</span>
          <span className="num text-[9.5px] text-ink-4">{m.id}</span>
          {dups > 0 && (
            <Link
              href="/research/missions"
              className="text-[9.5px] text-ink-4 underline decoration-border-soft underline-offset-2 transition-colors hover:text-ink-2"
            >
              {tr("另有 {n} 条同问句开案", { n: dups })}
            </Link>
          )}
          <span className="num ml-auto text-[9px] text-ink-4" title={r.title}>
            {r.label}
          </span>
        </div>
      </div>
    </div>
  );
}

/** ① 传感器状态条 — 三种状态从不沉默:开着没发现 / 开着有发现 / 未启用。
 * sensor==null(接口取不到)时整条不渲染 —— 不编造"没开"。 */
function SensorStrip({ sensor }: { sensor: SensorSnapshot }) {
  const tr = useUiText();
  const enabled = sensor.settings?.enabled ?? false;
  const obs = sensor.observations ?? [];
  type Parsed = {
    symbol?: string;
    interval?: string;
    move_pct?: number;
    direction?: string;
    threshold_pct?: number;
  };
  const rows = obs.map((o) => {
    let p: Parsed = {};
    try {
      const raw = typeof o.payload === "string" ? JSON.parse(o.payload) : o.payload;
      if (raw && typeof raw === "object") p = raw as Parsed;
    } catch {
      /* payload 损坏 — 该行只显示时间,不编造内容 */
    }
    const r = relTime(o.created_at, Date.now(), tr);
    return { id: o.id, p, r, qids: o.question_ids ?? [] };
  });
  if (!enabled) {
    return (
      <div className="border-t border-border-soft px-3.5 pb-2 pt-1.5 text-[9.5px] text-ink-4">
        {tr("行情传感器")} · <span>{tr("未启用 — 设置页「研究循环」里打开后,这里显示它扫到的异动")}</span>
      </div>
    );
  }
  const shown = rows.slice(0, 5);
  return (
    <div className="border-t border-border-soft px-3.5 pb-2 pt-1.5 text-[9.5px]" data-testid="sensor-strip">
      <div className="flex items-baseline gap-1.5 text-ink-4">
        <span>{tr("行情传感器")}</span>
        <span className="text-ink-3">
          {tr("每 {n} 分钟扫一次", { n: sensor.settings?.interval_minutes ?? "?" })}
        </span>
        <span className="num ml-auto">
          {obs.length > 0 ? tr("最近 {n} 条异动", { n: obs.length }) : tr("开着的,还没发现异动")}
        </span>
      </div>
      {shown.map(({ id, p, r, qids }) => (
        <div key={id} className="mt-0.5 flex items-baseline gap-2">
          <span className="num text-ink-2">{p.symbol ?? "?"}</span>
          <span className="text-ink-4">{p.interval ?? "?"}</span>
          <span className={`num ${p.direction === "down" ? "text-neg" : "text-pos"}`}>
            {p.direction === "down" ? "−" : "+"}
            {p.move_pct != null ? Math.abs(p.move_pct).toFixed(2) : "?"}%
          </span>
          {qids.length > 0 && (
            <Link
              href={`/research/missions?id=${qids[0]}`}
              className="text-ink-4 underline decoration-border-soft underline-offset-2 transition-colors hover:text-ink-2"
            >
              {tr("已开案 →")}
            </Link>
          )}
          <span className="num ml-auto text-ink-4" title={r.title}>
            {r.label}
          </span>
        </div>
      ))}
      {obs.length > shown.length && (
        <div className="mt-0.5 text-ink-4">
          {tr("另有 {n} 条更早的", { n: obs.length - shown.length })}
        </div>
      )}
    </div>
  );
}

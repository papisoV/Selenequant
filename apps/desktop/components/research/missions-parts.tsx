"use client";

import { useState } from "react";
import type { ActivityRow, MissionRow } from "./missions-types";
import { useUiText } from "@/components/i18n/use-ui-text"
import { identityTranslate, type Translate } from "@/lib/i18n/types"

const PHASE_LABEL: Record<string, string> = {
  investigation: "调查",
  needs_input: "拍板",
  validation: "验证",
  resolved: "结案",
  parked: "停放",
};

const PHASE_FULL: Record<string, string> = {
  investigation: "调查中",
  needs_input: "待拍板",
  validation: "验证中",
  resolved: "已结案",
  parked: "已停放",
};

/** 状态徽章 — 列表 & 详情头部共用;resolved/parked 中性态,其余 accent */
export function PhaseBadge({ phase }: { phase: string }) {
  const tr = useUiText();
  const neutral = phase === "resolved" || phase === "parked";
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded border px-1.5 py-0.5 text-[10px] font-medium ${
        neutral ? "border-border bg-surface-3 text-ink-3" : "border-accent/60 bg-accent/10 text-accent"
      }`}
    >
      <span className={`h-[5px] w-[5px] rounded-full ${neutral ? "bg-ink-4" : "bg-accent"}`} />
      {tr(PHASE_FULL[phase] ?? phase)}
    </span>
  );
}

/* ---------- StateStrip(头部状态带) ---------- */

export function StateStrip({
  phase,
  order,
}: {
  phase: string;
  order: readonly string[];
}) {
  const tr = useUiText()

  const idx = order.indexOf(phase);
  return (
    <div className="mt-2.5 flex items-center border-t border-border-soft pt-2.5">
      {order.map((p, i) => {
        const done = i < idx;
        const current = p === phase;
        return (
          <span key={p} className="contents">
            {i > 0 && <span className="mx-2 h-px w-[34px] shrink-0 bg-border" />}
            <span className={`flex items-center gap-1.5 text-[10px] ${current ? "font-medium text-accent" : done ? "text-ink-3" : "text-ink-4"}`}>
              <span
                className={`h-[7px] w-[7px] rounded-full border-[1.5px] ${
                  current
                    ? "border-accent bg-accent/30 shadow-[0_0_0_3px] shadow-accent/12"
                    : done
                      ? "border-ink-3 bg-ink-3"
                      : "border-ink-4"
                }`}
              />
              {tr(PHASE_LABEL[p] ?? p)}
            </span>
          </span>
        );
      })}
      {phase === "parked" && (
        <span className="ml-2 rounded bg-surface-4 px-1.5 py-0.5 text-[9px] text-ink-3">{tr("已停放(侧门)")}</span>
      )}    </div>
  );
}

/* ---------- NowBar(现在时锚 · 呼吸点) ---------- */

export function NowBar({
  who,
  sentence,
  anchor,
  breathe,
}: {
  who: "selene" | "user" | "done";
  sentence: React.ReactNode;
  anchor: string;
  breathe: boolean;
}) {
  const color = who === "user" ? "text-gold" : who === "done" ? "text-pos" : "text-accent";
  return (
    <div className="flex items-center gap-2.5 rounded-lg border border-border bg-surface-2 px-3 py-2">
      <span
        className={`h-[7px] w-[7px] shrink-0 rounded-full bg-current ${color} ${breathe ? "animate-breathe motion-reduce:animate-none" : ""}`}
      />
      <span className="text-[11px] text-ink">{sentence}</span>
      <span className="num ml-auto shrink-0 text-[9px] text-ink-4">{anchor}</span>
    </div>
  );
}

/* ---------- MissionPulse(活动流窄带 · 只读回放) ---------- */

/** Worker 建议动作 → 中文标签(与 mission_worker 的 recommendation 取值对齐) */
const REC_LABEL: Record<string, string> = {
  approve: "进入验证",
  more_evidence: "继续调查",
  redirect: "改方向",
  park: "先停放",
};

/** worker_failed 的 reason → 人话 */
const FAIL_REASON: Record<string, string> = {
  llm_unavailable: "模型没有响应",
  unparseable_response: "回复不是可解析的 JSON",
  worker_error: "本轮执行出错",
};

const ACTOR_STYLE: Record<string, string> = {
  system: "bg-surface-4 text-ink-3",
  selene: "bg-accent/12 text-accent",
  user: "bg-gold/12 text-gold",
};

function actSummary(a: ActivityRow, tr: Translate = identityTranslate): React.ReactNode {
  const d = (a.detail ?? {}) as Record<string, unknown>;
  switch (a.kind) {
    case "mission_opened":
      return <><b>{tr("Mission 开启")}</b>:{String(d.question ?? "")}</>;
    case "phase_transition": {
      const to = tr(PHASE_LABEL[String(d.to)] ?? String(d.to ?? ""));
      const action = typeof d.action === "string" ? d.action : "";
      return (
        <>
          {a.actor === "user" ? <b>{tr("决策:{action}", { action })}</b> : <b>{tr("状态转移")}</b>} → {to}
          {typeof d.note === "string" && d.note ? <span className="text-ink-3"> · {d.note}</span> : null}
        </>
      );
    }
    case "outcome_recorded":
      return <><b>{tr("Outcome 入账")}</b>:{String(d.verdict ?? "—")}</>;
    case "user_note":
      return <><b>{tr("你的留言")}</b>:{String(d.note ?? "")}</>;
    // Worker 的产出是这个管线唯一"给人看的结论"—— 不渲染的话,界面只剩
    // 一行 "worker_analysis",用户看到的是"研究在跑"，却看不到它到底说了
    // 什么、要你决定什么。proposal 后端已经拼好(动作 + 理由 + 信号)。
    case "worker_analysis": {
      const rec = typeof d.recommendation === "string" ? d.recommendation : "";
      const reason = typeof d.reason === "string" ? d.reason : "";
      const signal = typeof d.signal === "string" ? d.signal : "";
      const evidenceError =
        typeof d.evidence_error === "string" ? d.evidence_error : "";
      return (
        <>
          <b>{tr("分析已出")}</b>
          {rec ? <> · {tr("建议 {action}", { action: tr(REC_LABEL[rec] ?? rec) })}</> : null}
          {reason ? <span className="text-ink-3"> · {reason}</span> : null}
          {signal ? <span className="text-ink-4"> · {tr("信号 {value}", { value: signal })}</span> : null}
          {evidenceError ? (
            <span className="text-neg"> · {tr("证据未入账")}:{evidenceError}</span>
          ) : null}
        </>
      );
    }
    case "worker_failed": {
      const why = typeof d.reason === "string" ? d.reason : "";
      return (
        <>
          <b className="text-neg">{tr("本轮调查失败")}</b>
          <span className="text-ink-3"> · {tr(FAIL_REASON[why] ?? why)}</span>
        </>
      );
    }
    case "worker_budget_exhausted": {
      const rounds = typeof d.rounds === "number" ? d.rounds : null;
      return (
        <>
          <b>{tr("本轮调查预算用完")}</b>
          {rounds !== null ? <span className="text-ink-3"> · {tr("已跑 {n} 轮", { n: rounds })}</span> : null}
          <span className="text-ink-3"> · {tr("在任务上继续调查可再授权")}</span>
        </>
      );
    }
    case "worker_started": {
      const rounds = typeof d.rounds === "number" ? d.rounds : null;
      return (
        <>
          <b>{tr("开始调查")}</b>
          {rounds !== null ? <span className="text-ink-4"> · {tr("第 {n} 轮", { n: rounds + 1 })}</span> : null}
        </>
      );
    }
    // 下面四类同样会进这条流。其中 revisit_due 是**要人动手**的提醒,
    // 显示成 "revisit_due" 等于把"该复核了"藏起来。
    case "revisit_due": {
      const title = typeof d.question_title === "string" ? d.question_title : "";
      const hint = typeof d.hint === "string" ? d.hint : "";
      return (
        <>
          <b className="text-warn">{tr("复访到期")}</b>
          {title ? (
            <span className="text-ink-2"> · {title}</span>
          ) : null}
          {hint ? <span className="text-ink-3"> · {hint}</span> : null}
        </>
      );
    }
    case "experiment_completed": {
      const kind = typeof d.experiment_kind === "string" ? d.experiment_kind : "";
      const summary = typeof d.summary === "string" ? d.summary : "";
      return (
        <>
          <b>{tr("实验跑完")}</b>
          {kind ? <span className="text-ink-3"> · {kind}</span> : null}
          {summary ? <span className="text-ink-4"> · {summary}</span> : null}
        </>
      );
    }
    case "pa_diagnosis_record": {
      const interval = typeof d.interval === "string" ? d.interval : "";
      return (
        <>
          <b>{tr("PA 诊断已入账")}</b>
          {interval ? <span className="text-ink-3"> · {interval}</span> : null}
        </>
      );
    }
    case "pa_followup": {
      const question = typeof d.question === "string" ? d.question : "";
      return (
        <>
          <b>{tr("你就 PA 记录追问")}</b>
          {question ? <span className="text-ink-2"> · {question}</span> : null}
        </>
      );
    }
    default:
      return <>{a.kind}{typeof d.note === "string" ? ` · ${d.note}` : ""}</>;
  }
}

export function MissionPulse({ activity }: { activity: ActivityRow[] }) {
  const tr = useUiText()

  const [collapsed, setCollapsed] = useState(false);
  const showAll = activity.length <= 4 || !collapsed;
  const shown = showAll ? activity : activity.slice(-4);
  const canCollapse = activity.length > 4;
  return (
    <div className="rounded-lg border border-border bg-surface-1 px-3.5 py-2">
      <div className="mb-1 flex items-baseline gap-2.5">
        <span className="text-[11px] font-semibold tracking-wider text-ink">Activity Pulse</span>
        <span className="text-[10px] text-ink-3">{tr("只读时间线 · 全程可回放")}</span>
      </div>
      <div className="flex flex-col">
        {shown.map((a) => (
          <div key={a.id} className="flex items-baseline gap-2 py-0.5">
            <span className={`shrink-0 rounded px-1 text-[9px] font-medium ${ACTOR_STYLE[a.actor] ?? ACTOR_STYLE.system}`}>{a.actor}</span>
            <span className="min-w-0 flex-1 truncate text-[10.5px] leading-relaxed text-ink-2">{actSummary(a, tr)}</span>
            <span className="num shrink-0 text-[9px] text-ink-4">{fmtTime(a.occurred_at)}</span>
          </div>
        ))}
        {shown.length === 0 && <div className="py-1 text-[10px] text-ink-4">{tr("暂无活动记录")}</div>}
      </div>
      {canCollapse && (
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          className="mt-1 text-[10px] text-accent hover:underline"
        >
          {collapsed ? tr("展开全部 {n} 条", { n: activity.length }) : tr("收起")}
        </button>
      )}
    </div>
  );
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mi}`;
}

/* ---------- NextZone(未来:状态机四动作 / idle / 结案连锁) ---------- */

const NEXT_HINT: Record<string, string> = {
  approve: "进入验证执行",
  more_evidence: "回调查",
  redirect: "回调查",
  park: "归档不再消耗注意力",
  resolve: "结论入账,进 WORLD CHANGED",
};

type DecisionAction = "approve" | "more_evidence" | "redirect" | "park" | "resolve";

export function NextZone({
  mission,
  busy,
  err,
  onDecide,
  redirectOpen,
  note,
  setNote,
}: {
  mission: MissionRow;
  busy: boolean;
  err: string | null;
  onDecide: (a: DecisionAction) => void;
  redirectOpen: boolean;
  note: string;
  setNote: (v: string) => void;
}) {
  const tr = useUiText()

  const phase = mission.phase;
  if (phase === "needs_input") {
    const actions: Array<{ key: DecisionAction; label: string; primary?: boolean; quiet?: boolean }> = [
      { key: "approve", label: "批准建议 · Approve", primary: true },
      { key: "more_evidence", label: "要更多证据 · More evidence" },
      { key: "redirect", label: "重新定向 · Redirect" },
      { key: "park", label: "停放 · Park", quiet: true },
    ];
    return (
      <section className="rounded-lg border border-border bg-surface-2 px-3.5 py-2.5">
        <div className="mb-2 flex items-baseline gap-2.5">
          <span className="text-[11px] font-semibold tracking-wider text-ink">{tr("NEXT · 你的决定")}</span>
          <span className="text-[10px] text-ink-3">{tr("动作来自状态机合法转移")}</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {actions.map((a) => (
            <button
              key={a.key}
              type="button"
              disabled={busy}
              onClick={() => onDecide(a.key)}
              className={
                a.primary
                  ? "rounded-md bg-accent px-3.5 py-1.5 text-[11px] font-medium text-accent-foreground transition-colors hover:bg-accent/85 disabled:opacity-50"
                  : a.quiet
                    ? "rounded-md border border-transparent px-3 py-1.5 text-[11px] text-ink-3 transition-colors hover:text-ink disabled:opacity-50"
                    : "rounded-md border border-border bg-surface-3 px-3 py-1.5 text-[11px] text-ink transition-colors hover:border-accent/50 disabled:opacity-50"
              }
            >
              {busy ? tr("提交中…") : tr(a.label)}
            </button>
          ))}
        </div>
        {redirectOpen && (
          <div className="mt-2 flex items-center gap-2">
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder={tr("新方向说明(必填)")}
              className="h-7 flex-1 rounded border border-border bg-surface-1 px-2 text-[11px] text-ink outline-none placeholder:text-ink-5 focus:border-accent/50"
            />
            <button
              type="button"
              disabled={busy || !note.trim()}
              onClick={() => onDecide("redirect")}
              className="rounded bg-accent px-3 py-1 text-[10px] text-accent-foreground disabled:opacity-50"
            >
              {tr("提交定向")}
            </button>
          </div>
        )}
        {err && <div className="mt-2 text-[10px] text-neg">{tr(err)}</div>}
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border-soft pt-1.5 text-[9px] text-ink-4">
          {actions.map((a) => (
            <span key={a.key}>
              {tr(a.label.split(" · ")[0])} → {tr(NEXT_HINT[a.key])}
            </span>
          ))}
        </div>
      </section>
    );
  }
  if (phase === "investigation" || phase === "validation") {
    return (
      <section className="rounded-lg border border-border bg-surface-2 px-3.5 py-2.5">
        <div className="mb-1.5 text-[11px] font-semibold tracking-wider text-ink">NEXT</div>
        {phase === "investigation" ? (
          <div className="flex items-center gap-2 text-[11px] text-ink-3">
            <span className="h-1.5 w-1.5 rounded-full bg-ink-4" />
            {tr("调查完成 → 这里变成你的决策点")}
          </div>
        ) : (
          <>
            {/* 这里曾经写着"Outcome 入账 → 自动结案 · 无需你的输入"。没有任何
                生产路径写得了 resolved —— 状态机允许 validation→resolved,但唯一
                的写者是 keltner_revalidate.py 那个一次性脚本。于是批准后的
                Mission 只能停在 validation 或在实验跑完后被推回 needs_input,
                永远走不到结案,而界面还在向你保证它会自己收尾。 */}
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[11px] text-ink-3">{tr("验证跑完由你结案:")}</span>
              <button
                type="button"
                disabled={busy}
                onClick={() => onDecide("resolve")}
                className="rounded-md bg-accent px-3.5 py-1.5 text-[11px] font-medium text-accent-foreground transition-colors hover:bg-accent/85 disabled:opacity-50"
              >
                {tr("结案 · Resolve")}
              </button>
            </div>
            <div className="mt-1.5 text-[10px] text-ink-4">
              {tr("结论写入 Outcome → Mission 进 WORLD CHANGED,不再占着 WORKING")}
            </div>
          </>
        )}
      </section>
    );
  }
  // resolved / parked:结案连锁 — done 全部从 mission 真值推导,不写死
  // outcome 真实 schema(keltner_revalidate.py 落库):verdict/declared_intent/in_sample/oos/method
  const outcome = (mission.outcome ?? {}) as Record<string, unknown>;
  const hasOutcome = Object.keys(outcome).length > 0;
  const resolved = mission.phase === "resolved";
  const settled = resolved || mission.phase === "parked";
  const ranExperiment = hasOutcome && (outcome.oos != null || outcome.oos_return != null);
  // Memory/Intelligence 沉淀后端暂无对应字段,不显示假状态;结论去向见详情页中栏
  const steps: Array<{ label: string; done: boolean }> = [
    { label: resolved ? "Mission resolved" : "Mission parked", done: settled },
    { label: "Experiment 已执行", done: ranExperiment },
    { label: "Outcome 已记录", done: hasOutcome },
  ];
  return (
    <section className="rounded-lg border border-border bg-surface-2 px-3.5 py-2.5">
      <div className="mb-2 flex items-baseline gap-2.5">
        <span className="text-[11px] font-semibold tracking-wider text-ink">{tr("结案连锁")}</span>
        <span className="text-[10px] text-ink-3">{tr("一次决策必须改变系统其他对象")}</span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {steps.map((s, i) => (
          <span key={s.label} className="contents">
            {i > 0 && <span className="text-[9px] text-ink-4">→</span>}
            <span
              className={`inline-flex items-center gap-1.5 rounded border px-2 py-[3px] text-[10px] ${
                s.done ? "border-pos/40 bg-surface-3 text-ink" : "border-border bg-surface-3 text-ink-4"
              }`}
            >
              {s.done ? <span className="text-pos">✓</span> : <span className="text-ink-4">○</span>}
              {tr(s.label)}
            </span>
          </span>
        ))}
      </div>
    </section>
  );
}

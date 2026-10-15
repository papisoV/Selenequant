"use client";

import { useMemo, useState } from "react";
import { apiPost } from "@/lib/api-client";
import type { MissionRow, ActivityRow } from "./missions-types";
import type { ThesisScorecard } from "./missions-thesis";
import { ThesisScorecard as ThesisScorecardPanel } from "./thesis-scorecard";
import { MissionPulse, NowBar, NextZone, PhaseBadge, StateStrip } from "./missions-parts";

const PHASE_ORDER = ["investigation", "needs_input", "validation", "resolved"] as const;

type DecisionAction = "approve" | "more_evidence" | "redirect" | "park";

const CONFIRM_MSG: Record<DecisionAction, string> = {
  approve: "已批准 — 进入验证执行,结果自动入账后结案",
  more_evidence: "已要求补充证据 — 回到调查,Selene 补完材料再推送",
  redirect: "已重新定向 — 调查按新方向展开",
  park: "已停放 — 不再消耗注意力,随时可唤醒",
};

function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

function fmtNum(n: unknown): string {
  return typeof n === "number" ? n.toLocaleString("en-US", { maximumFractionDigits: 3 }) : String(n ?? "—");
}

/** 小标签(card/block 标题) */
const BLK_LABEL = "text-[9px] font-semibold uppercase tracking-wider text-ink-4";

/** 从 context_json 提取声明 vs 实跑漂移面(后端 REVALIDATE 管线约定字段) */
function driftRows(mission: MissionRow): Array<[string, string, string]> {
  const ctx = (mission.context ?? {}) as Record<string, unknown>;
  const declared = (ctx.declared ?? {}) as Record<string, unknown>;
  const asRun = (ctx.as_run ?? ctx.actual ?? {}) as Record<string, unknown>;
  const rows: Array<[string, string, string]> = [];
  for (const k of ["symbol", "interval"]) {
    const d = declared[k];
    const a = asRun[k];
    if (d != null || a != null) {
      rows.push([k, String(d ?? "—"), String(a ?? "—")]);
    }
  }
  return rows;
}

/** Now Bar 现在时文案 + 轮到谁(accent=selene 干活 / gold=等你 / pos=结案) */
function nowInfo(mission: MissionRow): { who: "selene" | "user" | "done"; sentence: React.ReactNode; anchor: string } {
  const opened = fmtTime(mission.opened_at);
  switch (mission.phase) {
    case "investigation":
      return {
        who: "selene",
        sentence: <>Selene 正在调查 — <b>复盘证据材料,评估可裁决性</b></>,
        anchor: `${opened} 开案`,
      };
    case "needs_input":
      return {
        who: "user",
        sentence: <>等你拍板 — <b>批准建议进入验证</b>,或要更多证据 / 重新定向 / 停放</>,
        anchor: `${fmtTime(mission.updated_at)} 推送`,
      };
    case "validation":
      return {
        who: "selene",
        sentence: <>实验运行中 — <b>结果自动入账并结案</b>,无需你的输入</>,
        anchor: `${fmtTime(mission.updated_at)} 批准`,
      };
    default: // resolved | parked
      return {
        who: "done",
        sentence: mission.phase === "resolved"
          ? <>已结案 — <b>结论已可被下一次 Mission 引用</b></>
          : <>已停放 — <b>不再消耗注意力,可随时唤醒</b></>,
        anchor: mission.resolved_at ? `全程 ${opened} → ${fmtTime(mission.resolved_at)}` : opened,
      };
  }
}

interface Props {
  mission: MissionRow;
  activity: ActivityRow[];
  /** 认识论计分卡(GET /thesis 映射而来;无支柱/未加载为 null,面板隐藏) */
  scorecard?: ThesisScorecard | null;
  /** 决策成功后的页面刷新回调(父页 revalidate 列表+详情) */
  onChanged?: () => void;
}

export function MissionDetailView({ mission, activity, scorecard, onChanged }: Props) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [redirectOpen, setRedirectOpen] = useState(false);
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState<DecisionAction | null>(null);

  const outcome = (mission.outcome ?? {}) as Record<string, unknown>;
  const isNeedsInput = mission.phase === "needs_input";
  const now = useMemo(() => nowInfo(mission), [mission]);

  async function decide(action: "approve" | "more_evidence" | "redirect" | "park") {
    if (action === "redirect" && !redirectOpen) {
      setRedirectOpen(true);
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await apiPost(`/api/v1/research/missions/${mission.id}/decision`, {
        action,
        note: action === "redirect" ? note || undefined : undefined,
      });
      setRedirectOpen(false);
      setNote("");
      setConfirmed(action);
      onChanged?.();
    } catch {
      setErr("决策提交失败 — 确认后端已启动且该 Mission 处于待拍板状态");
      setBusy(false);
      return;
    }
    setBusy(false);
  }

  return (
    <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-2.5 p-4">
      {/* 头部 */}
      <header className="rounded-lg border border-border bg-surface-2 p-3.5">
        <div className="flex flex-wrap items-center gap-2">
          <PhaseBadge phase={mission.phase} />
          <span className="num text-[10px] text-ink-3">{mission.id}</span>
          <span className="num ml-auto text-[10px] text-ink-3">opened {fmtTime(mission.opened_at)}</span>
        </div>
        <h1 className="mt-2 text-balance text-[16px] font-semibold leading-snug text-ink">{mission.title}</h1>
        <div className="mt-1 text-[12px] text-ink-2">{mission.question}</div>
        <div className="mt-2 flex flex-wrap items-center gap-x-3.5 gap-y-1 text-[10px] text-ink-2">
          <span><span className="text-ink-4">对象 </span>{mission.subject_type}</span>
          <span className="num">{mission.subject_id}</span>
          <span><span className="text-ink-4">触发 </span>{mission.created_by}</span>
        </div>
        <StateStrip phase={mission.phase} order={PHASE_ORDER} />
      </header>

      {/* NOW BAR — 现在时锚 */}
      <NowBar
        who={now.who}
        sentence={now.sentence}
        anchor={now.anchor}
        breathe={mission.phase === "investigation" || mission.phase === "validation" || mission.phase === "needs_input"}
      />

      {/* Activity Pulse — 刚发生(窄带,倒序取尾) */}
      <MissionPulse activity={activity} />

      {/* 三栏:事实 | 判断 | 证据 */}
      <div className="grid grid-cols-1 items-start gap-2 lg:grid-cols-[30fr_35fr_35fr]">
        {/* 左栏 — 随状态 */}
        <section className="min-h-[280px] rounded-lg border border-border bg-surface-1 p-3">
          <ColHead name={leftColName(mission.phase)} role={leftColRole(mission.phase)} />
          {isNeedsInput && <FactsBlock mission={mission} />}
          {(mission.phase === "investigation" || mission.phase === "parked") && (
            <p className="mt-1 text-[11px] leading-relaxed text-ink-2">
              {mission.phase === "parked"
                ? "此调查已停放,不再消耗注意力。唤醒后回到 investigation。"
                : "调查进行中 — 发现材料随工作入账,本栏逐步充实。"}
            </p>
          )}
          {mission.phase === "validation" && (
            <p className="mt-1 text-[11px] leading-relaxed text-ink-2">
              待裁决:实验结果将直接回答「{mission.question}」——验证的是原假设,不调参。
            </p>
          )}
          {mission.phase === "resolved" && (
            <div className="mt-1">
              <div className={BLK_LABEL}>实验裁决</div>
              <p className="text-[12px] font-medium leading-snug text-ink">{String(outcome.verdict ?? "—")}</p>
              <p className="mt-1 text-[11px] leading-relaxed text-ink-2">{String(outcome.summary ?? "结论见 Outcome。")}</p>
            </div>
          )}
        </section>

        {/* 中栏 — 随状态 */}
        <section className="min-h-[280px] rounded-lg border border-border bg-surface-1 p-3">
          <ColHead name={midColName(mission.phase)} role={midColRole(mission.phase)} />
          {isNeedsInput && <AssessmentBlock mission={mission} />}
          {mission.phase === "investigation" && (
            <p className="mt-1 text-[11px] leading-relaxed text-ink-2">
              完成调查后 Selene 会推送一个可拍板的验证方案到本栏。
            </p>
          )}
          {mission.phase === "validation" && (
            <div className="mt-1">
              <div className="flex items-center gap-2 rounded-md border border-border bg-surface-2 px-2.5 py-2">
                <span className="h-[6px] w-[6px] shrink-0 rounded-full bg-accent animate-breathe motion-reduce:animate-none" />
                <span className="text-[11px] text-ink"><b>实验执行中</b> — Outcome 将自动入账</span>
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-ink-2">完成后自动结案,结论连锁进 Memory 与 Strategy Intelligence。</p>
            </div>
          )}
          {mission.phase === "resolved" && (
            <div className="mt-1">
              <div className={BLK_LABEL}>已入账</div>
              <p className="text-[11px] leading-relaxed text-ink-2">
                Outcome 已记录;结论作为 Memory 素材沉淀,下次同类问题可直接引用。
              </p>
            </div>
          )}
          {mission.phase === "parked" && (
            <p className="mt-1 text-[11px] leading-relaxed text-ink-2">停放期间无后台工作。唤醒需要新的触发或人工操作。</p>
          )}
        </section>

        {/* 右栏 — Evidence(恒定右锚) */}
        <section className="min-h-[280px] rounded-lg border border-border bg-surface-1 p-3">
          <ColHead name="Evidence 证据" role="每个结论可回源" anchor="context" />
          <EvidenceCards mission={mission} />
        </section>
      </div>

      {/* 论点计分卡 — 有活跃支柱才显示(认识论面板,零合成评分) */}
      {scorecard && scorecard.pillars.length > 0 && (
        <ThesisScorecardPanel scorecard={scorecard} />
      )}

      {/* 决策确认行 — 成功后一行反馈,随 phase 变化自然过渡(不依赖定时器) */}
      {confirmed && !isNeedsInput && (
        <div className="rounded-md border border-pos/40 bg-[color-mix(in_srgb,var(--color-pos)_6%,var(--color-surface-1))] px-3 py-2 text-[11.5px] font-medium text-ink">
          {CONFIRM_MSG[confirmed]}
        </div>
      )}

      {/* NEXT / 状态区 */}
      <NextZone mission={mission} busy={busy} err={err} onDecide={decide} redirectOpen={redirectOpen} note={note} setNote={setNote} />
    </div>
  );
}

/* ---------- 子块 ---------- */

function leftColName(phase: string): string {
  return phase === "resolved" ? "Outcome 结果" : phase === "validation" ? "Hypothesis 假设" : "Facts 事实";
}
function leftColRole(phase: string): string {
  return phase === "resolved" ? "实验裁决了什么" : phase === "validation" ? "拿什么判" : "已知什么是真的";
}
function midColName(phase: string): string {
  return phase === "resolved" ? "Assessment 定稿" : phase === "validation" ? "Experiment 实验" : "Assessment 判断";
}
function midColRole(phase: string): string {
  return phase === "resolved" ? "最终结论" : phase === "validation" ? "怎么裁决" : "怎么解读事实";
}

function ColHead({ name, role, anchor }: { name: string; role: string; anchor?: string }) {
  return (
    <div className="flex items-baseline gap-2 border-b border-border-soft pb-2">
      <span className="text-[11px] font-semibold tracking-wider text-ink">{name}</span>
      <span className="text-[10px] text-ink-3">{role}</span>
      {anchor && <span className="num ml-auto text-[9px] text-ink-4">{anchor}</span>}
    </div>
  );
}

function FactsBlock({ mission }: { mission: MissionRow }) {
  const drift = driftRows(mission);
  const ctx = (mission.context ?? {}) as Record<string, unknown>;
  return (
    <div className="mt-2 flex flex-col gap-3">
      {drift.length > 0 && (
        <div>
          <div className={BLK_LABEL}>声明 vs 实跑</div>
          <table className="w-full text-[10px]">
            <thead>
              <tr className="border-b border-border text-left text-ink-3">
                <th className="py-1 pr-2 font-medium"></th>
                <th className="py-1 pr-2 font-medium">声明 declared</th>
                <th className="py-1 font-medium">实跑 as-run</th>
              </tr>
            </thead>
            <tbody>
              {drift.map(([k, d, a]) => (
                <tr key={k} className="border-b border-border-soft">
                  <td className="py-1.5 pr-2 text-ink-2">{k}</td>
                  <td className="num py-1.5 pr-2 text-ink">{d}</td>
                  <td className={`num py-1.5 ${d !== a ? "text-warn" : "text-ink"}`}>{a}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {drift.length === 0 && (
        <p className="text-[11px] leading-relaxed text-ink-2">
          声明与实跑配置一致;调查材料见右栏 context。
        </p>
      )}
      {ctx.trigger != null && (
        <div className="text-[10px] text-ink-3">
          <span className="text-ink-4">触发上下文 </span>
          {typeof ctx.trigger === "string" ? ctx.trigger : JSON.stringify(ctx.trigger)}
        </div>
      )}
    </div>
  );
}

function AssessmentBlock({ mission }: { mission: MissionRow }) {
  const ctx = (mission.context ?? {}) as Record<string, unknown>;
  const recommendation = (ctx.recommendation ?? {}) as Record<string, unknown>;
  const title = typeof recommendation.title === "string" ? recommendation.title : "回到声明配置重新验证";
  const reasoning = Array.isArray(recommendation.reasoning) ? (recommendation.reasoning as string[]) : [];
  return (
    <div className="mt-2 flex flex-col gap-2.5">
      <div>
        <div className={BLK_LABEL}>系统推荐</div>
        <p className="text-[12px] font-medium text-ink">{title}</p>
        {reasoning.length > 0 && (
          <ul className="mt-1.5 flex flex-col gap-1">
            {reasoning.map((r) => (
              <li key={r} className="flex gap-1.5 text-[10.5px] leading-relaxed text-ink-2">
                <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-ink-4" />
                {r}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="num text-[9px] leading-relaxed text-ink-4">
        判断锚点 {fmtTime(mission.updated_at)} — 拍板后以实验裁决,不以争论更新
      </div>
    </div>
  );
}

/** 调查上下文投影 — context 里可能塞大对象(如 120 根 K 线 bars),只提炼标量摘要 */
function contextSummaryRows(mission: MissionRow): Array<[string, string]> {
  const ctx = (mission.context ?? {}) as Record<string, unknown>;
  const rows: Array<[string, string]> = [];
  const scalars = (o: unknown, prefix = ""): void => {
    if (o == null || typeof o !== "object") return;
    for (const [k, v] of Object.entries(o as Record<string, unknown>)) {
      if (Array.isArray(v)) {
        rows.push([prefix + k, `${v.length} 项`]);
      } else if (v != null && typeof v === "object") {
        if (prefix.length < 20) scalars(v, `${prefix}${k}.`);
      } else {
        rows.push([prefix + k, String(v)]);
      }
    }
  };
  scalars(ctx);
  return rows.slice(0, 8);
}

function EvidenceCards({ mission }: { mission: MissionRow }) {
  const ctx = (mission.context ?? {}) as Record<string, unknown>;
  const outcome = (mission.outcome ?? {}) as Record<string, unknown>;
  const hasOutcomeData = Object.keys(outcome).length > 0;
  const summaryRows = contextSummaryRows(mission);
  return (
    <div className="mt-2 flex flex-col gap-2">
      <EvCard id="C1 · 声明配置" type="config" src={mission.subject_id}>
        <EvRow k="subject_type" v={mission.subject_type} />
        <EvRow k="opened_at" v={fmtTime(mission.opened_at)} num />
        <EvRow k="created_by" v={mission.created_by} />
      </EvCard>
      {summaryRows.length > 0 && (
        <EvCard id="调查上下文" type="context" src={mission.id}>
          {summaryRows.map(([k, v]) => (
            <EvRow key={k} k={k} v={v} num={false} />
          ))}
        </EvCard>
      )}
      {hasOutcomeData && (
        <EvCard id="实验裁决" type="backtest" src={mission.id}>
          <EvRow k="verdict" v={String(outcome.verdict ?? "—")} />
          <EvRow k="method" v={String(outcome.method ?? "—")} />
          {(() => {
            const oos = outcome.oos;
            const inS = outcome.in_sample;
            if (oos != null && typeof oos === "object") {
              const oosR = oos as Record<string, unknown>;
              return (
                <>
                  <EvRow k="oos.total_return" v={String(oosR.total_return ?? "—")} num />
                  <EvRow k="oos.max_drawdown" v={String(oosR.max_drawdown ?? "—")} num />
                  <EvRow k="oos.sharpe_ratio" v={String(oosR.sharpe_ratio ?? "—")} num />
                </>
              );
            }
            return inS != null && typeof inS === "object" ? (
              <>
                {Object.entries(inS as Record<string, unknown>).slice(0, 3).map(([k, v]) => (
                  <EvRow key={k} k={`in_sample.${k}`} v={String(v)} num />
                ))}
              </>
            ) : null;
          })()}
        </EvCard>
      )}
      {!hasOutcomeData && mission.phase === "resolved" && (
        <EvCard id="Outcome" type="outcome" src={mission.id}>
          <EvRow k="resolved_at" v={fmtTime(mission.resolved_at)} num />
        </EvCard>
      )}
    </div>
  );
}

function EvCard({ id, type, src, children }: { id: string; type: string; src: string; children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-border bg-surface-2 p-2.5">
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className="text-[10px] font-semibold text-ink-2">{id}</span>
        <span className="rounded bg-surface-4 px-1 py-px text-[9px] text-ink-3">{type}</span>
        <span className="num ml-auto text-[9px] text-ink-4">{src}</span>
      </div>
      <div className="flex flex-col gap-0.5">{children}</div>
    </div>
  );
}

function EvRow({ k, v, num }: { k: string; v: string; num?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-2 text-[10px]">
      <span className="shrink-0 text-ink-3">{k}</span>
      <span className={`text-right text-ink ${num ? "num" : ""}`}>{v}</span>
    </div>
  );
}

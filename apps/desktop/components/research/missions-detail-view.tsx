"use client";

import { useMemo, useState } from "react";
import { apiPost } from "@/lib/api-client";
import type { MissionRow, ActivityRow } from "./missions-types";
import type { ThesisScorecard } from "./missions-thesis";
import { ThesisScorecard as ThesisScorecardPanel } from "./thesis-scorecard";
import { MissionPulse, NowBar, NextZone, PhaseBadge, StateStrip } from "./missions-parts";
import { useUiText } from "@/components/i18n/use-ui-text"
import type { Translate } from "@/lib/i18n/types"
import { identityTranslate } from "@/lib/i18n/types"

const PHASE_ORDER = ["investigation", "needs_input", "validation", "resolved"] as const;

type DecisionAction = "approve" | "more_evidence" | "redirect" | "park" | "resolve";

/** 结案产物 —— 与后端 ``SettlementResult.to_detail()`` 对齐。 */
export interface SettlementInfo {
  decision_id: string | null;
  child_question_id: string | null;
  revisit_at: string | null;
  window_start: string | null;
  window_end: string | null;
  warnings: string[];
}

/** 决策回执文案。模块级映射存**中文源串**,渲染点 tr(CONFIRM_MSG[action]) —— 与
 *  indicator-library 的分类标签同一姿势:表是查表键,不是展示值。 */
const CONFIRM_MSG: Record<DecisionAction, string> = {
  approve: "已批准 — 进入验证执行,跑完由你结案",
  more_evidence: "已要求补充证据 — 回到调查,Selene 补完材料再推送",
  redirect: "已重新定向 — 调查按新方向展开",
  park: "已停放 — 不再消耗注意力,随时可唤醒",
  resolve: "已结案 — 结论写入 Outcome,进入 WORLD CHANGED",
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

/** Now Bar 现在时文案 + 轮到谁(accent=selene 干活 / gold=等你 / pos=结案)
 *
 * `tr` 由调用方注入:`sentence` 里 `<b>` 两侧的片段与 `anchor` 的模板串都要就地译,
 * 整串含 JSX 无法查表。默认恒等函数保持纯函数测试断言中文原文不变。
 */
function nowInfo(mission: MissionRow, tr: Translate = identityTranslate): { who: "selene" | "user" | "done"; sentence: React.ReactNode; anchor: string } {
  const opened = fmtTime(mission.opened_at);
  switch (mission.phase) {
    case "investigation":
      return {
        who: "selene",
        sentence: <>{tr("Selene 正在调查 — ")}<b>{tr("复盘证据材料,评估可裁决性")}</b></>,
        anchor: tr("{opened} 开案", { opened }),
      };
    case "needs_input":
      return {
        who: "user",
        sentence: <>{tr("等你拍板 — ")}<b>{tr("批准建议进入验证")}</b>{tr(",或要更多证据 / 重新定向 / 停放")}</>,
        anchor: tr("{t} 推送", { t: fmtTime(mission.updated_at) }),
      };
    case "validation":
      return {
        who: "selene",
        // 曾写「结果自动入账并结案,无需你的输入」——没有任何生产路径会写 resolved:
        // 状态机允许 validation→resolved,但唯一的生产写者是人工点「结案」,
        // 外加 keltner_revalidate.py 那个一次性脚本。实库里两条 mission 已在
        // validation 停留 8 天和 20 天。
        sentence: <>{tr("实验运行中 — ")}<b>{tr("跑完需你点「结案」")}</b>{tr(",结论才入账")}</>,
        anchor: tr("{t} 批准", { t: fmtTime(mission.updated_at) }),
      };
    default: // resolved | parked
      return {
        who: "done",
        sentence: mission.phase === "resolved"
          ? <>{tr("已结案 — ")}<b>{tr("结论已可被下一次 Mission 引用")}</b></>
          : <>{tr("已停放 — ")}<b>{tr("不再消耗注意力,可随时唤醒")}</b></>,
        anchor: mission.resolved_at
          ? tr("全程 {a} → {b}", { a: opened, b: fmtTime(mission.resolved_at) })
          : opened,
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
  const tr = useUiText()

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [redirectOpen, setRedirectOpen] = useState(false);
  const [note, setNote] = useState("");
  const [userNote, setUserNote] = useState("");
  const [noteBusy, setNoteBusy] = useState(false);
  const [noteMsg, setNoteMsg] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState<DecisionAction | null>(null);
  const [settlement, setSettlement] = useState<SettlementInfo | null>(null);

  const outcome = (mission.outcome ?? {}) as Record<string, unknown>;
  const isNeedsInput = mission.phase === "needs_input";
  const now = useMemo(() => nowInfo(mission, tr), [mission, tr]);

  async function decide(action: DecisionAction) {
    if (action === "redirect" && !redirectOpen) {
      setRedirectOpen(true);
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const resp = (await apiPost(`/api/v1/research/missions/${mission.id}/decision`, {
        action,
        note: action === "redirect" ? note || undefined : undefined,
      })) as { settlement?: SettlementInfo } | null;
      setRedirectOpen(false);
      setNote("");
      setConfirmed(action);
      // 结案会同时写决策 / 派生问题 / 设复访。丢掉这段等于让用户看不见
      // "这一笔到底做出了什么" —— 结案本是链路最该被确认的一步。
      setSettlement(action === "resolve" ? (resp?.settlement ?? null) : null);
      onChanged?.();
    } catch {
      setErr("决策提交失败 — 确认后端已启动且该 Mission 处于待拍板状态");
      setBusy(false);
      return;
    }
    setBusy(false);
  }

  async function submitUserNote() {
    const text = userNote.trim();
    if (!text || noteBusy) return;
    setNoteBusy(true);
    setNoteMsg(null);
    try {
      await apiPost(`/api/v1/research/missions/${mission.id}/note`, { note: text });
      setUserNote("");
      setNoteMsg("已留言 — 进入活动流,此后所有窗口都能看到");
      onChanged?.();
    } catch {
      setNoteMsg("留言失败 — 确认后端已启动");
    }
    setNoteBusy(false);
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
          <span><span className="text-ink-4">{tr("对象")} </span>{mission.subject_type}</span>
          <span className="num">{mission.subject_id}</span>
          <span><span className="text-ink-4">{tr("触发")} </span>{mission.created_by}</span>
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

      {/* 人工留言 — 只进活动流,不触发状态转移;人可以在调查途中补充线索 */}
      <section
        data-testid="mission-note"
        className="rounded-lg border border-border bg-surface-1 px-3.5 py-2"
      >
        <div className="mb-1 flex items-baseline gap-2.5">
          <span className="text-[11px] font-semibold tracking-wider text-ink">给 Selene 留言</span>
          <span className="text-[10px] text-ink-3">补充线索 / 指出盲区 · 不改变阶段</span>
        </div>
        <div className="flex items-center gap-2">
          <input
            value={userNote}
            onChange={(e) => setUserNote(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) void submitUserNote();
            }}
            maxLength={2000}
            disabled={noteBusy}
            placeholder="例:昨天资金费率转负了,调查时带上这一层"
            className="h-7 flex-1 rounded border border-border bg-surface-2 px-2 text-[11px] text-ink outline-none placeholder:text-ink-5 focus:border-accent/50"
          />
          <button
            type="button"
            data-testid="mission-note-submit"
            disabled={noteBusy || !userNote.trim()}
            onClick={() => void submitUserNote()}
            className="rounded border border-border bg-surface-3 px-3 py-1 text-[10px] text-ink transition-colors hover:border-accent/50 disabled:opacity-50"
          >
            {noteBusy ? "提交中…" : "留言"}
          </button>
        </div>
        {noteMsg && (
          <p className={`mt-1 text-[10px] ${noteMsg.startsWith("已留言") ? "text-ink-3" : "text-neg"}`}>
            {noteMsg}
          </p>
        )}
      </section>

      {/* 三栏:事实 | 判断 | 证据 */}
      <div className="grid grid-cols-1 items-start gap-2 lg:grid-cols-[30fr_35fr_35fr]">
        {/* 左栏 — 随状态 */}
        <section className="min-h-[280px] rounded-lg border border-border bg-surface-1 p-3">
          <ColHead name={leftColName(mission.phase, tr)} role={leftColRole(mission.phase, tr)} />
          {isNeedsInput && <FactsBlock mission={mission} />}
          {(mission.phase === "investigation" || mission.phase === "parked") && (
            <p className="mt-1 text-[11px] leading-relaxed text-ink-2">
              {mission.phase === "parked"
                ? tr("此调查已停放,不再消耗注意力。唤醒后回到 investigation。")
                : tr("调查进行中 — 发现材料随工作入账,本栏逐步充实。")}
            </p>
          )}
          {mission.phase === "validation" && (
            <p className="mt-1 text-[11px] leading-relaxed text-ink-2">
              {tr("待裁决:实验结果将直接回答「")}{mission.question}{tr("」——验证的是原假设,不调参。")}
            </p>
          )}
          {mission.phase === "resolved" && (
            <div className="mt-1">
              <div className={BLK_LABEL}>{tr("实验裁决")}</div>
              <p className="text-[12px] font-medium leading-snug text-ink">{String(outcome.verdict ?? "—")}</p>
              <p className="mt-1 text-[11px] leading-relaxed text-ink-2">{String(outcome.summary ?? tr("结论见 Outcome。"))}</p>
            </div>
          )}
        </section>

        {/* 中栏 — 随状态 */}
        <section className="min-h-[280px] rounded-lg border border-border bg-surface-1 p-3">
          <ColHead name={midColName(mission.phase, tr)} role={midColRole(mission.phase, tr)} />
          {isNeedsInput && <AssessmentBlock mission={mission} />}
          {mission.phase === "investigation" && (
            <p className="mt-1 text-[11px] leading-relaxed text-ink-2">
              {tr("完成调查后 Selene 会推送一个可拍板的验证方案到本栏。")}
            </p>
          )}
          {mission.phase === "validation" && (
            <div className="mt-1">
              <div className="flex items-center gap-2 rounded-md border border-border bg-surface-2 px-2.5 py-2">
                <span className="h-[6px] w-[6px] shrink-0 rounded-full bg-accent animate-breathe motion-reduce:animate-none" />
                <span className="text-[11px] text-ink"><b>{tr("实验执行中")}</b> {tr("— 无自动入账,跑完由你结案")}</span>
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-ink-2">{tr("验证跑完后由你点「结案」— 结论写入 Outcome,进入 WORLD CHANGED。")}</p>
            </div>
          )}
          {mission.phase === "resolved" && (
            <div className="mt-1">
              <div className={BLK_LABEL}>{tr("已入账")}</div>
              <p className="text-[11px] leading-relaxed text-ink-2">
                {tr("Outcome 已记录;结论作为 Memory 素材沉淀,下次同类问题可直接引用。")}
              </p>
            </div>
          )}
          {mission.phase === "parked" && (
            <p className="mt-1 text-[11px] leading-relaxed text-ink-2">{tr("停放期间无后台工作。唤醒需要新的触发或人工操作。")}</p>
          )}
        </section>

        {/* 右栏 — Evidence(恒定右锚) */}
        <section className="min-h-[280px] rounded-lg border border-border bg-surface-1 p-3">
          <ColHead name={tr("Evidence 证据")} role={tr("每个结论可回源")} anchor="context" />
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
          {tr(CONFIRM_MSG[confirmed])}
          {settlement && (
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10.5px] font-normal text-ink-2">
              <span>
                <span className="text-ink-4">决策 </span>
                {settlement.decision_id ?? "未落账(无归属问题)"}
              </span>
              <span>
                <span className="text-ink-4">下一个问题 </span>
                {settlement.child_question_id ?? "未派生"}
              </span>
              <span>
                <span className="text-ink-4">复访 </span>
                {settlement.revisit_at ? fmtTime(settlement.revisit_at) : "未设置"}
              </span>
              {settlement.warnings.map((w) => (
                <span key={w} className="text-warn">
                  {w}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* NEXT / 状态区 */}
      <NextZone mission={mission} busy={busy} err={err} onDecide={decide} redirectOpen={redirectOpen} note={note} setNote={setNote} />
    </div>
  );
}

/* ---------- 子块 ---------- */

/** 三栏列头文案 = 模块级映射,渲染点 tr(...) —— 同 CONFIRM_MSG 姿势。 */
function leftColName(phase: string, tr: Translate = identityTranslate): string {
  return tr(phase === "resolved" ? "Outcome 结果" : phase === "validation" ? "Hypothesis 假设" : "Facts 事实");
}
function leftColRole(phase: string, tr: Translate = identityTranslate): string {
  return tr(phase === "resolved" ? "实验裁决了什么" : phase === "validation" ? "拿什么判" : "已知什么是真的");
}
function midColName(phase: string, tr: Translate = identityTranslate): string {
  return tr(phase === "resolved" ? "Assessment 定稿" : phase === "validation" ? "Experiment 实验" : "Assessment 判断");
}
function midColRole(phase: string, tr: Translate = identityTranslate): string {
  return tr(phase === "resolved" ? "最终结论" : phase === "validation" ? "怎么裁决" : "怎么解读事实");
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
  const tr = useUiText()

  const drift = driftRows(mission);
  const ctx = (mission.context ?? {}) as Record<string, unknown>;
  return (
    <div className="mt-2 flex flex-col gap-3">
      {drift.length > 0 && (
        <div>
          <div className={BLK_LABEL}>{tr("声明 vs 实跑")}</div>
          <table className="w-full text-[10px]">
            <thead>
              <tr className="border-b border-border text-left text-ink-3">
                <th className="py-1 pr-2 font-medium"></th>
                <th className="py-1 pr-2 font-medium">{tr("声明 declared")}</th>
                <th className="py-1 font-medium">{tr("实跑 as-run")}</th>
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
          {tr("声明与实跑配置一致;调查材料见右栏 context。")}
        </p>
      )}
      {ctx.trigger != null && (
        <div className="text-[10px] text-ink-3">
          <span className="text-ink-4">{tr("触发上下文")} </span>
          {typeof ctx.trigger === "string" ? ctx.trigger : JSON.stringify(ctx.trigger)}
        </div>
      )}
    </div>
  );
}

function AssessmentBlock({ mission }: { mission: MissionRow }) {
  const tr = useUiText()

  const ctx = (mission.context ?? {}) as Record<string, unknown>;
  const recommendation = (ctx.recommendation ?? {}) as Record<string, unknown>;
  const title = typeof recommendation.title === "string" ? recommendation.title : tr("回到声明配置重新验证");
  const reasoning = Array.isArray(recommendation.reasoning) ? (recommendation.reasoning as string[]) : [];
  return (
    <div className="mt-2 flex flex-col gap-2.5">
      <div>
        <div className={BLK_LABEL}>{tr("系统推荐")}</div>
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
        {tr("判断锚点")} {fmtTime(mission.updated_at)} {tr("— 拍板后以实验裁决,不以争论更新")}
      </div>
    </div>
  );
}

/** 调查上下文投影 — context 里可能塞大对象(如 120 根 K 线 bars),只提炼标量摘要 */
function contextSummaryRows(mission: MissionRow, tr: Translate = identityTranslate): Array<[string, string]> {
  const ctx = (mission.context ?? {}) as Record<string, unknown>;
  const rows: Array<[string, string]> = [];
  const scalars = (o: unknown, prefix = ""): void => {
    if (o == null || typeof o !== "object") return;
    for (const [k, v] of Object.entries(o as Record<string, unknown>)) {
      if (Array.isArray(v)) {
        rows.push([prefix + k, tr("{n} 项", { n: v.length })]);
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
  const tr = useUiText()

  const ctx = (mission.context ?? {}) as Record<string, unknown>;
  const outcome = (mission.outcome ?? {}) as Record<string, unknown>;
  const hasOutcomeData = Object.keys(outcome).length > 0;
  const summaryRows = contextSummaryRows(mission, tr);
  return (
    <div className="mt-2 flex flex-col gap-2">
      <EvCard id={tr("C1 · 声明配置")} type="config" src={mission.subject_id}>
        <EvRow k="subject_type" v={mission.subject_type} />
        <EvRow k="opened_at" v={fmtTime(mission.opened_at)} num />
        <EvRow k="created_by" v={mission.created_by} />
      </EvCard>
      {summaryRows.length > 0 && (
        <EvCard id={tr("调查上下文")} type="context" src={mission.id}>
          {summaryRows.map(([k, v]) => (
            <EvRow key={k} k={k} v={v} num={false} />
          ))}
        </EvCard>
      )}
      {hasOutcomeData && (
        <EvCard id={tr("实验裁决")} type="backtest" src={mission.id}>
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

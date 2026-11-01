"use client";

// PA = Price Action analysis — 左图右 AI 闭环（AGPL-clean: 只学交互范式，零拷贝认知框架）
// 左侧 KfChart（symbol/tf 局部 state + AI 决策线 overlay），右侧 AI 分析面板
// 流程：选品种/周期 → 分析当前图表 → LLM 流式输出诊断 + 结构化 JSON(entry/SL/TP) → 画线到图上 → 自由追问

import React, { useState, useCallback, useRef, useEffect } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";
import { sendAgentChat } from "@/lib/api/chat";
import type { AgentChatContext } from "@/lib/api/chat";
import { useToolApproval } from "@/lib/hooks/use-tool-approval";
import { KfChart, type DecisionOverlay } from "@/components/watch/kf-chart";
import { useMarketStore } from "@/lib/store/market";
import { useApi } from "@/lib/hooks/use-api";
import { createExperiment } from "@/lib/api/experiments";
import { runSetupBacktest, type SetupBacktestResult } from "@/lib/api/setup-backtest";
import {
  analyzeTrading,
  createPriceAlert,
  getDecisionRecord,
  httpErrorMessage,
  listRecords,
  sendRecordFollowUp,
  type AnalyzeResponse,
  type PaFollowUp,
  type PaOrder,
  type PaTraceNode,
  type PriceAlertRequest,
  type RecordListItem,
} from "@/lib/api/decision-workflow";
import { parseRichMarkdownToReact } from "@/components/strategy/markdown-rich";
import { Sparkles, Send, Loader2, Bot, LineChart, Play, Save, Check, AlertTriangle, History, FileSearch, GitBranch, Bell, ChevronDown } from "lucide-react";
import { PaDecisionTreeView } from "@/components/research/pa-decision-tree";
import { HeartbeatStrip } from "@/components/research/heartbeat-strip";
import { useUiText } from "@/components/i18n/use-ui-text"
import { formatPending } from "@/lib/i18n/types"
import { identityTranslate, type Translate } from "@/lib/i18n/types"

interface PaDecision {
  direction?: string;
  entry?: number;
  stop_loss?: number;
  take_profit?: number;
  confidence?: number;
  reason?: string;
}

interface ReasoningStep {
  type: "thinking" | "tool_call" | "tool_result" | "reflection";
  data: Record<string, unknown>;
  timestamp: number;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoningSteps?: ReasoningStep[];
}

// 键是后端 interval/枚举值,值是中文展示文案 —— 表不动,渲染点过 tr()
// (同 indicator-library.tsx:76 对 INDICATOR_CATEGORY_LABELS 的处理)。
const SUPPORTED_TFS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];
const TF_LABELS: Record<string, string> = {
  "1m": "1分钟", "5m": "5分钟", "15m": "15分钟", "30m": "30分钟",
  "1h": "1小时", "4h": "4小时", "1d": "日线",
};

// 后端 orchestrator.py:34 的 4 档 trading_stance;此前 PA 面板从不传该字段,
// 后端能力一直不可达。文案与行情分析页(analysis-panel)保持同一套语义。
const STANCE_OPTIONS: { value: string; label: string }[] = [
  { value: "conservative", label: "保守" },
  { value: "balanced", label: "均衡" },
  { value: "aggressive", label: "激进" },
  { value: "very_aggressive", label: "极度激进" },
];

// Ticker REST fallback（WS 实时价走 KfChart 的 tickerLast，这里只取初始 mark_price）
interface TickerData {
  last_price?: number;
}

function newMessageId(): string {
  return `${Date.now().toString()}-${Math.random().toString(36).slice(2, 9)}`;
}

function deltaTextFromData(data: unknown): string {
  if (typeof data === "string") return data;
  if (data && typeof data === "object" && "text" in data) {
    const v = (data as { text: unknown }).text;
    if (typeof v === "string") return v;
  }
  return "";
}

function cleanReasoningText(raw: string): string {
  return raw
    .replace(/<\/?reasoning>/gi, "")
    .replace(/^(?:Step|Round)\s*\d+\s*[:：]?\s*(?:Think|Reflect|Act|Observe|Analyze|Decide)?\s*/gim, "")
    .trim();
}

const STEP_TONES: Record<ReasoningStep["type"], { icon: string; cls: string; label: string }> = {
  thinking: { icon: "🧠", cls: "border-sky-500/20 bg-sky-500/8 text-sky-200", label: "思考" },
  tool_call: { icon: "⚙️", cls: "border-violet-500/20 bg-violet-500/8 text-violet-200", label: "工具调用" },
  tool_result: { icon: "📊", cls: "border-emerald-500/20 bg-emerald-500/8 text-emerald-200", label: "工具结果" },
  reflection: { icon: "💡", cls: "border-amber-500/20 bg-amber-500/8 text-amber-200", label: "反思" },
};

function formatReasoningSummary(step: ReasoningStep): string {
  if (step.type === "thinking" || step.type === "reflection") {
    return cleanReasoningText(String(step.data.content ?? step.data.summary ?? ""));
  }
  if (step.type === "tool_call") {
    const tool = String(step.data.tool ?? step.data.name ?? "unknown");
    return `${tool}(${JSON.stringify(step.data.arguments ?? step.data.input ?? {})})`;
  }
  return cleanReasoningText(String(step.data.output ?? step.data.result ?? step.data.content ?? ""));
}

function ReasoningTimeline({ steps }: { steps: ReasoningStep[] }) {
  const tr = useUiText()

  if (!steps.length) return null;
  return (
    <div className="mb-3 space-y-1.5 rounded-xl border border-border/60 bg-background/40 p-2.5">
      <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">{tr("推理进度 ·")} {steps.length} {tr("步")}</p>
      {steps.map((step, idx) => {
        const v = STEP_TONES[step.type];
        const summary = formatReasoningSummary(step);
        const isLatest = idx === steps.length - 1;
        return (
          <div key={`${step.type}-${idx}-${step.timestamp}`} className="flex items-start gap-2">
            <span className={cn("mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px]", v.cls)}>
              {v.icon}
            </span>
            <div className="min-w-0 flex-1">
              <p className={cn("text-[11px] leading-5 text-muted-foreground", isLatest && "text-foreground/90")}>
                <span className="font-medium text-foreground/70">{tr(v.label)}</span>
                {summary ? ` · ${summary}` : ""}
              </p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// 后端 order 是自由 dict(validator 只约束 trade⇒非空);字段名多键容错
function pickOrderField(order: PaOrder | null | undefined, keys: string[]): number | undefined {
  if (!order) return undefined;
  for (const k of keys) {
    const v = order[k];
    if (typeof v === "number" && Number.isFinite(v)) return v;
    if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  }
  return undefined;
}

function orderDirection(order: PaOrder | null | undefined): string | null {
  const d = order?.direction ?? order?.side;
  return typeof d === "string" ? d.toLowerCase() : null;
}

function decisionFromRecord(record: AnalyzeResponse): PaDecision {
  const diag2 = record.stage2?.diagnosis_or_decision ?? null;
  const outcome = diag2?.terminal_outcome;
  const decisionWord = diag2?.decision ?? outcome ?? "unknown";
  const dir = orderDirection(diag2?.order) ?? (decisionWord.includes("多") ? "long" : decisionWord.includes("空") ? "short" : undefined);
  return {
    direction: dir ?? undefined,
    entry: pickOrderField(diag2?.order, ["entry", "entry_price"]),
    stop_loss: pickOrderField(diag2?.order, ["stop_loss", "stop_price"]),
    take_profit: pickOrderField(diag2?.order, ["take_profit", "target_price", "target"]),
    confidence: typeof diag2?.decision_summary?.confidence === "number"
      ? diag2.decision_summary.confidence
      : undefined,
    reason: typeof diag2?.decision_summary?.reject_reason === "string"
      ? (diag2.decision_summary.reject_reason as string)
      : undefined,
  };
}

// 冻结快照:分析发起时的品种/周期/现价,面板展示与决策卡据此对账
interface FrozenSnapshot {
  symbol: string;
  tf: string;
  markPrice: number | null;
  barsTotal: number | null;
  lastClose: number | null;
  windowEnd: number | string | null;
}

// window_end_iso 名不副实:真管线写 ms 数值,旧记录可能是 ISO 字符串。
// 统一渲染成本地可读时间;解析失败回退原文(不静默丢字段)。
function formatWindowEnd(value: number | string): string {
  if (typeof value === "number") {
    if (Number.isFinite(value)) return new Date(value).toLocaleString("zh-CN", { hour12: false });
    return String(value);
  }
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? value : new Date(parsed).toLocaleString("zh-CN", { hour12: false });
}

const GATE_LABELS: Record<string, string> = {
  proceed: "入场质量达标",
  wait: "等待观望",
  unknown: "状态不明",
};

const OUTCOME_LABELS: Record<string, string> = {
  trade: "交易",
  wait: "观望",
  reject: "否决",
  proceed: "通过",
};

// W2 结果闭环(T3a judge_outcome):入场单的后续市场判定,与上面的
// OUTCOME_LABELS(本次决策终局方向)语义不同 — 不合并。
const OUTCOME_STATUS_LABELS: Record<string, string> = {
  win: "命中",
  loss: "止损",
  expired: "未触发",
  timeout_open: "持有未结",
};

const NEXT_PERIOD_LABELS: Record<string, string> = {
  long: "看涨",
  short: "看跌",
  neutral: "观望",
};

// 最近诊断列表行的徽章着色(与上方语义词汇表配套)
const DIRECTION_TONE: Record<string, string> = {
  long: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  short: "border-rose-500/40 bg-rose-500/10 text-rose-300",
  neutral: "border-sky-500/40 bg-sky-500/10 text-sky-300",
};

const OUTCOME_TONE: Record<string, string> = {
  win: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  loss: "border-rose-500/40 bg-rose-500/10 text-rose-300",
  expired: "border-border/60 bg-surface-1 text-muted-foreground",
  timeout_open: "border-sky-500/40 bg-sky-500/10 text-sky-300",
};

// trace 节点答案语义着色:go 蓝 / hold 黄 / stop 红(与视觉稿正典一致)
type TraceTone = "go" | "hold" | "stop";
function traceTone(answer: string): TraceTone {
  const a = answer.toLowerCase();
  if (/reject|否决|命中/.test(a)) return "stop";
  if (/wait|观望|等待|range|区间/.test(a)) return "hold";
  return "go";
}

const TONE_DOT: Record<TraceTone, string> = {
  go: "border-sky-400",
  hold: "border-amber-400",
  stop: "border-red-400",
};
const TONE_TEXT: Record<TraceTone, string> = {
  go: "text-sky-300",
  hold: "text-amber-300",
  stop: "text-red-300",
};

function TraceTimeline({ trace, title }: { trace: PaTraceNode[]; title: string }) {
  const tr = useUiText()

  // 默认折叠只显末节点(critique 认知负荷项);单节点链直接展开
  const collapsible = trace.length > 1;
  const [open, setOpen] = useState(!collapsible);
  const last = trace[trace.length - 1];
  if (!trace.length) return null;
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => collapsible && setOpen((v) => !v)}
        className={cn(
          "text-[10px] font-semibold uppercase tracking-[0.12em] text-foreground/80",
          collapsible ? "cursor-pointer transition-colors hover:text-primary" : "cursor-default",
        )}
      >
        {title} · {trace.length} {tr("节点")}
      </button>
      <div className="relative mt-1.5 space-y-1.5 pl-[13px]">
        <span aria-hidden className="absolute bottom-2 left-[3px] top-2 w-px bg-border/70" />
        {!open && collapsible ? (
          <div className="relative rounded-lg border border-border/50 bg-background/40 px-2 py-1.5">
            <span aria-hidden className={cn("absolute -left-[13px] top-[9px] h-1.5 w-1.5 rounded-full border-2 bg-background", TONE_DOT[traceTone(last.answer)])} />
            <p className="text-[11px] text-foreground/90">
              <span className="font-mono text-primary">{last.node_id}</span>
              {" · "}{last.question}
              <span className={cn("ml-1 font-semibold", TONE_TEXT[traceTone(last.answer)])}>{last.answer}</span>
            </p>
          </div>
        ) : (
          trace.map((n, i) => {
            const tone = traceTone(n.answer);
            return (
              <div key={`${n.node_id}-${i}`} className="relative rounded-lg border border-border/50 bg-background/40 px-2 py-1.5">
                <span aria-hidden className={cn("absolute -left-[13px] top-[9px] h-1.5 w-1.5 rounded-full border-2 bg-background", TONE_DOT[tone])} />
                <p className="text-[11px] text-foreground/90">
                  <span className="font-mono text-primary">{n.node_id}</span>
                  {" · "}{n.question}
                  <span className={cn("ml-1 font-semibold", TONE_TEXT[tone])}>{n.answer}</span>
                </p>
                <p className="mt-0.5 text-[10px] leading-4 text-muted-foreground">
                  <span className="font-mono">{n.bar_range ?? "—"}</span>{n.reason ? ` · ${n.reason}` : ""}
                </p>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

// LLM 自报节点改判的存证展示(orchestrator 不应用,只透出;models.py NodeOverride)
function OverridesList({ overrides }: { overrides: Array<{ node_id: string; new_answer: string; reason: string; bar_range: string | null; stage: "Stage 1" | "Stage 2" }> }) {
  const tr = useUiText()

  if (!overrides.length) return null;
  return (
    <div className="space-y-1">
      {overrides.map((o, i) => (
        <div key={`${o.node_id}-${i}`} className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-2 py-1.5">
          <p className="text-[11px] text-foreground/90">
            <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1 py-0.5 text-[10px] font-medium text-amber-300">{tr("改判")}</span>
            {" "}<span className="font-mono text-primary">{o.node_id}</span>
            <span className="text-[10px] text-muted-foreground"> · {o.stage}</span>
            {" → "}<span className="font-semibold text-amber-300">{o.new_answer}</span>
            {o.bar_range ? <span className="ml-1 font-mono text-[10px] text-muted-foreground">{o.bar_range}</span> : null}
          </p>
          {o.reason ? <p className="mt-0.5 text-[10px] leading-4 text-muted-foreground">{o.reason}</p> : null}
        </div>
      ))}
    </div>
  );
}

// 风险回报比(方向未知时不猜,返回 null)
function orderRR(d: PaDecision | null): number | null {
  if (!d || d.entry == null || d.stop_loss == null || d.take_profit == null) return null;
  if (d.direction !== "long" && d.direction !== "short") return null;
  const risk = d.direction === "short" ? d.stop_loss - d.entry : d.entry - d.stop_loss;
  const reward = d.direction === "short" ? d.entry - d.take_profit : d.take_profit - d.entry;
  if (risk <= 0 || reward <= 0) return null;
  return reward / risk;
}

// order 行:线色小标签与图上 overlay 线同色(入场 #e3b650 / SL #EF5350 / TP #10b981);正典金=var(--color-gold)
function OrderRow({ label, labelCls, note, value }: { label: string; labelCls: string; note: string | null; value: string }) {
  return (
    <div className="flex items-baseline gap-2 border-b border-border/40 py-1 last:border-b-0">
      <span className={cn("w-8 shrink-0 rounded border px-1 text-center text-[10px]", labelCls)}>{label}</span>
      {note ? <span className="ml-auto text-[10px] text-muted-foreground">{note}</span> : <span className="ml-auto" />}
      <span className="font-mono text-[12px] text-foreground">{value}</span>
    </div>
  );
}

// 原始 LLM I/O 折叠查看(受控 state,默认收起;JSON 单行紧凑排版)
function RawMessagesBlock({ title, messages }: { title: string; messages: Array<Record<string, unknown>> | undefined }) {
  const tr = useUiText()

  const [open, setOpen] = useState(false);
  if (!messages?.length) return null;
  return (
    <div className="rounded-lg border border-border/50 bg-background/40 px-2 py-1.5">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground transition-colors hover:text-foreground"
      >
        {title} · {messages.length} {tr("条")}
      </button>
      {open && (
        <div className="mt-1.5 space-y-1">
          {messages.map((m, i) => (
            <pre
              key={i}
              className="max-h-40 overflow-y-auto whitespace-pre-wrap break-all rounded border border-border/40 bg-background/60 px-1.5 py-1 font-mono text-[10px] leading-4 text-foreground/80"
            >
              {JSON.stringify(m)}
            </pre>
          ))}
        </div>
      )}
    </div>
  );
}

function PaDecisionRecordCard({ record, frozen, decision }: { record: AnalyzeResponse; frozen: FrozenSnapshot | null; decision: PaDecision | null }) {
  const tr = useUiText()

  const diag1 = record.stage1?.diagnosis_or_decision ?? null;
  const diag2 = record.stage2?.diagnosis_or_decision ?? null;
  const gate = diag1?.gate_result ?? "unknown";
  const state = diag1?.state ?? "unknown";
  const outcome = diag2?.terminal_outcome;
  const usage = record.usage_total ?? {};
  const summary = record.input_data?.summary;
  const atr14 = summary?.atr14 != null && Number.isFinite(Number(summary.atr14)) ? Number(summary.atr14) : null;
  const nextPeriod = (diag2?.decision_summary?.next_period ?? null) as
    | { direction?: string; watch_levels?: number[]; invalidate_if?: string }
    | null;
  // W2 审计:结果闭环(T3a judge_outcome)。与 diag2.terminal_outcome(本次决策
  // 终局方向 trade/wait/reject)是两回事 — 这是对入场单的后续行情复盘。
  const auditStatus = typeof record.outcome?.status === "string" ? record.outcome.status : null;
  const pnlR = typeof record.outcome?.pnl_r === "number" && Number.isFinite(record.outcome.pnl_r)
    ? record.outcome.pnl_r
    : null;

  const dir = decision?.direction ?? null;
  const hasAnyPrice = decision?.entry != null || decision?.stop_loss != null || decision?.take_profit != null;
  const rr = orderRR(decision);
  const s1Trace = diag1?.trace ?? [];
  const lastS1Reason = s1Trace.length ? s1Trace[s1Trace.length - 1]?.reason ?? "" : "";
  const rejectReason = typeof diag2?.decision_summary?.reject_reason === "string"
    ? (diag2.decision_summary.reject_reason as string)
    : "";

  // ATR 倍数注记(比率过小不展示,避免 "-0.0 ATR" 噪声)
  const atrNote = (delta: number): string | null => {
    if (!atr14 || atr14 <= 0) return null;
    const r = Math.abs(delta) / atr14;
    if (r < 0.1) return null;
    return `${delta >= 0 ? "+" : "-"}${r.toFixed(1)} ATR`;
  };
  const entryNote = decision?.entry != null && frozen?.lastClose
    // P2a:"快照收盘"非"现价"——图上 overlay 的"现价"线是实时 ticker,同词
    // 不同义会让 Director 对账时误判滑点。这里比的永远是 AI 快照窗口末收盘。
    ? tr("{pct}% 快照收盘", { pct: `${decision.entry >= frozen.lastClose ? "+" : ""}${(((decision.entry - frozen.lastClose) / frozen.lastClose) * 100).toFixed(1)}` })
    : null;

  return (
    <div className="mt-2 space-y-2 rounded-xl border border-primary/15 bg-background/50 px-3 py-2.5">
      {/* 徽章行:state / gate / outcome / 短路标记 / input_id */}
      <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
        {record.meta?.demo === true && (
          <span className="rounded border border-violet-500/40 bg-violet-500/10 px-1.5 py-0.5 font-medium text-violet-300">
            {diag2?.order
              ? tr("演示输出 · 真 LLM 试跑,不入审计表")
              : tr("演示输出 · 不生成交易指令")}
          </span>
        )}
        <span className="rounded border border-sky-500/40 bg-sky-500/10 px-1.5 py-0.5 text-sky-300">{state}</span>
        <span className={cn(
          "rounded border px-1.5 py-0.5 font-medium",
          gate === "proceed" ? "border-sky-500/40 bg-sky-500/10 text-sky-300"
            : gate === "wait" ? "border-amber-500/40 bg-amber-500/10 text-amber-300"
              : "border-border/60 bg-muted/40 text-muted-foreground"
        )}>
          {tr(GATE_LABELS[gate] ?? gate)}
        </span>
        {outcome && (
          <span className={cn(
            "rounded border px-1.5 py-0.5 font-medium",
            outcome === "trade" ? "border-[#e3b650]/40 bg-[#e3b650]/10 text-[#e3b650]"
              : outcome === "reject" ? "border-destructive/40 bg-destructive/10 text-destructive"
                : "border-amber-500/40 bg-amber-500/10 text-amber-300"
          )}>
            {tr(OUTCOME_LABELS[outcome] ?? outcome)}
          </span>
        )}
        {!diag2 && (
          <span className="rounded border border-dashed border-border/70 px-1.5 py-0.5 text-muted-foreground">
            {tr("Stage 2 未执行 · 短路")}
          </span>
        )}
        {record.meta?.input_id && (
          <span className="ml-auto font-mono text-[10px] text-muted-foreground">{String(record.meta.input_id)}</span>
        )}
      </div>

      {/* 冻结快照(分析发起时的对账锚) */}
      {frozen && (
        <p className="border-b border-border/40 pb-2 text-[10px] text-muted-foreground">
          {tr("快照")} {frozen.symbol}-PERP · {tr(TF_LABELS[frozen.tf] ?? frozen.tf)}
          {frozen.lastClose != null && tr(" · 收盘 {v}", { v: frozen.lastClose.toLocaleString() })}
          {frozen.barsTotal != null && tr(" · {n} 根K线", { n: frozen.barsTotal })}
          {frozen.windowEnd != null && tr(" · 截至 {v}", { v: formatWindowEnd(frozen.windowEnd) })}
        </p>
      )}

      {/* order 金框(trade)/ 短路块(wait)/ 否决块(reject)互斥 */}
      {hasAnyPrice && decision ? (
        <div className="rounded-lg border border-[#e3b650]/30 bg-[#e3b650]/[0.04] px-2.5 py-2">
          <div className="flex items-baseline gap-2">
            <span className={cn(
              "text-[12px] font-semibold",
              dir === "short" ? "text-neg" : dir === "long" ? "text-pos" : "text-muted-foreground"
            )}>
              {dir === "short" ? tr("做空 · short") : dir === "long" ? tr("做多 · long") : tr("方向未定")}
            </span>
            {decision.confidence != null && Number.isFinite(decision.confidence) && (
              <span className="rounded border border-border/60 bg-muted/40 px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {tr("信心")} {Math.round(decision.confidence * 100)}%
              </span>
            )}
            {rr != null && (
              <span className="ml-auto text-[10px] text-muted-foreground">
                {tr("风险回报")} <span className="font-mono font-semibold text-foreground">1 : {rr.toFixed(1)}</span>
              </span>
            )}
          </div>
          <div className="mt-1">
            <OrderRow
              label={tr("入场")}
              labelCls="border-[#e3b650]/40 text-[#e3b650]"
              note={entryNote}
              value={decision.entry?.toLocaleString() ?? "—"}
            />
            <OrderRow
              label={tr("止损")}
              labelCls="border-[#EF5350]/40 text-[#EF5350]"
              note={decision.entry != null && decision.stop_loss != null ? atrNote(decision.stop_loss - decision.entry) : null}
              value={decision.stop_loss?.toLocaleString() ?? "—"}
            />
            <OrderRow
              label={tr("止盈")}
              labelCls="border-[#10b981]/40 text-[#10b981]"
              note={decision.entry != null && decision.take_profit != null ? atrNote(decision.take_profit - decision.entry) : null}
              value={decision.take_profit?.toLocaleString() ?? "—"}
            />
          </div>
        </div>
      ) : outcome === "reject" ? (
        <div className="rounded-lg border border-destructive/25 bg-destructive/[0.04] px-2.5 py-2">
          <p className="text-[10px] font-semibold tracking-[0.08em] text-destructive">{tr("硬性否决 · 不生成交易指令")}</p>
          <p className="mt-1 text-[11px] leading-5 text-foreground/80">{rejectReason || tr("否决条件命中,放弃本次入场。")}</p>
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-border/70 bg-background/40 px-2.5 py-2">
          <p className="text-[10px] font-semibold tracking-[0.08em] text-amber-300">{tr("无入场信号 · 不生成交易指令")}</p>
          <p className="mt-1 text-[11px] leading-5 text-foreground/80">{rejectReason || lastS1Reason || tr("等待入场信号后再评估。")}</p>
          {/* P2b:wait 态出路——重评时机与开关都在别处(图上倒计时+autoSubmit),这里点名 */}
          <p className="mt-1.5 border-t border-border/40 pt-1.5 text-[10px] leading-4 text-muted-foreground">
            {tr("本周期收盘后(见左侧倒计时)可重评——勾选「收盘后自动增量分析」即自动跑，否则手动再点一次分析。")}
          </p>
        </div>
      )}

      {/* 结果复盘(T3a):只有 win/loss 是已结算真值才着色;未结算保持中性不误导 */}
      {auditStatus && (
        <div
          data-testid="pa-outcome-audit"
          className="rounded-lg border border-border/60 bg-background/40 px-2.5 py-2"
        >
          <div className="flex flex-wrap items-center gap-1.5">
            <p className="text-[10px] font-semibold tracking-[0.08em] text-muted-foreground">{tr("结果复盘 · OUTCOME")}</p>
            <span className={cn(
              "rounded border px-1.5 py-0.5 text-[10px] font-medium",
              auditStatus === "win" ? "border-pos/40 bg-pos/10 text-pos"
                : auditStatus === "loss" ? "border-neg/40 bg-neg/10 text-neg"
                  : "border-border/60 bg-muted/40 text-muted-foreground"
            )}>
              {tr(OUTCOME_STATUS_LABELS[auditStatus] ?? auditStatus)}
            </span>
            {pnlR != null && (
              <span className={cn("ml-auto font-mono text-[11px] font-semibold", pnlR >= 0 ? "text-pos" : "text-neg")}>
                {pnlR >= 0 ? "+" : ""}{pnlR.toFixed(2)} R
              </span>
            )}
          </div>
          {(auditStatus === "expired" || auditStatus === "timeout_open") && (
            <p className="mt-1 text-[10px] leading-4 text-muted-foreground">
              {auditStatus === "expired"
                ? tr("入场价在观察窗内未被触及 — 未开仓,不计盈亏。")
                : tr("已入场,但窗口结束时止损/止盈均未触及 — 结果未定,不作盈亏解读。")}
            </p>
          )}
        </div>
      )}

      {/* next_period 下周期预告(任意 outcome 均可携带) */}
      {nextPeriod && (
        <div className="rounded-lg border border-border/60 bg-background/40 px-2.5 py-2">
          <p className="text-[10px] font-semibold tracking-[0.08em] text-muted-foreground">{tr("下周期预告 · NEXT PERIOD")}</p>
          <div className="mt-1 space-y-0.5 text-[11px]">
            {nextPeriod.direction && (
              <p>
                <span className="text-muted-foreground">{tr("倾向")}</span>{" "}
                <span className="text-foreground/90">{tr(NEXT_PERIOD_LABELS[nextPeriod.direction] ?? nextPeriod.direction)}</span>
              </p>
            )}
            {nextPeriod.watch_levels?.length ? (
              <p>
                <span className="text-muted-foreground">{tr("观察位")}</span>{" "}
                <span className="font-mono text-foreground/90">{nextPeriod.watch_levels.map((v) => v.toLocaleString()).join(" / ")}</span>
              </p>
            ) : null}
            {nextPeriod.invalidate_if && (
              <p>
                <span className="text-muted-foreground">{tr("失效条件")}</span>{" "}
                <span className="text-foreground/90">{nextPeriod.invalidate_if}</span>
              </p>
            )}
          </div>
        </div>
      )}

      {/* 双 trace 竖向连线时间轴 */}
      <TraceTimeline trace={s1Trace} title={tr("Stage 1 诊断链")} />
      {diag2?.trace?.length ? <TraceTimeline trace={diag2.trace} title={tr("Stage 2 决策链")} /> : null}
      <OverridesList
        overrides={[
          ...(diag1?.node_overrides ?? []).map((o) => ({ ...o, stage: "Stage 1" as const })),
          ...(diag2?.node_overrides ?? []).map((o) => ({ ...o, stage: "Stage 2" as const })),
        ]}
      />

      {/* 原始 LLM I/O(audit/replay,默认收起) */}
      {record.stage1?.messages?.length ? (
        <RawMessagesBlock title={tr("查看原始 prompt/response · Stage 1")} messages={record.stage1.messages} />
      ) : null}
      {record.stage2?.messages?.length ? (
        <RawMessagesBlock title={tr("查看原始 prompt/response · Stage 2")} messages={record.stage2.messages} />
      ) : null}

      {/* meta 行 */}
      {(record.strategy_files_used?.length || usage.total_tokens || atr14 != null) && (
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 border-t border-border/40 pt-2 text-[10px] text-muted-foreground">
          {record.strategy_files_used?.length ? (
            <span>{tr("物料")} <span className="font-mono text-foreground/70">{record.strategy_files_used.join(" · ")}</span></span>
          ) : null}
          {usage.total_tokens ? (
            <span>
              tokens <span className="font-mono text-foreground/70">
                {usage.prompt_tokens ?? 0} {tr("入 /")} {usage.completion_tokens ?? 0} {tr("出 / 共")} {usage.total_tokens}
              </span>
            </span>
          ) : null}
          {atr14 != null && <span>ATR14 <span className="font-mono text-foreground/70">{atr14.toLocaleString()}</span></span>}
        </div>
      )}
    </div>
  );
}

// 诚实加载态:端点单次调用无服务端阶段进度,只显示静态文案(不做假阶段推进)
const PA_LOADING_TEXT = "两阶段管线运行中(单次调用,约 1-3 分钟)";

function formatElapsed(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function buildOverlays(
  decision: PaDecision | null,
  markPrice: number | null,
  tr: Translate = identityTranslate,
): DecisionOverlay[] {
  // 现价参考线独立于决策：即使 LLM 未产出结构化 JSON（parse 失败），仍画现价线兜底
  const overlays: DecisionOverlay[] = [];
  if (markPrice != null && Number.isFinite(markPrice) && markPrice > 0) {
    overlays.push({ price: markPrice, color: "#a0a8b8", title: tr("现价 {p}", { p: markPrice.toLocaleString() }) });
  }
  if (!decision) return overlays;
  if (decision.entry != null && Number.isFinite(decision.entry) && decision.entry > 0) {
    overlays.push({ price: decision.entry, color: "#e3b650", title: tr("入场 {p}", { p: decision.entry.toLocaleString() }) });
  }
  if (decision.stop_loss != null && Number.isFinite(decision.stop_loss) && decision.stop_loss > 0) {
    overlays.push({ price: decision.stop_loss, color: "#EF5350", title: tr("SL {p}", { p: decision.stop_loss.toLocaleString() }) });
  }
  if (decision.take_profit != null && Number.isFinite(decision.take_profit) && decision.take_profit > 0) {
    overlays.push({ price: decision.take_profit, color: "#10b981", title: tr("TP {p}", { p: decision.take_profit.toLocaleString() }) });
  }
  return overlays;
}

// 单次模拟的 R:R 恒定，分桶键为 "3.0" 之类；这里展示最近一次模拟的 R:R
function pct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(0)}%`;
}

function num(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

function SetupBacktestCard({
  result,
  attached,
}: {
  result: SetupBacktestResult;
  attached?: boolean;
}) {
  const tr = useUiText()

  if (result.no_trigger) {
    return (
      <div className="mt-1.5 rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-200">
        <p className="font-medium">{tr("历史无此价位触发，样本不足")}</p>
        {result.reason && <p className="mt-0.5 text-[10px] text-muted-foreground">{result.reason}</p>}
      </div>
    );
  }
  if (result.total_setups === 0) {
    return (
      <div className="mt-1.5 rounded-xl border border-border/70 bg-background/40 px-3 py-2 text-[11px] text-muted-foreground">
        <p className="font-medium">{tr("历史样本不足")}</p>
        {result.reason && <p className="mt-0.5 text-[10px]">{result.reason}</p>}
      </div>
    );
  }

  const buckets = Object.entries(result.rr_buckets ?? {});

  return (
    <div className="mt-1.5 rounded-xl border border-primary/20 bg-background/40 px-3 py-2">
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-medium text-foreground">{tr("历史回测 ·")} {result.total_setups} {tr("个样本")}</p>
        <span className="flex items-center gap-2">
          {attached && (
            <span className="text-[10px] text-emerald-400/80">{tr("已挂诊断记录")}</span>
          )}
          {result.open_setups > 0 && (
            <span className="text-[10px] text-muted-foreground">{result.open_setups} {tr("个未收敛")}</span>
          )}
        </span>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
        <span className="text-muted-foreground">{tr("胜率")}</span>
        <span className="text-right font-mono text-foreground">{pct(result.win_rate)}</span>
        <span className="text-muted-foreground">{tr("盈亏比(RR)")}</span>
        <span className="text-right font-mono text-foreground">{buckets[0]?.[0] ?? "—"}</span>
        <span className="text-muted-foreground">{tr("期望")}</span>
        <span className="text-right font-mono text-foreground">{num(result.expectancy)}R</span>
        <span className="text-muted-foreground">{tr("最大回撤")}</span>
        <span className="text-right font-mono text-foreground">{num(result.max_drawdown)}R</span>
      </div>
      {buckets.length > 0 && (
        <div className="mt-2">
          <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">{tr("R:R 分桶")}</p>
          <div className="mt-1 space-y-1">
            {buckets.map(([rr, b]) => {
              // 样本量:优先用分桶自带 count,否则 wins+losses;<5 诚实标注
              const sample = Number.isFinite(b.count) && b.count > 0 ? b.count : b.wins + b.losses;
              const thin = sample < 5;
              return (
                <div key={rr} className="flex items-center justify-between text-[10px]">
                  <span className="flex items-center gap-1.5 text-muted-foreground">
                    <span>
                      {b.wins} {tr("胜 /")} {b.losses} {tr("负")}
                    </span>
                    <span>
                      {tr("样本")} {sample}
                      {thin && <span className="ml-0.5">{tr("(样本不足)")}</span>}
                    </span>
                  </span>
                  <span className="font-mono text-foreground">{tr("胜率")} {pct(b.win_rate)}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export function PaPanel() {
  const tr = useUiText()

  // 局部 symbol/tf：从全局 store 初始化，改这里不写回全局（避免污染 watch 页）
  const globalPageFilter = useMarketStore((s) => s.pageFilter);
  const [symbol, setSymbol] = useState(globalPageFilter.symbol || "BTC");
  const [tf, setTf] = useState(globalPageFilter.tf || "1h");
  // 交易倾向(4 档):透传后端 trading_stance,只影响 Stage2 决策倾向段。
  const [stance, setStance] = useState("balanced");

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [generating, setGenerating] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>(undefined);
  const [decision, setDecision] = useState<PaDecision | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [analyzedSymbol, setAnalyzedSymbol] = useState<string | null>(null);
  const [analyzedTf, setAnalyzedTf] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);

  // 结构化两阶段管线:真 LLM 双调用(1-3 分钟),与旧 chat 追问双轨并存
  const [paRecord, setPaRecord] = useState<AnalyzeResponse | null>(null);
  const [paFrozen, setPaFrozen] = useState<FrozenSnapshot | null>(null);
  const [paLoading, setPaLoading] = useState(false);
  const [paElapsedSec, setPaElapsedSec] = useState(0);
  const [paError, setPaError] = useState<string | null>(null);
  // P2c:进行中分析的 AbortController(与 chat 流的 abortRef 各管各的)
  const paAbortRef = useRef<AbortController | null>(null);
  // 收盘归零自动跑增量(opt-in,默认关;一次触发即熄火见 handleCloseElapsed)
  const [autoSubmit, setAutoSubmit] = useState(false);

  // W3-B 保鲜差距:AI 诊断窗口末 → epoch 秒,传给 KfChart 数「+N 根新K线」。
  // 后端 window_end_iso 名不副实——真管线写的是 ms 数值(klines.db timestamp),
  // 老记录/demo 可能是字符串:数字 /1000,字符串 Date.parse,都失败静默不显示。
  const staleAsOfEpoch: number | null = (() => {
    const raw = paRecord?.input_data?.summary?.window_end_iso;
    if (raw == null || raw === "") return null;
    if (typeof raw === "number" && Number.isFinite(raw)) return Math.floor(raw / 1000);
    if (typeof raw === "string") {
      const parsed = Date.parse(raw);
      return Number.isNaN(parsed) ? null : Math.floor(parsed / 1000);
    }
    return null;
  })();

  // setup 回测状态（决策生成后手动触发）
  const [backtesting, setBacktesting] = useState(false);
  const [backtestResult, setBacktestResult] = useState<SetupBacktestResult | null>(null);
  const [backtestError, setBacktestError] = useState<string | null>(null);

  // 锚定追问(Phase D):问题对着已落库的那条记录作答,问答对追加回同一条记录。
  // 列表本身存在 paRecord.followups(单一真值来源),不另设本地副本。
  const [followUpLoading, setFollowUpLoading] = useState(false);
  const [followUpError, setFollowUpError] = useState<string | null>(null);

  // S7f 到价提醒：单次 POST，成功即"已设置"（诚实：不发火、不轮询确认）
  const [alertState, setAlertState] = useState<"idle" | "loading" | "done">("idle");
  const [alertError, setAlertError] = useState<string | null>(null);

  // 最近诊断(结算回看):列表 + 回看态。回看 = 决策卡整体替换为那条记录的
  // 快照,顶部横条可返回;重跑分析/autoSubmit 天然把 paRecord 换回"当前"。
  const [recentRecords, setRecentRecords] = useState<RecordListItem[] | null>(null);
  const [recentScope, setRecentScope] = useState<"current" | "all">("current");
  const [recentLoading, setRecentLoading] = useState(false);
  const [recentError, setRecentError] = useState<string | null>(null);
  const [reviewingId, setReviewingId] = useState<string | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

  // 左栏视图：图表（KfChart）或决策树（B5 可视化）
  const [leftView, setLeftView] = useState<"chart" | "tree">("chart");

  const messagesRef = useRef<ChatMessage[]>([]);
  const sessionIdRef = useRef<string | undefined>(undefined);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  messagesRef.current = messages;
  sessionIdRef.current = sessionId;

  // B0 确认门:与 FAB 面/对话页/终端/策略侧栏共用同一份审批状态机
  // (此前本面 onEvent 未处理 tool_approval_request → 写工具静默挂起到超时)。
  const {
    pendingApprovals,
    approvingIds,
    approvalError,
    handleApprovalEvent,
    approve,
  } = useToolApproval({ getSessionId: () => sessionIdRef.current });

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
      paAbortRef.current?.abort();
    };
  }, []);

  // 当前价 → mark_price（喂给 LLM 做参照 + 图上现价参考线）
  const tradingSym = /USDT$|USD$|PERP$/.test(symbol) ? symbol : `${symbol}USDT`;
  const { data: tickerData } = useApi<TickerData>(
    `/api/v1/markets/${encodeURIComponent(tradingSym)}/ticker`
  );
  const markPrice = tickerData?.last_price ?? null;

  const overlays = buildOverlays(decision, markPrice, tr);

  // 锚定追问的锚点:记录带 input_id 才谈得上"对着这条记录追问"
  const recordId = paRecord?.meta?.input_id ?? null;
  const followUps: PaFollowUp[] = paRecord?.followups ?? [];

  const applyDecision = useCallback((d: PaDecision | null) => {
    setDecision(d);
    setBacktestResult(null);
    setBacktestError(null);
    setAlertState("idle");
    setAlertError(null);
  }, []);

  const canSave = !!decision?.entry && !!decision?.stop_loss && !!decision?.take_profit;
  // 只有真交易指令(trade + 三价位齐)才可保存/回测;wait/reject 只读展示
  const outcome = paRecord?.stage2?.diagnosis_or_decision?.terminal_outcome ?? null;
  const outcomeCanSave = outcome === "trade" && canSave;

  const saveExperiment = useCallback(async () => {
    if (!decision || saving) return;
    setSaving(true);
    setSaveError(null);
    setSavedId(null);
    try {
      const created = await createExperiment({
        // name/hypothesis 是写进实验库的**数据**(非即时 DOM),但页面上会回显,
        // 故按当前 locale 写入;tr 缺席(纯函数测试)时 identityTranslate 落回中文。
        name: `${symbol} ${tr(TF_LABELS[tf] ?? tf)} ${tr("PA 形态")}`,
        hypothesis: decision.reason || tr("AI 识别 {sym} {tf} 价格行为形态", { sym: symbol, tf: tr(TF_LABELS[tf] ?? tf) }),
        symbol: `${symbol}/USDT`,
        timeframe: tf,
        dataset: "klines.db (source=auto)",
        strategy_version: "pa-decision-v1",
        setup_params: {
          direction: decision.direction,
          entry: decision.entry,
          stop_loss: decision.stop_loss,
          take_profit: decision.take_profit,
        },
        backtest_result: backtestResult ?? undefined,
      });
      setSavedId(created.id);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }, [decision, saving, symbol, tf, backtestResult]);

  const runSetupBacktestFn = useCallback(async () => {
    if (!decision?.entry || !decision?.stop_loss || !decision?.take_profit || backtesting) return;
    setBacktesting(true);
    setBacktestError(null);
    setBacktestResult(null);
    try {
      const result = await runSetupBacktest({
        // 后端 get_klines 以统一符号（BASE/QUOTE）作 SQLite 分区键，须与存储格式一致
        symbol: `${symbol}/USDT`,
        timeframe: tf,
        exchange: "binance",
        market_type: "futures",
        entry: decision.entry,
        stop_loss: decision.stop_loss,
        take_profit: decision.take_profit,
        direction: decision.direction,
        input_id: paRecord?.meta?.input_id ?? undefined,
      });
      setBacktestResult(result);
    } catch (err) {
      setBacktestError(err instanceof Error ? err.message : String(err));
    } finally {
      setBacktesting(false);
    }
  }, [decision, backtesting, symbol, tf, paRecord]);

  const createPriceAlertFn = useCallback(async () => {
    if (!decision || alertState === "loading" || alertState === "done") return;
    setAlertState("loading");
    setAlertError(null);
    const symbolForAlert = analyzedSymbol ?? `${symbol}/USDT`;
    const intervalForAlert = analyzedTf ?? tf;
    const levels: PriceAlertRequest["levels"] = {};
    if (decision.entry != null) levels.entry = decision.entry;
    if (decision.stop_loss != null) levels.stop_loss = decision.stop_loss;
    if (decision.take_profit != null) levels.take_profit = decision.take_profit;
    const body: PriceAlertRequest = {
      symbol: symbolForAlert.includes("/") ? symbolForAlert : `${symbolForAlert}/USDT`,
      interval: intervalForAlert,
      direction: decision.direction ?? null,
      input_id: paRecord?.meta?.input_id ?? null,
      session_id: paRecord?.meta?.session_id ?? null,
      levels,
    };
    try {
      await createPriceAlert(body);
      setAlertState("done");
    } catch (err) {
      setAlertError(httpErrorMessage(err));
      setAlertState("idle");
    }
  }, [decision, alertState, analyzedSymbol, analyzedTf, symbol, tf, paRecord]);

  const runAnalysis = useCallback(async (demo = false, incremental = false) => {
    if (generating || paLoading) return;
    setPaError(null);
    setPaRecord(null);
    setPaFrozen(null);
    const frozen: FrozenSnapshot = {
      symbol,
      tf,
      markPrice,
      barsTotal: null,
      lastClose: null,
      windowEnd: null,
    };
    setPaFrozen(frozen);
    setPaLoading(true);
    setPaElapsedSec(0);
    setAnalyzedSymbol(symbol);
    setAnalyzedTf(tf);
    applyDecision(null);
    // P2c:1-3 分钟双 LLM 调用可取消。abort 时 api() 抛 DOMException AbortError。
    const controller = new AbortController();
    paAbortRef.current = controller;

    try {
      const record = await analyzeTrading({
        symbol: `${symbol}/USDT`,
        interval: tf,
        bars: 120,
        market_type: "futures",
        trading_stance: stance,
        htf_interval: tf === "1h" ? "4h" : tf === "4h" ? "1d" : null,
        demo,
        // 缺省不发该字段:既有各路请求体逐字节不变(后端默认 False 等价)
        ...(incremental ? { incremental: true } : {}),
      }, controller.signal);
      if (!mountedRef.current) return;
      setPaRecord(record);
      const snap = record.input_data?.summary;
      setPaFrozen({
        ...frozen,
        barsTotal: snap?.bars_total ?? null,
        lastClose: snap?.last_close ?? null,
        windowEnd: snap?.window_end_iso ?? null,
      });
      applyDecision(decisionFromRecord(record));
      const msgs = record.input_data?.summary;
      const inputId = record.meta?.input_id;
      setMessages((prev) => [
        ...prev,
        {
          id: newMessageId(),
          role: "user",
          content: tr("📊 分析 {sym} · {tf} 图表({mode})", { sym: symbol, tf: tr(TF_LABELS[tf] ?? tf), mode: incremental ? tr("增量续接") : tr("两阶段管线") }),
        },
        {
          id: newMessageId(),
          role: "assistant",
          content: inputId
            ? tr("两阶段诊断完成。窗口 {a} → {b},共 {n} 根 K 线。结构化结果见下方决策卡,可直接追问。", { a: msgs?.window_start_iso ?? "?", b: msgs?.window_end_iso ?? "?", n: msgs?.bars_total ?? "?" })
            : tr("两阶段诊断完成,但记录缺少 input_id。"),
        },
      ]);
    } catch (err) {
      if (mountedRef.current && !(err instanceof DOMException && err.name === "AbortError")) {
        setPaError(httpErrorMessage(err));
      }
    } finally {
      if (paAbortRef.current === controller) paAbortRef.current = null;
      if (mountedRef.current) setPaLoading(false);
    }
  }, [generating, paLoading, symbol, tf, stance, markPrice, applyDecision]);

  // P2c:取消进行中的分析。请求被 abort 后后端可能仍在跑(LLM 已计费),
  // 前端只断开等待——错误横幅不弹,加载卡随 paLoading 熄灭。
  const cancelAnalysis = useCallback(() => {
    paAbortRef.current?.abort();
  }, []);

  // 收盘倒计时归零(A3 语义:一次触发即熄火,不排队重试 —— 无重复烧钱风险)。
  // runAnalysis 的 `if (generating || paLoading) return` 是静默吞,自动路撞上分析中
  // 必须补一条告知,否则用户看到"勾了却没反应"。
  const handleCloseElapsed = useCallback(() => {    setAutoSubmit(false);
    if (generating || paLoading) {
      setMessages((prev) => [
        ...prev,
        {
          id: newMessageId(),
          role: "assistant",
          content: tr("收盘已到，但分析进行中，跳过自动增量提交（未排队重试）"),
        },
      ]);
      return;
    }
    void runAnalysis(false, true);
  }, [generating, paLoading, runAnalysis]);

  // 加载态真实计时:每秒 +1,分析结束(paLoading 复位)自动停止
  useEffect(() => {
    if (!paLoading) return;
    const timer = setInterval(() => setPaElapsedSec((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, [paLoading]);

  // ---- 最近诊断(结算回看) ----
  const loadRecent = useCallback(async (scope: "current" | "all") => {
    setRecentLoading(true);
    setRecentError(null);
    try {
      const rows = await listRecords(
        scope === "current" ? { symbol: `${symbol}/USDT`, interval: tf, limit: 50 } : { limit: 50 },
      );
      if (mountedRef.current) setRecentRecords(rows);
    } catch (err) {
      if (mountedRef.current) setRecentError(httpErrorMessage(err));
    } finally {
      if (mountedRef.current) setRecentLoading(false);
    }
  }, [symbol, tf]);

  // 首次挂载拉一次;分析成功落库后也刷新(新记录自然进列表)
  useEffect(() => {
    void loadRecent(recentScope);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只在挂载/分析完成后刷,scope 变化走 onChange
  }, [paRecord?.meta?.input_id]);

  const openRecordReview = useCallback(async (row: RecordListItem) => {
    setReviewLoading(true);
    setReviewError(null);
    try {
      const full = await getDecisionRecord(row.record_id);
      if (!mountedRef.current) return;
      setPaRecord(full);
      setDecision(decisionFromRecord(full));
      setReviewingId(row.record_id);
      const snap = full.input_data?.summary;
      setPaFrozen({
        symbol: snap?.symbol?.replace(/\/USDT$/, "") ?? symbol,
        tf: snap?.interval ?? tf,
        markPrice: null,
        barsTotal: snap?.bars_total ?? null,
        lastClose: snap?.last_close ?? null,
        windowEnd: snap?.window_end_iso ?? null,
      });
      setAnalyzedSymbol(snap?.symbol?.replace(/\/USDT$/, "") ?? null);
      setAnalyzedTf(snap?.interval ?? null);
    } catch (err) {
      if (mountedRef.current) setReviewError(httpErrorMessage(err));
    } finally {
      if (mountedRef.current) setReviewLoading(false);
    }
  }, [symbol, tf]);

  const exitRecordReview = useCallback(() => {
    setReviewingId(null);
    setReviewError(null);
  }, []);

  const sendFollowUp = useCallback(async () => {
    const text = input.trim();
    if (!text || generating || followUpLoading) return;

    // 锚定追问:已有落库记录时,问题交给服务端对着那条记录作答 —— 上下文由服务端
    // 从冻结快照组,问答对也追加回同一条记录。前端不再拼 [最近诊断上下文] 前缀:
    // 本地拼的上下文会随本地状态漂移,锚定后不会。
    if (recordId) {
      setInput("");
      setFollowUpError(null);
      setFollowUpLoading(true);
      try {
        const res = await sendRecordFollowUp(recordId, text);
        if (mountedRef.current) {
          setPaRecord((prev) => (prev ? { ...prev, followups: res.followups } : prev));
        }
      } catch (err) {
        if (mountedRef.current) setFollowUpError(httpErrorMessage(err));
      } finally {
        if (mountedRef.current) setFollowUpLoading(false);
      }
      return;
    }

    setInput("");
    setStreamError(null);

    // S4: 无记录时退回通用聊天链路;聊天展示仍用用户原文
    const d = decision;
    const prefix = paRecord
      ? `[最近诊断上下文] ${analyzedSymbol ?? symbol} ${analyzedTf ?? tf} | 方向:${d?.direction ?? "—"} | entry:${d?.entry ?? "—"} | SL:${d?.stop_loss ?? "—"} | TP:${d?.take_profit ?? "—"} | 现价:${paFrozen?.lastClose ?? "—"}\n\n[问题] ${text}`
      : text;

    const userId = newMessageId();
    const assistantId = newMessageId();
    setMessages((prev) => [
      ...prev,
      { id: userId, role: "user", content: text },
      { id: assistantId, role: "assistant", content: "", reasoningSteps: [] },
    ]);
    setGenerating(true);

    const onEvent = (event: { type: string; data: unknown }) => {
      if (!mountedRef.current) return;
      // B0 确认门:审批请求/结果/结束交给共享状态机(与其余 agent 面同源)。
      if (
        event.type === "tool_approval_request" ||
        event.type === "tool_result" ||
        event.type === "done"
      ) {
        handleApprovalEvent(
          event.type,
          (typeof event.data === "object" && event.data !== null
            ? event.data
            : {}) as Record<string, unknown>,
        );
      }
      if (event.type === "thinking" || event.type === "tool_call" || event.type === "tool_result" || event.type === "reflection") {
        const step: ReasoningStep = {
          type: event.type as ReasoningStep["type"],
          data: typeof event.data === "object" && event.data !== null
            ? (event.data as Record<string, unknown>)
            : { content: String(event.data ?? "") },
          timestamp: Date.now(),
        };
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, reasoningSteps: [...(m.reasoningSteps ?? []), step] } : m))
        );
        return;
      }
      if (event.type === "text_delta" || event.type === "text") {
        const chunk = deltaTextFromData(event.data);
        if (!chunk) return;
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, content: m.content + chunk } : m))
        );
        return;
      }
      if (event.type === "error") {
        setStreamError(typeof event.data === "string" ? event.data : String(event.data ?? "Error"));
        setGenerating(false);
        return;
      }
      if (event.type === "done") {
        const doneData = event.data as Record<string, unknown> | undefined;
        if (doneData?.error) setStreamError(String(doneData.error));
        const d = doneData?.decision as PaDecision | undefined;
        if (d) applyDecision(d);
        setGenerating(false);
      }
    };

    const controller = new AbortController();
    abortRef.current = controller;
    const timeout = setTimeout(() => controller.abort(), 10 * 60 * 1000);

    try {
      const result = await sendAgentChat(
        prefix, onEvent, undefined, sessionIdRef.current, controller.signal,
        // B0 门会让后端 producer 阻塞等人裁决(流不关),审批卡此刻已可点。
        // session id 随 X-Session-ID 响应头先到 —— 立刻回报,否则新会话首轮的
        // getSessionId() 读到 undefined,点「批准」静默 no-op。
        {
          onSessionId: (sid) => {
            sessionIdRef.current = sid;
            if (mountedRef.current) setSessionId(sid);
          },
        },
      );
      if (mountedRef.current && result.sessionId) {
        setSessionId(result.sessionId);
        sessionIdRef.current = result.sessionId;
      }
    } finally {
      clearTimeout(timeout);
      abortRef.current = null;
      if (mountedRef.current) setGenerating(false);
    }
  }, [generating, input, followUpLoading, recordId, applyDecision, decision, paRecord, paFrozen, analyzedSymbol, analyzedTf, symbol, tf]);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, generating]);

  const latestAssistant = [...messages].reverse().find((m) => m.role === "assistant");

  return (
    <div className="pa-layout grid h-full min-h-0 gap-3 p-3" style={{ gridTemplateColumns: "1fr var(--rail-w)" }}>
      {/* 左：图表 */}
      <div className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-border/70 bg-background/35">
        <HeartbeatStrip className="mx-3 mt-2 shrink-0" />
        <div className="flex shrink-0 items-center gap-2 border-b border-border/60 px-3 py-2">
          <span className="text-[13px] font-semibold text-foreground">{tr("价格行为分析")}</span>
          <span className="rounded bg-muted/60 px-1.5 py-0.5 text-[10px] text-muted-foreground">
            {symbol}-PERP · {tr(TF_LABELS[tf] ?? tf)}
          </span>
          {analyzedSymbol && (
            <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
              {tr("AI 已分析")} {analyzedSymbol} · {tr(TF_LABELS[analyzedTf ?? tf] ?? analyzedTf)}
            </span>
          )}
          <div className="ml-auto flex items-center gap-1.5">
            <div className="flex h-7 items-center rounded-lg border border-border/70 bg-background/60 p-0.5">
              <button
                type="button"
                onClick={() => setLeftView("chart")}
                className={cn(
                  "flex h-6 items-center gap-1 rounded-md px-2 text-[11px] transition-colors",
                  leftView === "chart" ? "bg-primary/15 text-primary" : "text-muted-foreground hover:text-foreground"
                )}
              >
                <LineChart className="h-3.5 w-3.5" /> {tr("图表")}
              </button>
              <button
                type="button"
                onClick={() => setLeftView("tree")}
                className={cn(
                  "flex h-6 items-center gap-1 rounded-md px-2 text-[11px] transition-colors",
                  leftView === "tree" ? "bg-primary/15 text-primary" : "text-muted-foreground hover:text-foreground"
                )}
              >
                <GitBranch className="h-3.5 w-3.5" /> {tr("决策树")}
              </button>
            </div>
            <select
              aria-label="交易标的"
              className="h-7 rounded-lg border border-border/70 bg-background/60 px-1.5 text-[11px] font-mono text-foreground outline-none"
              value={symbol}
              onChange={(e) => { setSymbol(e.target.value); setDecision(null); setAnalyzedSymbol(null); setPaRecord(null); setPaFrozen(null); setPaError(null); }}
              disabled={generating}
            >
              {["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK"].map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            <select
              aria-label="K线周期"
              className="h-7 rounded-lg border border-border/70 bg-background/60 px-1.5 text-[11px] font-mono text-foreground outline-none"
              value={tf}
              onChange={(e) => { setTf(e.target.value); setDecision(null); setAnalyzedSymbol(null); setPaRecord(null); setPaFrozen(null); setPaError(null); }}
              disabled={generating}
            >
              {SUPPORTED_TFS.map((t) => (
                <option key={t} value={t}>{tr(TF_LABELS[t] ?? t)}</option>
              ))}
            </select>
            <select
              className="h-7 rounded-lg border border-border/70 bg-background/60 px-1.5 text-[11px] text-foreground outline-none"
              value={stance}
              onChange={(e) => setStance(e.target.value)}
              disabled={generating || paLoading}
              title="交易倾向:只改变 Stage2 决策段的激进程度,不影响 Stage1 诊断"
              aria-label="交易倾向"
            >
              {STANCE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{tr(o.label)}</option>
              ))}
            </select>
            <Button
              size="sm"
              className="h-7 gap-1 rounded-lg text-[11px]"
              onClick={() => void runAnalysis()}
              disabled={generating || paLoading}
            >
              {paLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <LineChart className="h-3.5 w-3.5" />}
              {paLoading ? tr("分析中…") : tr("分析当前图表")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 gap-1 rounded-lg text-[11px]"
              onClick={() => void runAnalysis(true)}
              disabled={generating || paLoading}
              title={tr("跑真实两阶段管线并打演示标 — 结果不入审计表,不参与事后结果判定")}
            >
              {paLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
              {paLoading ? tr("运行中…") : tr("演示一轮(真 LLM)")}
            </Button>
            <label
              className="flex cursor-pointer select-none items-center gap-1.5 text-[11px] text-muted-foreground"
              title={tr("收盘倒计时归零时自动跑一次增量分析(续接最近一次同品种/周期诊断)。触发一次后自动取消勾选。")}
            >
              <Checkbox
                checked={autoSubmit}
                onCheckedChange={(v) => setAutoSubmit(v === true)}
                disabled={generating || paLoading}
              />
              {tr("收盘后自动增量分析")}
            </label>
          </div>
        </div>
        <div className="min-h-0 flex-1">
          {leftView === "chart" ? (
            <KfChart
              symbolOverride={reviewingId ? (analyzedSymbol ?? symbol) : symbol}
              tfOverride={reviewingId ? (analyzedTf ?? tf) : tf}
              decisionOverlays={overlays}
              // P1#2 冻结锚对齐:回看历史记录时也冻结——锚=该记录窗口末
              // (kf-chart 内 resolveFrozenAnchor 优先取 staleAsOf),图=AI 当时
              // 所见,新 bar 被 clamp 且由保鲜徽章如实报差距。退出回看解冻。
              // 品种/周期随记录走(全部品种作用域点行,选择器不动)。
              frozen={paLoading || reviewingId != null}
              staleAsOf={staleAsOfEpoch}
              autoSubmit={autoSubmit}
              onCloseElapsed={handleCloseElapsed}
            />
          ) : (
            <PaDecisionTreeView treeId="trading_decision" />
          )}
        </div>
      </div>

      {/* 右：AI 面板 */}
      <div className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-border/70 bg-background/35">
        <div className="flex shrink-0 items-center gap-2 border-b border-border/60 px-3 py-2">
          <Bot className="h-4 w-4 text-primary" />
          <span className="text-[13px] font-semibold text-foreground">{tr("AI 分析")}</span>
          {sessionId && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-emerald-400" title={tr("会话已建立")} />}
        </div>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3">
          {messages.length === 0 && !paLoading && !paRecord && (
            <div className="flex flex-col items-center justify-center py-10 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-primary/15 bg-primary/10">
                <Sparkles className="h-6 w-6 text-primary" />
              </div>
              <h3 className="mt-3 text-sm font-semibold text-foreground">{tr("分析当前图表")}</h3>
              <p className="mt-1 max-w-[240px] text-[12px] leading-5 text-muted-foreground">
                {tr("选择品种和周期，点击\"分析当前图表\"。两阶段管线基于最近 120 根 K 线做结构诊断与入场决策（真 LLM，约 1-3 分钟），结果画到图上。")}
              </p>
            </div>
          )}

          {paError && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-[12px] text-destructive">
              <p className="flex items-center gap-1.5 font-medium"><AlertTriangle className="h-3.5 w-3.5" /> {tr("两阶段分析失败")}</p>
              <p className="mt-1 text-[11px] leading-5 text-destructive/90">{paError}</p>
              <button
                type="button"
                onClick={() => void runAnalysis()}
                disabled={paLoading}
                className="mt-2 rounded-md border border-destructive/40 bg-destructive/10 px-2 py-1 text-[11px] text-destructive transition-colors hover:bg-destructive/20 disabled:pointer-events-none disabled:opacity-50"
              >
                {tr("重试")}
              </button>
            </div>
          )}

          {paLoading && (
            <div className="rounded-xl border border-primary/20 bg-primary/5 px-3 py-2.5">
              <div className="flex items-center gap-2 text-[12px] text-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                {tr(PA_LOADING_TEXT)}
              </div>
              <p className="mt-2 font-mono text-[10px] text-muted-foreground">
                {tr("已运行")} {formatElapsed(paElapsedSec)}
              </p>
              <div className="mt-1.5 flex items-center justify-between gap-2">
                <p className="text-[10px] text-muted-foreground">{tr("真实双阶段 LLM 调用，请勿关闭页面。")}</p>
                {/* P2c:长任务可取消(前端断开等待;后端 LLM 已发起的调用可能仍计费) */}
                <button
                  type="button"
                  onClick={cancelAnalysis}
                  className="shrink-0 rounded-md border border-border/60 bg-background/40 px-2 py-0.5 text-[10px] text-muted-foreground transition-colors hover:border-destructive/40 hover:text-destructive"
                >
                  {tr("取消分析")}
                </button>
              </div>
            </div>
          )}

          {streamError && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-[12px] text-destructive">
              {streamError}
            </div>
          )}

          {messages.map((msg) => (
            <div key={msg.id} className={cn("flex", msg.role === "user" ? "justify-end" : "justify-start")}>
              <div
                className={cn(
                  "max-w-[94%] rounded-2xl border px-3 py-2.5 text-[13px] leading-6 shadow-sm",
                  msg.role === "user"
                    ? "border-primary/20 bg-primary/10 text-foreground"
                    : "border-border/70 bg-background/65 text-muted-foreground"
                )}
              >
                {msg.role === "user" ? (
                  <p className="whitespace-pre-wrap text-foreground">{msg.content}</p>
                ) : (
                  <>
                    <ReasoningTimeline steps={msg.reasoningSteps ?? []} />
                    {!!msg.content && <div className="pa-ai-richtext">{parseRichMarkdownToReact(msg.content, generating)}</div>}
                  </>
                )}
              </div>
            </div>
          ))}

          {generating && (
            <div className="flex items-center gap-2 rounded-xl border border-border/70 bg-background/40 px-3 py-2 text-[12px] text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              {tr("正在分析 K 线…")}
            </div>
          )}

          {/* B0 确认门:写工具被后端拦下,等人裁决(此前本面不渲染 → 静默挂起) */}
          {pendingApprovals.length > 0 && (
            <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-3 py-2.5">
              <p className="mb-1.5 text-[12px] font-medium text-amber-200">
                {tr("Agent 请求执行以下操作，需要确认")}
              </p>
              {approvalError && (
                <p className="mb-1.5 text-[11px] text-destructive">{formatPending(approvalError, tr)}</p>
              )}
              <div className="space-y-2">
                {pendingApprovals.map((p) => (
                  <div key={p.toolCallId} className="rounded-lg border border-border/70 bg-background/60 px-2.5 py-2">
                    <p className="text-[12px] font-medium text-foreground">{p.toolName}</p>
                    <p className="mt-0.5 break-all font-mono text-[11px] leading-4 text-muted-foreground line-clamp-3">
                      {Object.keys(p.arguments).length > 0
                        ? JSON.stringify(p.arguments)
                        : tr("（无参数）")}
                    </p>
                    <div className="mt-2 flex gap-2">
                      <Button
                        size="sm"
                        className="h-7 px-3 text-[11px]"
                        disabled={approvingIds.has(p.toolCallId)}
                        onClick={() => void approve(p.toolCallId, true)}
                      >
                        {tr("批准")}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-3 text-[11px] text-muted-foreground hover:text-destructive"
                        disabled={approvingIds.has(p.toolCallId)}
                        onClick={() => void approve(p.toolCallId, false)}
                      >
                        {tr("拒绝")}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* 只要求 paRecord:无决策的记录也要有"返回当前"出口,否则回看态被困 */}
        {reviewingId && paRecord && (
          <div
            data-testid="pa-review-banner"
            className="shrink-0 border-b border-border/60 bg-primary/5 px-3 py-1.5"
          >
            <div className="flex items-center gap-2">
              <History className="h-3 w-3 shrink-0 text-primary" />
              <p className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">
                {tr("正在回看")} <span className="font-mono text-foreground">{reviewingId}</span> {tr("的历史诊断(非当前图表)")}
              </p>
              <button
                type="button"
                onClick={exitRecordReview}
                className="shrink-0 rounded-md border border-border/60 bg-background/40 px-2 py-0.5 text-[10px] text-muted-foreground transition-colors hover:border-primary/30 hover:text-primary"
              >
                {tr("返回当前")}
              </button>
            </div>
          </div>
        )}

        {decision && paRecord && (
          <div className="shrink-0 border-t border-border/60 px-3 py-2">
            <PaDecisionRecordCard record={paRecord} frozen={paFrozen} decision={decision} />
            {followUps.length > 0 && (
              <details
                data-testid="pa-followup-section"
                className="mt-2 rounded-xl border border-border/70 bg-background/40"
              >
                <summary className="cursor-pointer select-none px-3 py-2 text-[11px] font-medium text-muted-foreground">
                  {tr("追问记录 ·")} {followUps.length} {tr("条")}
                </summary>
                <ul className="space-y-2 border-t border-border/60 px-3 py-2">
                  {followUps.map((f, i) => (
                    <li key={`${i}-${f.at ?? ""}`} className="text-[11px] leading-5">
                      <p className="text-foreground">
                        {tr("问:")}{f.question}
                        {f.used_live === true && (
                          <span
                            data-testid="pa-followup-used-live"
                            className="ml-1.5 rounded border border-border/60 bg-surface-1 px-1 py-px align-middle text-[10px] font-medium text-muted-foreground"
                          >
                            {tr("含实时读数")}
                          </span>
                        )}
                      </p>
                      <p className="mt-0.5 whitespace-pre-wrap text-muted-foreground">
                        {f.answer}
                      </p>
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {followUpLoading && (
              <p className="mt-1.5 flex items-center gap-1 text-[11px] text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" /> {tr("正在对着这条记录追问…")}
              </p>
            )}
            {followUpError && (
              <p
                data-testid="pa-followup-error"
                className="mt-1.5 flex items-center gap-1 text-[11px] text-destructive"
              >
                <AlertTriangle className="h-3 w-3" /> {tr("追问失败:")} {followUpError}
              </p>
            )}
            {outcomeCanSave ? (
              <div className="mt-2 flex items-center gap-2 rounded-xl border border-primary/20 bg-primary/5 px-3 py-2">
                <div className="min-w-0 flex-1">
                  <p className="text-[11px] font-medium text-foreground">
                    {decision.direction === "long" ? tr("做多") : decision.direction === "short" ? tr("做空") : tr("中性")}
                    {" · "}{tr("入场")} {decision.entry?.toLocaleString() ?? "—"}
                  </p>
                  <p className="mt-0.5 text-[10px] text-muted-foreground">
                    SL {decision.stop_loss?.toLocaleString() ?? "—"} · TP {decision.take_profit?.toLocaleString() ?? "—"}
                  </p>
                </div>
                {savedId ? (
                  <span className="flex items-center gap-2 text-[11px] text-emerald-400">
                    <span className="flex items-center gap-1">
                      <Check className="h-3.5 w-3.5" /> {tr("已保存")}
                    </span>
                    <Link
                      href={`/research/experiment-evidence?id=${savedId}`}
                      className="flex items-center gap-1 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-emerald-300 transition-colors hover:bg-emerald-500/20"
                    >
                      <FileSearch className="h-3 w-3" /> {tr("查看证据报告")}
                    </Link>
                  </span>
                ) : (
                  <div className="flex shrink-0 items-center gap-1.5">
                    <Button
                      size="sm"
                      className="h-7 gap-1 rounded-lg text-[11px]"
                      onClick={() => void saveExperiment()}
                      disabled={!canSave || saving}
                    >
                      {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                      {tr("保存为实验")}
                    </Button>
                    <details className="relative">
                      <summary
                        className="flex h-7 cursor-pointer list-none items-center gap-1 rounded-lg border border-border/60 px-2 text-[11px] text-muted-foreground transition-colors hover:text-foreground [&::-webkit-details-marker]:hidden"
                        aria-label={tr("更多操作")}
                      >
                        <ChevronDown className="h-3 w-3" />
                      </summary>
                      <div className="absolute right-0 top-8 z-10 w-36 rounded-lg border border-border/60 bg-popover p-1 shadow-lg">
                        <button
                          type="button"
                          className="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-[11px] text-foreground transition-colors hover:bg-muted"
                          onClick={(e) => {
                            e.currentTarget.closest("details")?.removeAttribute("open");
                            void runSetupBacktestFn();
                          }}
                          disabled={!canSave || backtesting}
                        >
                          {backtesting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <History className="h-3.5 w-3.5" />}
                          {tr("回测该形态")}
                        </button>
                        <button
                          type="button"
                          className="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-[11px] text-foreground transition-colors hover:bg-muted"
                          onClick={(e) => {
                            e.currentTarget.closest("details")?.removeAttribute("open");
                            void createPriceAlertFn();
                          }}
                          disabled={!canSave || alertState !== "idle"}
                        >
                          {alertState === "loading" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Bell className="h-3.5 w-3.5" />}
                          {alertState === "done" ? tr("已设置") : tr("到价提醒")}
                        </button>
                      </div>
                    </details>
                  </div>
                )}
              </div>
            ) : (
              <div className="mt-2 rounded-xl border border-dashed border-border/70 px-3 py-2 text-[10px] text-muted-foreground">
                {outcome === "reject"
                  ? tr("硬性否决,无交易指令,不可保存")
                  : tr("无交易指令,等入场信号后再保存")}
              </div>
            )}
            {saveError && (
              <p className="mt-1.5 flex items-center gap-1 text-[11px] text-destructive">
                <AlertTriangle className="h-3 w-3" /> {saveError}
              </p>
            )}
            {backtestError && (
              <p className="mt-1.5 flex items-center gap-1 text-[11px] text-destructive">
                <AlertTriangle className="h-3 w-3" /> {backtestError}
              </p>
            )}
            {alertError && (
              <p className="mt-1.5 flex items-center gap-1 text-[11px] text-destructive">
                <AlertTriangle className="h-3 w-3" /> {tr("到价提醒设置失败:")} {alertError}
              </p>
            )}
            {backtestResult && (
              <SetupBacktestCard
                result={backtestResult}
                attached={backtestResult.attached_to_record === true}
              />
            )}
          </div>
        )}

        <div className="shrink-0 border-t border-border/70 bg-card/80 p-2.5">
          <div className="flex items-center justify-between gap-2 px-0.5 pb-1.5">
            <p className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
              <History className="h-3 w-3" /> {tr("最近诊断")}
              {recentRecords && recentRecords.length > 0 && (
                <span className="text-[10px] font-normal">· {recentRecords.length}</span>
              )}
            </p>
            <div className="flex items-center gap-1">
              <button
                type="button"
                data-testid="pa-recent-scope-current"
                onClick={() => { setRecentScope("current"); void loadRecent("current"); }}
                disabled={recentScope === "current" || recentLoading}
                className={cn(
                  "rounded-md border px-1.5 py-0.5 text-[10px] transition-colors disabled:opacity-50",
                  recentScope === "current"
                    ? "border-primary/30 bg-primary/10 text-primary"
                    : "border-border/60 bg-background/40 text-muted-foreground hover:text-foreground"
                )}
              >
                {tr("当前品种")}
              </button>
              <button
                type="button"
                data-testid="pa-recent-scope-all"
                onClick={() => { setRecentScope("all"); void loadRecent("all"); }}
                disabled={recentScope === "all" || recentLoading}
                className={cn(
                  "rounded-md border px-1.5 py-0.5 text-[10px] transition-colors disabled:opacity-50",
                  recentScope === "all"
                    ? "border-primary/30 bg-primary/10 text-primary"
                    : "border-border/60 bg-background/40 text-muted-foreground hover:text-foreground"
                )}
              >
                {tr("全部品种")}
              </button>
            </div>
          </div>

          {recentLoading && (
            <div className="space-y-1 py-1">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-7 animate-pulse rounded-md bg-border/30" />
              ))}
            </div>
          )}

          {!recentLoading && recentError && (
            <div className="flex items-center gap-2 py-1 text-[11px] text-destructive">
              <AlertTriangle className="h-3 w-3 shrink-0" />
              <span className="min-w-0 flex-1 truncate">{recentError}</span>
              <button
                type="button"
                onClick={() => void loadRecent(recentScope)}
                className="shrink-0 rounded-md border border-border/60 px-1.5 py-0.5 text-[10px] text-muted-foreground hover:text-foreground"
              >
                {tr("重试")}
              </button>
            </div>
          )}

          {!recentLoading && !recentError && recentRecords === null && (
            <p className="py-1 text-[10px] text-muted-foreground">{tr("加载中…")}</p>
          )}

          {!recentLoading && !recentError && Array.isArray(recentRecords) && recentRecords.length === 0 && (
            <p className="py-1.5 text-[10px] leading-5 text-muted-foreground">
              {tr("还没有诊断记录——跑一次分析后，这里会列出每条决策及其结算结果。")}
            </p>
          )}

          {!recentLoading && !recentError && Array.isArray(recentRecords) && recentRecords.length > 0 && (
            <ul data-testid="pa-recent-list" className="max-h-[168px] space-y-1 overflow-y-auto">
              {recentRecords.map((row) => {
                const isReviewing = reviewingId === row.record_id;
                return (
                  <li key={row.record_id}>
                    <button
                      type="button"
                      onClick={() => void openRecordReview(row)}
                      disabled={reviewLoading}
                      className={cn(
                        "w-full rounded-lg border px-2 py-1.5 text-left transition-colors",
                        isReviewing
                          ? "border-primary/30 bg-primary/5"
                          : "border-border/60 bg-background/40 hover:border-primary/20 hover:bg-primary/5"
                      )}
                    >
                      <div className="flex items-center gap-1.5">
                        <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                          {row.timestamp ? new Date(row.timestamp).toLocaleString(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"}
                        </span>
                        <span className="shrink-0 text-[10px] text-foreground">
                          {row.symbol ?? "—"}
                        </span>
                        <span className="shrink-0 text-[10px] text-muted-foreground">{row.interval ?? ""}</span>
                        {row.demo && (
                          <span className="shrink-0 rounded border border-border/60 bg-surface-1 px-1 py-px text-[9px] text-muted-foreground">demo</span>
                        )}
                        <span className="min-w-0 flex-1" />
                        {row.direction && (
                          <span className={cn("shrink-0 rounded border px-1 py-px text-[9px] font-medium", DIRECTION_TONE[row.direction] ?? "border-border/60 bg-surface-1 text-muted-foreground")}>
                            {tr(NEXT_PERIOD_LABELS[row.direction] ?? row.direction)}
                          </span>
                        )}
                        {row.outcome?.status && (
                          <span className={cn("shrink-0 rounded border px-1 py-px text-[9px] font-medium", OUTCOME_TONE[row.outcome.status] ?? "border-border/60 bg-surface-1 text-muted-foreground")}>
                            {tr(OUTCOME_STATUS_LABELS[row.outcome.status] ?? row.outcome.status)}
                            {typeof row.outcome.pnl_r === "number" && (
                              <span className="ml-0.5">{row.outcome.pnl_r > 0 ? "+" : ""}{row.outcome.pnl_r.toFixed(2)}R</span>
                            )}
                          </span>
                        )}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="shrink-0 border-t border-border/70 bg-card/80 p-2.5">
          <div className="flex items-end gap-2 rounded-xl border border-border/70 bg-background/55 px-2 py-1.5">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void sendFollowUp();
                }
              }}
              placeholder={recordId ? tr("对着这条诊断追问…（锚定该记录）") : tr("追问分析结果…（同会话上下文）")}
              className="max-h-[120px] min-h-[32px] flex-1 resize-none overflow-y-auto border-0 bg-transparent px-1 py-1 text-[13px] leading-[1.6] text-foreground shadow-none outline-none placeholder:text-muted-foreground focus-visible:ring-0"
              disabled={generating || paLoading || followUpLoading}
              rows={1}
            />
            <Button
              size="icon"
              aria-label="发送追问"
              className="h-8 w-8 shrink-0 rounded-xl"
              onClick={() => void sendFollowUp()}
              disabled={!input.trim() || generating || paLoading || followUpLoading}
            >
              {generating || followUpLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
            </Button>
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1">
            <button
              type="button"
              disabled={(!latestAssistant && !recordId) || generating || followUpLoading}
              onClick={() => { setInput(tr("为什么？")); }}
              className="rounded-md border border-border/60 bg-background/40 px-2 py-1 text-[10px] text-muted-foreground transition-colors hover:border-primary/30 hover:text-primary disabled:pointer-events-none disabled:opacity-50"
            >
              {tr("为什么")}
            </button>
            <button
              type="button"
              disabled={(!latestAssistant && !recordId) || generating || followUpLoading}
              onClick={() => { setInput(tr("如果方向错了怎么办？")); }}
              className="rounded-md border border-border/60 bg-background/40 px-2 py-1 text-[10px] text-muted-foreground transition-colors hover:border-primary/30 hover:text-primary disabled:pointer-events-none disabled:opacity-50"
            >
              {tr("风险如何")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

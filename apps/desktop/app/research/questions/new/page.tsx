"use client";

/**
 * 立问页 —— 任务卡形状(吸收 external-research-project external-research-project-research-architect):
 * 可证伪假设 + 反向假设 + 对照信号 + 信号形成/最早成交/标签 horizon。
 * 后端 body 是纯文本,council prompt 直接消费 —— 零 schema 变更。
 *
 * 2026-10-26 critique 重做(P0-1):可见标签分节、策略多选(真数据 50 个)、
 * 周期九档(=HORIZON_FOR_INTERVAL 正典)、结构化四段序列化回纯文本、
 * 锚定召回预览(前端本地 overlap,与 recall._anchor_overlap 同语义)、
 * per-field 校验错误替换 catch-all。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api-client";
import { createQuestion, listQuestions } from "@/lib/api/research-graph";
import { useUiText } from "@/components/i18n/use-ui-text"

/** 周期正典 = counterfactual_settlement.HORIZON_FOR_INTERVAL 九键。 */
const INTERVALS = ["1m", "5m", "15m", "30m", "1h", "4h", "8h", "12h", "1d"] as const;

const REGIMES = ["trend", "range", "high_vol", "low_vol", "crisis", "calm"] as const;
interface StrategyOption {
  id: string;
  name: string;
}

interface SectionProps {
  title: string;
  hint?: string;
  children: React.ReactNode;
}

function Section({ title, hint, children }: SectionProps) {
  return (
    <div className="flex flex-col gap-2">
      <div>
        <h2 className="text-[11px] font-semibold text-ink-2">{title}</h2>
        {hint && <p className="mt-0.5 text-[11px] text-ink-4">{hint}</p>}
      </div>
      {children}
    </div>
  );
}

/** 散文段用多行框。单行 Input 看不出「可写一段话」,用户会当填空题填两个字。 */
function ProseField(props: {
  id: string;
  label: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  error?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={props.id} className="text-xs text-ink-2">{props.label}</Label>
      <textarea
        id={props.id}
        rows={2}
        placeholder={props.placeholder}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        className="w-full resize-y rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm leading-relaxed text-ink placeholder:text-muted-foreground focus-visible:outline-none"
      />
      {props.error && <p className="text-[11px] text-neg">{props.error}</p>}
    </div>
  );
}

function NewQuestionInner() {
  const tr = useUiText()

  const router = useRouter();
  const [title, setTitle] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [counter, setCounter] = useState("");
  const [controlSignal, setControlSignal] = useState("");
  const [windowLine, setWindowLine] = useState("");
  const [symbol, setSymbol] = useState("BTC");
  const [intervals, setIntervals] = useState<string[]>(["1d"]);
  const [strategies, setStrategies] = useState<string[]>([]);
  const [regimes, setRegimes] = useState<string[]>([]);
  const [strategySearch, setStrategySearch] = useState("");
  const [strategyPickerOpen, setStrategyPickerOpen] = useState(false);
  const [strategyOptions, setStrategyOptions] = useState<StrategyOption[] | null>(null);
  const [strategyOptionsError, setStrategyOptionsError] = useState(false);
  const [recall, setRecall] = useState<{ total: number; byStatus: Record<string, number> } | null>(null);
  const [fieldError, setFieldError] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = title.trim().length > 0 && !submitting;

  // 策略选项:挂载时拉一次真数据;失败/超时降级成手打逃生口(不堵立问)。
  // 超时由 api-client 内置(12s GET_TIMEOUT_MS)保证,abort 后走 catch 降级。
  // 注意 fetchEnvelope 已解 envelope,返回的 data 就是裸数组(不是 {strategies:[]})。
  useEffect(() => {
    let alive = true;
    api<StrategyOption[]>("/api/v1/strategies")
      .then((rows) => {
        if (alive) setStrategyOptions((Array.isArray(rows) ? rows : []).filter((s) => s.id));
      })
      .catch(() => {
        if (alive) setStrategyOptionsError(true);
      });
    return () => { alive = false; };
  }, []);

  // 锚定召回预览:锚定变化 → 拉全量问题 → 本地 overlap 计数。
  // 与 recall._anchor_overlap 同语义:三个键取交集,命中即算召回。
  const anchorReady = symbol.trim().length > 0 || intervals.length > 0 || strategies.length > 0 || regimes.length > 0;
  useEffect(() => {
    if (!anchorReady) { setRecall(null); return; }
    let alive = true;
    listQuestions({ limit: 200 })
      .then((d) => {
        if (!alive) return;
        const mine = {
          symbols: symbol.trim().toUpperCase() ? [symbol.trim().toUpperCase()] : [],
          intervals,
          strategy_ids: strategies,
          regimes,
        };
        let total = 0;
        const byStatus: Record<string, number> = {};
        for (const q of d.questions) {
          const a = (q.anchor ?? {}) as Record<string, unknown>;
          const overlap = (["strategy_ids", "intervals", "regimes"] as const).some((k) => {
            const theirs = Array.isArray(a[k]) ? (a[k] as string[]) : [];
            return theirs.some((v) => (mine[k] as string[]).includes(v));
          });
          if (overlap) {
            total += 1;
            byStatus[q.status] = (byStatus[q.status] ?? 0) + 1;
          }
        }
        setRecall({ total, byStatus });
      })
      .catch(() => {
        if (alive) setRecall(null);
      });
    return () => { alive = false; };
  }, [symbol, intervals, strategies, regimes, anchorReady]);

  const filteredStrategyOptions = useMemo(() => {
    if (!strategyOptions) return [];
    const q = strategySearch.trim().toLowerCase();
    if (!q) return strategyOptions;
    return strategyOptions.filter(
      (s) => s.id.toLowerCase().includes(q) || s.name.toLowerCase().includes(q),
    );
  }, [strategyOptions, strategySearch]);

  function toggle<T extends string>(list: T[], value: T): T[] {
    return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
  }

  function validate(): boolean {
    const errs: Record<string, string> = {};
    if (!title.trim()) errs.title = tr("给问题起个标题");
    if (intervals.length === 0) errs.intervals = tr("至少选一个周期");
    if (hypothesis.trim() && !counter.trim()) errs.counter = tr("写了假设就写反向——什么情况会推翻它");
    setFieldError(errs);
    return Object.keys(errs).length === 0;
  }

  async function submit() {
    if (!canSubmit || !validate()) return;
    setSubmitting(true);
    setError(null);
    try {
      const anchor: Record<string, unknown> = {
        symbols: symbol.trim().toUpperCase() ? [symbol.trim().toUpperCase()] : [],
        intervals,
      };
      if (strategies.length) anchor.strategy_ids = strategies;
      if (regimes.length) anchor.regimes = regimes;
      // 结构化四段 → 纯文本 body(后端契约不变,council prompt 直接消费)
      const bodyLines = [
        `假设(可证伪):${hypothesis.trim()}`,
        `反向假设(什么情况会推翻它):${counter.trim()}`,
        `对照信号(同条件下该看到什么/不该看到什么):${controlSignal.trim()}`,
        `信号形成 → 最早可成交 → 标签窗口:${windowLine.trim()}`,
      ].filter((l) => !l.endsWith(":"));
      const created = await createQuestion({
        title: title.trim(),
        body: bodyLines.join("\n") || undefined,
        anchor,
      });
      router.push(`/research/questions?q=${encodeURIComponent(created.question_id)}`);
    } catch (e) {
      setSubmitting(false);
      setError(
        e instanceof Error && e.message
          ? `${tr("创建失败")}:${e.message}`
          : tr("创建失败 — 确认后端可用后重试"),
      );
    }
  }

  const regimeLabels: Record<string, string> = {
    trend: tr("趋势"),
    range: tr("震荡"),
    high_vol: tr("高波动"),
    low_vol: tr("低波动"),
    crisis: tr("危机"),
    calm: tr("平静"),
  };

  const recallSummary = useMemo(() => {
    if (!recall) return null;
    if (recall.total === 0) return tr("还没有会召回的历史研究——这个问题是新的");
    const parts: string[] = [];
    const answered = recall.byStatus["sufficiently_answered"] ?? 0;
    const falsified = recall.byStatus["falsified"] ?? 0;
    const open = (recall.byStatus["open"] ?? 0) + (recall.byStatus["parked"] ?? 0);
    if (answered) parts.push(`${answered} ${tr("已回答")}`);
    if (falsified) parts.push(`${falsified} ${tr("已证伪")}`);
    if (open) parts.push(`${open} ${tr("进行中")}`);
    return tr("锚定命中 {n} 条历史研究:{detail}").replace("{n}", String(recall.total)).replace("{detail}", parts.join(", "));
  }, [recall, tr]);

  return (
    <div className="mx-auto flex h-full max-w-2xl flex-col gap-5 overflow-y-auto p-6">
      <div>
        <h1 className="text-[15px] font-semibold text-ink">{tr("立一个新问题")}</h1>
        <p className="mt-1 text-[11px] text-ink-4">
          {tr("任务卡四段填满,Selene 调查时才知道拿什么检验、拿什么对照。")}
        </p>
      </div>

      <Section title={tr("问题")}>
        <div className="flex flex-col gap-1">
          <Label htmlFor="nq-title" className="text-xs text-ink-2">{tr("标题")}</Label>
          <Input
            id="nq-title"
            placeholder={tr("一个问题,例:BTC 突破是否有效")}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="h-9"
          />
          {fieldError.title && <p className="text-[11px] text-neg">{fieldError.title}</p>}
        </div>
      </Section>

      <Section
        title={tr("任务卡(可证伪假设四段)")}
        hint={tr("每段都可以留空——但四段填满,调查才知道拿什么检验、什么情况认输。")}
      >
        <div className="flex flex-col gap-3">
          <ProseField
            id="nq-hypo"
            label={tr("假设(可证伪)")}
            placeholder={tr("例:BTC 突破 20 日高点后 3 天内回撤超过 5%")}
            value={hypothesis}
            onChange={setHypothesis}
          />
          <ProseField
            id="nq-counter"
            label={tr("反向假设(什么情况会推翻它)")}
            placeholder={tr("例:突破后横盘超过 5 天仍站稳")}
            value={counter}
            onChange={setCounter}
            error={fieldError.counter}
          />
          <ProseField
            id="nq-control"
            label={tr("对照信号")}
            placeholder={tr("同条件下该看到什么/不该看到什么")}
            value={controlSignal}
            onChange={setControlSignal}
          />
          <ProseField
            id="nq-window"
            label={tr("信号形成 → 最早可成交 → 标签窗口")}
            placeholder={tr("例:突破当日形成 → 次日开盘 → 3 天")}
            value={windowLine}
            onChange={setWindowLine}
          />
        </div>
      </Section>

      <Section
        title={tr("锚定")}
        hint={tr("锚定字段决定这个问题能召回哪些历史研究:symbol/周期/策略匹配的问题会互相看见证据。")}
      >
        <div className="flex flex-col gap-3 rounded border border-border bg-surface p-3">
          <div className="flex flex-col gap-1">
            <Label htmlFor="nq-symbol" className="text-xs text-ink-2">{tr("标的")}</Label>
            <Input
              id="nq-symbol"
              placeholder="BTC"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              className="h-9"
            />
          </div>

          <div className="flex flex-col gap-1">
            <Label className="text-xs text-ink-2">{tr("周期")}</Label>
            <div className="flex flex-wrap gap-1.5">
              {INTERVALS.map((iv) => (
                <button
                  key={iv}
                  type="button"
                  onClick={() => setIntervals(toggle(intervals, iv))}
                  className={`h-7 rounded-md border px-2 text-xs ${
                    intervals.includes(iv)
                      ? "border-accent bg-accent/10 text-ink"
                      : "border-border text-ink-2 hover:border-ink-4"
                  }`}
                >
                  {iv}
                </button>
              ))}
            </div>
            {fieldError.intervals && <p className="text-[11px] text-neg">{fieldError.intervals}</p>}
          </div>

          <div className="flex flex-col gap-1">
            <Label className="text-xs text-ink-2">{tr("策略")}</Label>
            {strategyOptions === null && !strategyOptionsError && (
              <Skeleton className="h-7 w-full" />
            )}
            {strategyOptionsError && (
              <Input
                placeholder={tr("策略列表取不到——手输 ID,逗号分隔")}
                onChange={(e) => setStrategies(e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
                className="h-9"
              />
            )}
            {strategyOptions !== null && (
              <>
                <button
                  type="button"
                  onClick={() => setStrategyPickerOpen(!strategyPickerOpen)}
                  className="flex h-9 items-center gap-1.5 rounded-md border border-border bg-background px-2 text-xs text-ink-2 hover:border-ink-4"
                >
                  {strategies.length
                    ? strategies.map((s) => strategyOptions.find((o) => o.id === s)?.name ?? s).join(", ")
                    : tr("从 {n} 个策略里选(可多选,可留空)").replace("{n}", String(strategyOptions.length))}
                </button>
                {strategyPickerOpen && (
                  <div className="flex max-h-48 flex-col gap-1 overflow-y-auto rounded-md border border-border bg-background p-2">
                    <Input
                      placeholder={tr("搜策略名或 ID")}
                      value={strategySearch}
                      onChange={(e) => setStrategySearch(e.target.value)}
                      className="h-7 text-xs"
                    />
                    {filteredStrategyOptions.map((s) => (
                      <label key={s.id} className="flex items-center gap-2 rounded px-1 py-0.5 text-xs text-ink-2 hover:bg-surface-2">
                        <Checkbox
                          checked={strategies.includes(s.id)}
                          onCheckedChange={() => setStrategies(toggle(strategies, s.id))}
                        />
                        <span className="truncate">{s.name}</span>
                        <span className="ml-auto shrink-0 font-mono text-[10px] text-ink-4">{s.id}</span>
                      </label>
                    ))}
                    {filteredStrategyOptions.length === 0 && (
                      <p className="px-1 py-0.5 text-[11px] text-ink-4">{tr("没有匹配的策略")}</p>
                    )}
                  </div>
                )}
              </>
            )}
          </div>

          <div className="flex flex-col gap-1">
            <Label className="text-xs text-ink-2">{tr("市场状态(regime)")}</Label>
            <div className="flex flex-wrap gap-1.5">
              {REGIMES.map((rg) => (
                <button
                  key={rg}
                  type="button"
                  onClick={() => setRegimes(toggle(regimes, rg))}
                  className={`h-7 rounded-md border px-2 text-xs ${
                    regimes.includes(rg)
                      ? "border-accent bg-accent/10 text-ink"
                      : "border-border text-ink-2 hover:border-ink-4"
                  }`}
                >
                  {regimeLabels[rg] ?? rg}
                </button>
              ))}
            </div>
          </div>
        </div>
      </Section>

      {recallSummary && (
        <div className="rounded border border-border bg-surface p-3">
          <p className="text-xs text-ink-2">{recallSummary}</p>
          <p className="mt-1 text-[11px] text-ink-4">
            {tr("召回是自动的:匹配锚定的问题互相看见证据,历史结论进入下一次判断。")}
          </p>
        </div>
      )}

      {error && <p className="text-xs text-neg">{error}</p>}
      {/* 主操作与上面的字段隔一条线并放大:之前 h-6 比周期 chip 还小,是整页唯一的出口。 */}
      <div className="flex items-center gap-3 border-t border-border pt-4">
        <Button size="sm" className="h-8 px-5 text-xs" disabled={!canSubmit} onClick={submit}>
          {submitting ? tr("创建中…") : tr("立问")}
        </Button>
        {!canSubmit && !submitting && (
          <span className="text-[11px] text-ink-4">{tr("填好标题就能立问")}</span>
        )}
      </div>
    </div>
  );
}

export default function NewQuestionPage() {
  return <NewQuestionInner />;
}

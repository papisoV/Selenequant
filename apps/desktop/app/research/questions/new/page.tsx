"use client";

/**
 * 立问页 —— 任务卡形状(吸收 external-research-project external-research-project-research-architect):
 * 可证伪假设 + 反向假设 + 对照信号 + 信号形成/最早成交/标签 horizon。
 * 后端 body 是纯文本,council prompt 直接消费 —— 零 schema 变更。
 */

import { useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { createQuestion } from "@/lib/api/research-graph";

const BODY_TEMPLATE = [
  "假设(可证伪):",
  "反向假设(什么情况会推翻它):",
  "对照信号(同条件下该看到什么/不该看到什么):",
  "信号形成 → 最早可成交 → 标签窗口:",
].join("\n");

function NewQuestionInner() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState(BODY_TEMPLATE);
  const [symbol, setSymbol] = useState("BTC");
  const [interval, setInterval] = useState("1d");
  const [strategyIds, setStrategyIds] = useState("");
  const [regimes, setRegimes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = title.trim().length > 0 && !submitting;

  async function submit() {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const anchor: Record<string, unknown> = {
        symbols: [symbol.trim().toUpperCase()],
        intervals: [interval],
      };
      if (strategyIds.trim()) {
        anchor.strategy_ids = strategyIds.split(",").map((s) => s.trim()).filter(Boolean);
      }
      if (regimes.trim()) {
        anchor.regimes = regimes.split(",").map((s) => s.trim()).filter(Boolean);
      }
      const created = await createQuestion({ title: title.trim(), body: body.trim() || undefined, anchor });
      router.push(`/research/questions?q=${encodeURIComponent(created.question_id)}`);
    } catch {
      setSubmitting(false);
      setError("创建失败 — 确认后端可用后重试");
    }
  }

  return (
    <main className="mx-auto flex h-full max-w-2xl flex-col justify-center gap-3 p-6">
      <h1 className="text-sm text-ink">立一个新问题</h1>
      <Input
        aria-label="问题标题"
        placeholder="一个问题,例:BTC 突破是否有效"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <textarea
        aria-label="补充说明(可选)"
        placeholder="补充说明(可选)"
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={6}
        className="w-full rounded border border-border bg-surface p-2 font-mono text-xs text-ink-2 outline-none focus:border-accent"
      />
      <p className="text-[11px] text-ink-4">
        任务卡四段填满,Selene 调查时才知道拿什么检验、拿什么对照。
      </p>
      <div className="flex flex-col gap-2 rounded border border-border bg-surface p-3">
        <div className="flex gap-2">
          <Input
            aria-label="锚定 symbol"
            placeholder="BTC"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            className="h-8 flex-1"
          />
          <select
            aria-label="锚定周期"
            value={interval}
            onChange={(e) => setInterval(e.target.value)}
            className="h-8 rounded-md border border-border bg-background px-2 text-xs text-ink-2 outline-none focus:border-accent"
          >
            <option value="15m">15m</option>
            <option value="1h">1h</option>
            <option value="1d">1d</option>
          </select>
        </div>
        <Input
          aria-label="锚定策略(可选,逗号分隔)"
          placeholder="策略 ID,逗号分隔,例:V52"
          value={strategyIds}
          onChange={(e) => setStrategyIds(e.target.value)}
          className="h-8"
        />
        <Input
          aria-label="锚定 regime(可选,逗号分隔)"
          placeholder="Regime,逗号分隔,例:trend"
          value={regimes}
          onChange={(e) => setRegimes(e.target.value)}
          className="h-8"
        />
        <p className="text-[11px] text-ink-4">
          锚定字段决定这个问题能召回哪些历史研究:symbol/周期/策略匹配的问题会互相看见证据。
        </p>
      </div>
      {error && <p className="text-xs text-neg">{error}</p>}
      <Button size="sm" className="self-start" disabled={!canSubmit} onClick={submit}>
        {submitting ? "创建中…" : "立问"}
      </Button>
    </main>
  );
}

export default function NewQuestionPage() {
  return <NewQuestionInner />;
}

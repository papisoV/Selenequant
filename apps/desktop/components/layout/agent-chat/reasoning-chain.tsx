"use client";

import React, { useState, useEffect } from "react";
import { useUiText } from "@/components/i18n/use-ui-text"

export interface ReasoningStep {
  type: "thinking" | "tool_call" | "tool_result" | "reflection";
  data: Record<string, unknown>;
  timestamp: number;
}

const STEP_ICONS: Record<string, string> = {
  thinking: "💭",
  tool_call: "🔧",
  tool_result: "📊",
  reflection: "🔍",
};

const STEP_LABELS: Record<string, string> = {
  thinking: "思考",
  tool_call: "工具调用",
  tool_result: "工具结果",
  reflection: "反思",
};

function ReasoningStepItem({ step, defaultOpen }: { step: ReasoningStep; defaultOpen: boolean }) {
  const icon = STEP_ICONS[step.type] ?? "•";
  const label = STEP_LABELS[step.type] ?? step.type;

  let summary: string;
  if (step.type === "thinking" || step.type === "reflection") {
    summary = String((step.data as Record<string, unknown>).content ?? "");
  } else if (step.type === "tool_call") {
    const d = step.data as Record<string, unknown>;
    summary = `${d.tool ?? "unknown"}(${JSON.stringify(d.arguments ?? {})})`;
  } else if (step.type === "tool_result") {
    const d = step.data as Record<string, unknown>;
    const output = String(d.output ?? d.result ?? "");
    summary = output.length > 120 ? output.slice(0, 120) + "…" : output;
  } else {
    summary = JSON.stringify(step.data);
  }

  return (
    <details open={defaultOpen} className="group">
      <summary className="cursor-pointer flex items-center gap-1.5 py-1 text-xs text-muted-foreground hover:text-foreground select-none">
        <span>{icon}</span>
        <span className="font-medium">{label}</span>
        <span className="text-[11px] opacity-60 truncate max-w-[280px]">{summary}</span>
      </summary>
      <div className="ml-6 mt-1 mb-2 text-[11px] bg-muted/40 rounded-lg p-2 font-mono whitespace-pre-wrap break-all">
        {step.type === "tool_call"
          ? JSON.stringify(step.data, null, 2)
          : summary}
      </div>
    </details>
  );
}

export function ReasoningChain({ steps, isComplete }: { steps: ReasoningStep[]; isComplete: boolean }) {
  const tr = useUiText()

  const [isOpen, setIsOpen] = useState(!isComplete);

  useEffect(() => {
    if (isComplete) {
      setIsOpen(false);
    }
  }, [isComplete]);

  if (!steps || steps.length === 0) return null;

  return (
    <div className="mb-2 rounded-lg border border-primary/20 bg-primary/5 overflow-hidden">
      <details
        open={isOpen}
        onToggle={(e) => setIsOpen(e.currentTarget.open)}
        className="group"
      >
        <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-primary hover:bg-primary/10 flex items-center justify-between select-none">
          <span>{tr("🧠 推理链 (")}{steps.length} {tr("步)")}</span>
          <span className="text-[10px] opacity-60 group-open:hidden">{tr("点击展开")}</span>
        </summary>
        <div className="px-3 pb-2 space-y-0.5">
          {steps.map((step, idx) => (
            <ReasoningStepItem
              key={`${step.type}-${idx}`}
              step={step}
              defaultOpen={idx === steps.length - 1}
            />
          ))}
        </div>
      </details>
    </div>
  );
}

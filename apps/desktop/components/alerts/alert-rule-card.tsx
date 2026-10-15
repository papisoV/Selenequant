"use client";

import { cn } from "@/lib/utils";
import type { AlertRule } from "@/lib/api/intelligence";

const TYPE_LABEL: Record<string, string> = {
  price: "价格",
  indicator: "指标",
  onchain: "链上",
  large_trade: "大单",
  voice: "语音",
};

function thresholdSummary(rule: AlertRule): string {
  const t = rule.threshold ?? {};
  const parts = Object.entries(t).map(([k, v]) => `${k}=${String(v)}`);
  return parts.length ? parts.join(", ") : "—";
}

export function AlertRuleCard({
  rule,
  onEdit,
  onDelete,
  onToggle,
}: {
  rule: AlertRule;
  onEdit: (rule: AlertRule) => void;
  onDelete: (rule: AlertRule) => void;
  onToggle: (rule: AlertRule) => void;
}) {
  return (
    <div
      className="alert-rule-card flex items-center gap-2 rounded border border-border-soft px-2 py-1.5 text-[11px]"
      data-rule-id={rule.id}
    >
      <button
        type="button"
        role="switch"
        aria-checked={rule.enabled}
        aria-label={`开关 ${rule.name}`}
        onClick={() => onToggle(rule)}
        className={cn(
          "relative h-4 w-7 rounded-full transition-colors shrink-0",
          rule.enabled ? "bg-pos" : "bg-surface-2",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-3 w-3 rounded-full bg-white transition-transform",
            rule.enabled ? "translate-x-3.5" : "translate-x-0.5",
          )}
        />
      </button>
      <span className="w-32 truncate font-medium text-ink">{rule.name}</span>
      <span className="w-14 shrink-0 font-mono text-ink-3">
        {TYPE_LABEL[rule.type] ?? rule.type}
      </span>
      <span className="flex-1 truncate font-mono text-ink-3">{thresholdSummary(rule)}</span>
      <button
        type="button"
        onClick={() => onEdit(rule)}
        className="rounded border border-border-soft px-1.5 py-0.5 text-[9px] text-ink-3 hover:bg-surface-2 shrink-0"
      >
        编辑
      </button>
      <button
        type="button"
        onClick={() => onDelete(rule)}
        className="rounded border border-border-soft px-1.5 py-0.5 text-[9px] text-neg hover:bg-surface-2 shrink-0"
      >
        删除
      </button>
    </div>
  );
}

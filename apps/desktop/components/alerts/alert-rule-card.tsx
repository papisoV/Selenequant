"use client";

import { cn } from "@/lib/utils";
import { useUiText } from "@/components/i18n/use-ui-text";
import type { StoredAlertRule } from "@/lib/store/alerts";

const TYPE_LABEL: Record<string, string> = {
  price: "价格",
  indicator: "指标",
  large_trade: "大单",
  voice: "语音",
};

// thresholdSummary 原样打印后端的阈值键(k=v)。键是协议字段名,不是文案;
// 唯一需要译的是「没有阈值」的破折号占位,由调用点处理。

function thresholdSummary(rule: StoredAlertRule): string {
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
  rule: StoredAlertRule;
  onEdit: (rule: StoredAlertRule) => void;
  onDelete: (rule: StoredAlertRule) => void;
  onToggle: (rule: StoredAlertRule) => void;
}) {
  const tr = useUiText();
  return (
    <div
      className="alert-rule-card flex items-center gap-2 rounded border border-border-soft px-2 py-1.5 text-[11px]"
      data-rule-id={rule.id}
    >
      <button
        type="button"
        role="switch"
        aria-checked={rule.enabled}
        aria-label={tr("开关 {name}", { name: rule.name })}
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
        {tr(TYPE_LABEL[rule.type] ?? rule.type)}
        {TYPE_LABEL[rule.type] ? null : (
          // 已从枚举下架的旧规则(如 onchain:无数据源,重开后是假监控)。
          // 保留可见 + 明说不会再触发,好过让它静静躺在列表里装作在监控。
          <span className="ml-1 text-[9px] text-ink-4">({tr("不再支持")})</span>
        )}
      </span>
      <span className="flex-1 truncate font-mono text-ink-3">{thresholdSummary(rule)}</span>
      {rule.localOnly && (
        // 后台求值器只读后端的规则。这条还停在本机 ⇒ 它不会触发,不标出来
        // 就等于让用户以为自己在被监控。
        <span className="shrink-0 text-[9px] text-ink-4">({tr("未同步,不会触发")})</span>
      )}
      <button
        type="button"
        onClick={() => onEdit(rule)}
        className="rounded border border-border-soft px-1.5 py-0.5 text-[9px] text-ink-3 hover:bg-surface-2 shrink-0"
      >
        {tr("编辑")}
      </button>
      <button
        type="button"
        onClick={() => onDelete(rule)}
        className="rounded border border-border-soft px-1.5 py-0.5 text-[9px] text-neg hover:bg-surface-2 shrink-0"
      >
        {tr("删除")}
      </button>
    </div>
  );
}

"use client";

import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";

// MetricBlock — onchain 页专用,单个指标块 (value + 24h change + description)
// 旧代码 markets2/onchain-metrics-panel.tsx:153-235

export interface MetricData {
  value?: number;
  change_24h?: number;
  timestamp?: string;
}

export function MetricBlock({
  icon: Icon,
  iconCls,
  label,
  value,
  change24h,
  description,
  valueCls,
}: {
  icon: LucideIcon;
  iconCls?: string;
  label: string;
  value: string;
  change24h?: number;
  description?: string;
  valueCls?: string;
}) {
  const hasChange = change24h !== undefined && change24h !== null;
  const positive = hasChange ? (change24h as number) >= 0 : false;

  return (
    <div className="rounded-lg bg-surface-2 p-2 space-y-1">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Icon className={cn("h-4 w-4", iconCls)} />
          <span className="text-[10px] text-ink-3">{label}</span>
        </div>
        {hasChange && (
          <span
            className={cn(
              "text-[10px] font-mono",
              positive ? "text-pos" : "text-neg"
            )}
          >
            {positive ? "+" : ""}
            {(change24h as number).toFixed(2)}%
          </span>
        )}
      </div>
      <div className={cn("text-[14px] font-bold font-mono", valueCls ?? "text-ink")}>
        {value}
      </div>
      {description && (
        <p className="text-[9px] text-ink-4">{description}</p>
      )}
    </div>
  );
}

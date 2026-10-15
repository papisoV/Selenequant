"use client";

import { Flame } from "lucide-react";
import { cn } from "@/lib/utils";

// HeatmapBar — liquidations 页专用，单条价格区间的红绿双色 bar
// 旧代码 markets2/liquidation-heatmap-panel.tsx:188-228

export interface HeatmapItem {
  timestamp: number;
  short_amount_usd: number;
  long_amount_usd: number;
  total_amount_usd: number;
  short_count: number;
  long_count: number;
  price_high: number;
  price_low: number;
}

function formatUSD(val: number): string {
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
  return `$${val.toFixed(0)}`;
}

function formatPrice(val: number): string {
  if (val >= 1000) return val.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (val >= 1) return val.toFixed(2);
  return val.toFixed(4);
}

export function HeatmapBar({ item, maxAmount }: { item: HeatmapItem; maxAmount: number }) {
  const safeMax = maxAmount || 1;
  const intensity = item.total_amount_usd / safeMax;
  const isHighIntensity = intensity > 0.7;
  const longPct = (item.long_amount_usd / safeMax) * 100;
  const shortPct = (item.short_amount_usd / safeMax) * 100;

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-[10px]">
        <span className="font-mono text-ink-3">
          {formatPrice(item.price_low)} - {formatPrice(item.price_high)}
        </span>
        <div className="flex gap-3 font-mono">
          <span className="text-neg">{formatUSD(item.long_amount_usd)}</span>
          <span className="text-pos">{formatUSD(item.short_amount_usd)}</span>
        </div>
      </div>
      <div className="relative h-4 rounded overflow-hidden bg-surface-2">
        <div
          className="absolute left-0 top-0 h-full bg-neg/60"
          style={{ width: `${longPct}%` }}
        />
        <div
          className="absolute right-0 top-0 h-full bg-pos/60"
          style={{ width: `${shortPct}%` }}
        />
        {isHighIntensity && (
          <Flame className="absolute right-1 top-1/2 -translate-y-1/2 h-3 w-3 text-gold" />
        )}
      </div>
    </div>
  );
}

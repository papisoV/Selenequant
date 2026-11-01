"use client";

import { useState } from "react";
import useSWR from "swr";
import { RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useContextStore } from "@/lib/store/context";
import { HeatmapBar, type HeatmapItem } from "./parts/heatmap-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 旧代码 markets2/liquidation-heatmap-panel.tsx 完整吸收
// 数据层: /api/v1/liquidations/heatmap?symbol=X&timeframe=Y&force=true
// 视觉骨架: V17Card + StatBlock + v17-page-grid (新前端)
// 对接逻辑: timeframe 切换 + 30s 自动刷新 + force refresh (旧代码)

interface HeatmapData {
  symbol: string;
  timeframe: string;
  items: HeatmapItem[];
  count: number;
}

const TIMEFRAME_OPTIONS = [
  { value: "h1", label: "1H" },
  { value: "h4", label: "4H" },
  { value: "day", label: "1D" },
] as const;

function normalizeSymbol(sym: string): string {
  return sym.split("-")[0] || "BTC";
}

async function fetcher<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" } });
    if (!res.ok) return null;
    const body = await res.json();
    if (body && typeof body === "object" && "data" in body && "meta" in body) {
      if (body.error) return null;
      return (body.data ?? null) as T;
    }
    return body as T;
  } catch {
    return null;
  }
}

function formatUSD(val: number): string {
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
  return `$${val.toFixed(0)}`;
}

export function LiquidationsPage() {
  const tr = useUiText()

  const { pageFilter } = useContextStore();
  const baseSym = normalizeSymbol(pageFilter.symbol);
  const [timeframe, setTimeframe] = useState<string>("h4");
  const [forceToken, setForceToken] = useState(0);

  const swrKey = `liq-${baseSym}-${timeframe}-${forceToken}`;
  const path = `/api/v1/liquidations/heatmap?symbol=${baseSym}&timeframe=${timeframe}${forceToken > 0 ? "&force=true" : ""}`;

  const { data } = useSWR<HeatmapData | null>(swrKey, () => fetcher<HeatmapData>(path), {
    revalidateOnFocus: false,
    dedupingInterval: 5000,
    refreshInterval: 30000,
  });

  const d = data ?? null;
  const items = d?.items ?? [];
  const sortedItems = [...items].sort((a, b) => a.price_low - b.price_low);
  const maxAmount = sortedItems.reduce((max, it) => Math.max(max, it.total_amount_usd), 0) || 1;
  const totalLong = sortedItems.reduce((s, it) => s + it.long_amount_usd, 0);
  const totalShort = sortedItems.reduce((s, it) => s + it.short_amount_usd, 0);
  const totalAll = sortedItems.reduce((s, it) => s + it.total_amount_usd, 0);
  const [refreshing, setRefreshing] = useState(false);

  const handleRefresh = () => {
    setRefreshing(true);
    setForceToken((t) => t + 1);
    setTimeout(() => setRefreshing(false), 800);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("24h 爆仓")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("总爆仓")} value={items.length > 0 ? formatUSD(totalAll) : "—"} cls="text-gold" />
          <StatBlock label={tr("多")} value={items.length > 0 ? formatUSD(totalLong) : "—"} cls="text-pos" />
          <StatBlock label={tr("空")} value={items.length > 0 ? formatUSD(totalShort) : "—"} cls="text-neg" />
        </div>
      </V17Card>
      <V17Card
        title={tr("价格区间热力")}
        right={
          <div className="flex items-center gap-1">
            <div className="flex gap-1">
              {TIMEFRAME_OPTIONS.map((tf) => (
                <button
                  key={tf.value}
                  onClick={() => setTimeframe(tf.value)}
                  className={cn(
                    "rounded px-1.5 py-0.5 text-[10px] transition-colors",
                    timeframe === tf.value
                      ? "bg-gold/20 text-gold"
                      : "text-ink-3 hover:bg-surface-2 hover:text-ink-2"
                  )}
                >
                  {tf.label}
                </button>
              ))}
            </div>
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
              aria-label="force refresh"
            >
              <RefreshCw className={cn("h-3 w-3", refreshing && "animate-spin")} />
            </button>
          </div>
        }
      >
        <div className="text-[9px] text-ink-4 mb-2">
          {d?.symbol ?? baseSym} · {d?.count ?? 0} levels
        </div>
        {items.length === 0 ? (
          <div className="py-4 text-center text-[10px] text-ink-4">{tr("暂无数据")}</div>
        ) : (
          <div className="space-y-2 max-h-[400px] overflow-y-auto">
            {sortedItems.map((item, idx) => (
              <HeatmapBar key={`heatmap-${idx}`} item={item} maxAmount={maxAmount} />
            ))}
          </div>
        )}
      </V17Card>
    </div>
  );
}

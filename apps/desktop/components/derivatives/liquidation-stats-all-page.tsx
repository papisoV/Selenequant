"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { V17Card } from "@/components/shared/page-filter-bar";
import { useApi } from "@/lib/hooks/use-api";

// 数据层: /api/v1/derivatives/liquidation-stats/all → { windows: { "15m": LiquidationStats, ... } }

interface LiquidationStats {
  symbol: string;
  window: string;
  long_liquidated_usd: number;
  short_liquidated_usd: number;
  total_liquidated_usd: number;
  long_count: number;
  short_count: number;
  largest_single_usd: number;
  timestamp: number;
}

interface LiquidationStatsAllResponse {
  windows: Record<string, LiquidationStats>;
}

const WINDOW_LABELS = ["15m", "30m", "1h", "4h", "24h"];

function formatUsd(val: number): string {
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
  return `$${val.toFixed(2)}`;
}

export function LiquidationStatsAllPage() {
  const [forceToken, setForceToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const path = `/api/v1/derivatives/liquidation-stats/all${forceToken > 0 ? "?force=true" : ""}`;
  const { data } = useApi<LiquidationStatsAllResponse>(path);

  const handleRefresh = () => {
    setRefreshing(true);
    setForceToken((t) => t + 1);
    setTimeout(() => setRefreshing(false), 800);
  };

  const windows = data?.windows ?? {};
  const entries = WINDOW_LABELS.filter((w) => windows[w]).map((w) => windows[w]);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="全市场爆仓统计"
        right={
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
            aria-label="force refresh"
          >
            <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
          </button>
        }
      >
        <div className="text-[9px] text-ink-4 mb-1.5">
          {entries.length > 0 ? `${entries.length} 个时间窗口` : "暂无数据"}
        </div>
        {entries.length === 0 ? (
          <div className="flex items-center justify-center h-40 text-[10px] text-ink-4">暂无爆仓统计数据</div>
        ) : (
          <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))" }}>
            {entries.map((s) => {
              const total = s.total_liquidated_usd ?? 0;
              const longPct = total > 0 ? ((s.long_liquidated_usd ?? 0) / total) * 100 : 0;
              return (
                <div key={s.window} className="rounded border border-border-soft bg-surface-2 p-2">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[10px] font-medium text-ink">{s.window}</span>
                    <span className="text-[9px] text-ink-4">
                      {s.long_count ?? 0} 多 / {s.short_count ?? 0} 空
                    </span>
                  </div>
                  <div className="text-[13px] font-medium text-ink">{formatUsd(total)}</div>
                  <div className="mt-1 h-1.5 rounded bg-surface-3 overflow-hidden">
                    <div
                      className="h-full rounded"
                      style={{ width: `${longPct}%`, backgroundColor: "var(--color-pos, #22c55e)" }}
                    />
                  </div>
                  <div className="mt-1 flex justify-between text-[9px] text-ink-4">
                    <span className="text-pos">多 {formatUsd(s.long_liquidated_usd ?? 0)}</span>
                    <span className="text-neg">空 {formatUsd(s.short_liquidated_usd ?? 0)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </V17Card>
    </div>
  );
}

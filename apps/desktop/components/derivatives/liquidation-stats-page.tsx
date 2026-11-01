"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useApi } from "@/lib/hooks/use-api";
import { useUiText } from "@/components/i18n/use-ui-text"

// 数据层: /api/v1/derivatives/liquidation-stats?window=1h → LiquidationStats
// LiquidationStats: { symbol, window, long_liquidated_usd, short_liquidated_usd, total_liquidated_usd,
//   long_count, short_count, largest_single_usd, timestamp }

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

const WINDOW_OPTIONS = ["15m", "30m", "1h", "4h", "24h"] as const;

function formatUsd(val: number): string {
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
  return `$${val.toFixed(2)}`;
}

export function LiquidationStatsPage() {
  const tr = useUiText()

  const [window_, setWindow] = useState<string>("1h");
  const [forceToken, setForceToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const path = `/api/v1/derivatives/liquidation-stats?window=${window_}${forceToken > 0 ? "&force=true" : ""}`;
  const { data } = useApi<LiquidationStats>(path);

  const handleRefresh = () => {
    setRefreshing(true);
    setForceToken((t) => t + 1);
    setTimeout(() => setRefreshing(false), 800);
  };

  const longUsd = data?.long_liquidated_usd ?? 0;
  const shortUsd = data?.short_liquidated_usd ?? 0;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("爆仓统计")}
        right={
          <div className="flex items-center gap-1">
            <div className="flex gap-1">
              {WINDOW_OPTIONS.map((w) => (
                <button
                  key={w}
                  onClick={() => setWindow(w)}
                  className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
                    window_ === w
                      ? "bg-gold/20 text-gold"
                      : "text-ink-3 hover:bg-surface-2 hover:text-ink-2"
                  }`}
                >
                  {w}
                </button>
              ))}
            </div>
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
              aria-label="force refresh"
            >
              <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
            </button>
          </div>
        }
      >
        <div className="text-[9px] text-ink-4 mb-1.5">
          {data ? `${data.symbol} · ${data.window} 窗口` : tr("暂无数据")}
        </div>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("总爆仓")} value={data ? formatUsd(data.total_liquidated_usd) : "—"} cls="text-gold" />
          <StatBlock label={tr("多头爆仓")} value={data ? formatUsd(longUsd) : "—"} cls="text-pos" />
          <StatBlock label={tr("空头爆仓")} value={data ? formatUsd(shortUsd) : "—"} cls="text-neg" />
        </div>
      </V17Card>
      <V17Card title={tr("笔数")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("多头笔数")} value={data?.long_count ?? 0} cls="text-pos" />
          <StatBlock label={tr("空头笔数")} value={data?.short_count ?? 0} cls="text-neg" />
          <StatBlock label={tr("最大单笔")} value={data ? formatUsd(data.largest_single_usd) : "—"} />
        </div>
      </V17Card>
    </div>
  );
}

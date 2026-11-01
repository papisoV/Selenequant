"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useApi } from "@/lib/hooks/use-api";
import { useUiText } from "@/components/i18n/use-ui-text"

// 数据层: /api/v1/derivatives/large-trades/stats?window_ms=3600000
// → { window_ms, buy_count, sell_count, buy_volume_usd, sell_volume_usd, total_count }

interface LargeTradeStats {
  window_ms: number;
  buy_count: number;
  sell_count: number;
  buy_volume_usd: number;
  sell_volume_usd: number;
  total_count: number;
}

const WINDOW_OPTIONS = [
  { value: 600_000, label: "10m" },
  { value: 1_800_000, label: "30m" },
  { value: 3_600_000, label: "1H" },
  { value: 14_400_000, label: "4H" },
  { value: 86_400_000, label: "24H" },
] as const;

function formatUsd(val: number): string {
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
  return `$${val.toFixed(2)}`;
}

export function LargeTradesStatsPage() {
  const tr = useUiText()

  const [windowMs, setWindowMs] = useState<number>(3_600_000);
  const [forceToken, setForceToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const path = `/api/v1/derivatives/large-trades/stats?window_ms=${windowMs}${forceToken > 0 ? "&force=true" : ""}`;
  const { data } = useApi<LargeTradeStats>(path);

  const handleRefresh = () => {
    setRefreshing(true);
    setForceToken((t) => t + 1);
    setTimeout(() => setRefreshing(false), 800);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("大宗成交统计")}
        right={
          <div className="flex items-center gap-1">
            <div className="flex gap-1">
              {WINDOW_OPTIONS.map((w) => (
                <button
                  key={w.value}
                  onClick={() => setWindowMs(w.value)}
                  className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
                    windowMs === w.value
                      ? "bg-gold/20 text-gold"
                      : "text-ink-3 hover:bg-surface-2 hover:text-ink-2"
                  }`}
                >
                  {w.label}
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
          {data ? tr("窗口 {h}h", { h: (data.window_ms / 3_600_000).toFixed(data.window_ms % 3_600_000 === 0 ? 0 : 1) }) : tr("暂无数据")}
        </div>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("总笔数")} value={data?.total_count ?? 0} />
          <StatBlock label={tr("买单数")} value={data?.buy_count ?? 0} cls="text-pos" />
          <StatBlock label={tr("卖单数")} value={data?.sell_count ?? 0} cls="text-neg" />
        </div>
      </V17Card>
      <V17Card title={tr("成交金额")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(2, 1fr)" }}>
          <StatBlock label={tr("买金额")} value={data ? formatUsd(data.buy_volume_usd) : "—"} cls="text-pos" />
          <StatBlock label={tr("卖金额")} value={data ? formatUsd(data.sell_volume_usd) : "—"} cls="text-neg" />
        </div>
      </V17Card>
    </div>
  );
}

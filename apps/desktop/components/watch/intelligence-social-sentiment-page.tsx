"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";

// 社交情绪 — /api/v1/intelligence/social/sentiment
// shape: { overall, by_subreddit{ "r/xxx": 0.xx }, timestamp }

interface SocialSentimentData {
  overall?: number;
  by_subreddit?: Record<string, number>;
  timestamp?: number;
}

function sentCls(v?: number): string {
  if (v == null) return "text-ink-4";
  if (v >= 0.55) return "text-pos";
  if (v <= 0.45) return "text-neg";
  return "text-gold";
}

function barCls(v?: number): string {
  if (v == null) return "bg-ink-4/30";
  if (v >= 0.55) return "bg-pos";
  if (v <= 0.45) return "bg-neg";
  return "bg-gold";
}

export function IntelligenceSocialSentimentPage() {
  const { useApi } = useApiPage();
  const { data, isLoading, mutate } = useApi<SocialSentimentData | null>(
    "/api/v1/intelligence/social/sentiment",
  );
  const [refreshing, setRefreshing] = useState(false);
  const d = data ?? null;
  const subs = Object.entries(d?.by_subreddit ?? {});
  const overall = d?.overall ?? null;

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="社交情绪"
        right={
          <button
            type="button"
            onClick={handleRefresh}
            disabled={refreshing || isLoading}
            aria-label="刷新"
            className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
          >
            <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
          </button>
        }
      >
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(2, 1fr)" }}>
          <StatBlock label="综合情绪" value={overall != null ? overall.toFixed(3) : "—"} cls={sentCls(overall ?? undefined)} />
          <StatBlock label="追踪社区" value={String(subs.length)} cls="text-ink-3" />
        </div>
        <div className="mt-3 rounded bg-surface-2 p-3">
          <div className="mb-1 flex justify-between text-[9px] text-ink-4">
            <span>偏空</span>
            <span>中性</span>
            <span>偏多</span>
          </div>
          <div className="relative h-2 rounded bg-gradient-to-r from-neg via-gold to-pos">
            <div
              className="absolute -top-0.5 h-3 w-0.5 bg-ink -translate-x-1/2"
              style={{ left: `${Math.max(0, Math.min(100, (overall ?? 0.5) * 100))}%` }}
            />
          </div>
        </div>
      </V17Card>

      <V17Card title="各社区情绪">
        {subs.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-3">
            {isLoading ? "加载中…" : "暂无数据"}
          </div>
        ) : (
          <div className="flex flex-col">
            {subs.map(([sub, v]) => (
              <div key={sub} className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft">
                <span className="w-20 truncate text-ink-2">{sub}</span>
                <div className="flex-1 h-2 bg-surface-2 rounded overflow-hidden">
                  <div className={`h-full ${barCls(v)}`} style={{ width: `${v * 100}%` }} />
                </div>
                <span className={`w-10 text-right font-mono num ${sentCls(v)}`}>{v.toFixed(2)}</span>
              </div>
            ))}
          </div>
        )}
      </V17Card>
    </div>
  );
}

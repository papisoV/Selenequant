"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { useMarketStore } from "@/lib/store/market";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";

// 评分分解 — /api/v1/us-radar/scores/{symbol}/breakdown
// shape: { symbol, total_score, signal, breakdown[], signals[], timestamp }
// dimension: { dimension, score, weight, weighted_contribution, signal }

interface ScoreDimension {
  dimension?: string;
  score?: number;
  weight?: number;
  weighted_contribution?: number;
  signal?: string;
}
interface BreakdownData {
  symbol?: string;
  total_score?: number;
  signal?: string;
  breakdown?: ScoreDimension[];
  signals?: string[];
  timestamp?: number;
}

const bare = (s: string): string => s.replace(/[-/:].*$/, "");

const DIM_LABEL: Record<string, string> = {
  technical: "技术",
  sentiment: "情绪",
  news: "新闻",
  volume: "量能",
  risk: "风险",
  l2: "L2",
  flow: "资金流",
  relationship: "关联",
};

function dimLabel(d: string): string {
  return DIM_LABEL[d] ?? d;
}

function signalCls(sig?: string): string {
  if (sig === "positive" || sig === "long" || sig === "bullish") return "text-pos";
  if (sig === "negative" || sig === "short" || sig === "bearish") return "text-neg";
  return "text-ink-3";
}

function fmtTime(ts?: number): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString("en-US", { hour12: false });
}

export function ScoresSymbolBreakdownPage() {
  const { useApi } = useApiPage({ distinguish: true });
  const { pageFilter } = useMarketStore();
  const symbol = bare(pageFilter.symbol) || "BTC";
  const { data, isLoading, mutate } = useApi<BreakdownData | null>(
    `/api/v1/us-radar/scores/${symbol}/breakdown`,
  );
  const [refreshing, setRefreshing] = useState(false);
  const d = data ?? null;

  const breakdown = d?.breakdown ?? [];
  const maxContribution = Math.max(1, ...breakdown.map((b) => Math.abs(b.weighted_contribution ?? 0)));

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={`评分分解 · ${d?.symbol ?? symbol}`}
        right={
          <div className="flex items-center gap-2">
            <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3">{symbol}</span>
            <span className="text-[9px] text-ink-4 font-mono">{fmtTime(d?.timestamp)}</span>
            <button
              type="button"
              onClick={handleRefresh}
              disabled={refreshing || isLoading}
              aria-label="刷新"
              className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
            >
              <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
            </button>
          </div>
        }
      >
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label="总分" value={d?.total_score != null ? String(d.total_score) : "—"} cls="text-gold" />
          <StatBlock label="信号" value={d?.signal ?? "—"} cls={signalCls(d?.signal)} />
          <StatBlock label="维度" value={String(breakdown.length)} />
          <StatBlock label="信号数" value={String(d?.signals?.length ?? 0)} />
        </div>
      </V17Card>

      <V17Card title="8 维评分">
        {breakdown.length === 0 ? (
          isLoading ? <div className="text-center text-ink-4 text-[11px] py-3">加载中…</div> : <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
        ) : (
          <div className="flex flex-col">
            {breakdown.map((b) => {
              const contribution = b.weighted_contribution ?? 0;
              const positive = contribution >= 0;
              return (
                <div key={b.dimension} className="flex items-center gap-2 border-b border-border-soft py-1.5 text-[10px]">
                  <span className="w-14 font-medium text-ink">{dimLabel(b.dimension ?? "")}</span>
                  <div className="flex-1 flex items-center gap-1">
                    <div className="h-2 flex-1 bg-surface-2 rounded overflow-hidden flex">
                      <div
                        className={`h-full ${positive ? "bg-pos" : "bg-neg"}`}
                        style={{ width: `${Math.min(100, (Math.abs(contribution) / maxContribution) * 100)}%` }}
                      />
                    </div>
                  </div>
                  <span className={`w-8 text-right font-mono num ${positive ? "text-pos" : "text-neg"}`}>
                    {contribution > 0 ? "+" : ""}{contribution.toFixed(2)}
                  </span>
                  <span className={`w-8 text-right font-mono num ${signalCls(b.signal)}`}>{b.score ?? 0}</span>
                </div>
              );
            })}
          </div>
        )}
      </V17Card>
    </div>
  );
}

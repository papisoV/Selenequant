"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { useMarketStore } from "@/lib/store/market";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";

// 业务域插入页 — US Radar 快照
// 端点: /api/v1/us-radar/snapshot
// 真实 shape: { indices[], movers, event_signals[], radar_scores{}, relationship_chains[], event_scenarios[], ai_summary, timestamp }

interface RadarIndex {
  symbol?: string;
  shortName?: string;
  regularMarketPrice?: number;
  regularMarketChangePercent?: number;
  regularMarketChange?: number;
}
interface RadarSignal {
  symbol?: string;
  signal?: string;
  score?: number;
  dimensions?: Record<string, number>;
}
interface SnapshotData {
  indices?: RadarIndex[];
  event_signals?: RadarSignal[];
  radar_scores?: Record<string, { total_score?: number; signal?: string }>;
  ai_summary?: string | null;
  timestamp?: number;
}

const bare = (s: string): string => s.replace(/[-/:].*$/, "");

function fmtTime(ts?: number): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString("en-US", { hour12: false });
}

function signalCls(sig?: string): string {
  if (sig === "long" || sig === "bullish") return "text-pos";
  if (sig === "short" || sig === "bearish") return "text-neg";
  return "text-ink-3";
}

function signalLabel(sig?: string): string {
  if (sig === "long" || sig === "bullish") return "多";
  if (sig === "short" || sig === "bearish") return "空";
  return "中";
}

export function UsRadarPage() {
  const { useApi } = useApiPage({ distinguish: true });
  const { data, isLoading, mutate } = useApi<SnapshotData | null>("/api/v1/us-radar/snapshot");
  const [refreshing, setRefreshing] = useState(false);
  const d = data ?? null;

  const indices = d?.indices ?? [];
  const signals = d?.event_signals ?? [];
  const radarScores = d?.radar_scores ?? {};
  const topByScore = Object.entries(radarScores)
    .map(([symbol, s]) => ({ symbol, ...(s ?? {}) }))
    .filter((s) => s.total_score != null)
    .sort((a, b) => (b.total_score ?? 0) - (a.total_score ?? 0))
    .slice(0, 10);

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  const { setSymBase } = useMarketStore();
  const openSymbol = (sym: string) => {
    setSymBase(bare(sym));
    window.location.href = "/watch/us-radar-symbols-symbol";
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="US 市场雷达"
        right={
          <div className="flex items-center gap-2">
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
        {isLoading && !d ? (
          <div className="text-center text-ink-4 text-[11px] py-3">加载中…</div>
        ) : (
          <div className="flex flex-col gap-2">
            {d?.ai_summary && (
              <div className="rounded bg-surface-2 px-2 py-1.5 text-[10px] text-ink-2">
                {d.ai_summary}
              </div>
            )}
            <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
              <StatBlock label="指数" value={String(indices.length)} />
              <StatBlock label="事件信号" value={String(signals.length)} cls="text-gold" />
              <StatBlock label="评分标的" value={String(Object.keys(radarScores).length)} />
              <StatBlock label="更新时间" value={fmtTime(d?.timestamp) || "—"} cls="text-ink-3" />
            </div>
          </div>
        )}
      </V17Card>

      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(2, 1fr)" }}>
        <V17Card title="指数 (Yahoo)">
          <div className="flex flex-col">
            {indices.length === 0 && !isLoading && (
              <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
            )}
            {indices.map((idx, i) => {
              const pct = idx.regularMarketChangePercent ?? 0;
              return (
                <div key={i} className="flex items-center justify-between gap-2 text-[10px] py-1 border-b border-border-soft">
                  <span className="w-12 truncate text-ink font-medium">{idx.symbol}</span>
                  <span className="flex-1 truncate text-ink-4">{idx.shortName}</span>
                  <span className="w-14 text-right font-mono num text-ink">{idx.regularMarketPrice?.toFixed(2) ?? "—"}</span>
                  <span className={`w-12 text-right font-mono num ${pct >= 0 ? "text-pos" : "text-neg"}`}>
                    {pct > 0 ? "+" : ""}{pct.toFixed(2)}%
                  </span>
                </div>
              );
            })}
          </div>
        </V17Card>

        <V17Card title="事件信号">
          <div className="flex flex-col">
            {signals.length === 0 && !isLoading && (
              <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
            )}
            {signals.map((s, i) => (
              <button
                key={i}
                type="button"
                onClick={() => openSymbol(s.symbol ?? "")}
                className="flex items-center gap-2 text-left text-[10px] py-1 border-b border-border-soft hover:bg-surface-2"
              >
                <span className="w-14 text-ink font-medium">{s.symbol}</span>
                <div className="flex-1 h-2 bg-surface-2 rounded overflow-hidden">
                  <div className="h-full bg-gold" style={{ width: `${Math.min(100, (s.score ?? 0) / 100 * 100)}%` }} />
                </div>
                <span className="w-8 text-right font-mono num text-ink-2">{s.score ?? 0}</span>
                <span className={`w-6 text-right ${signalCls(s.signal)}`}>{signalLabel(s.signal)}</span>
              </button>
            ))}
          </div>
        </V17Card>
      </div>

      <V17Card title="Top 评分标的">
        <div className="flex flex-col">
          {topByScore.length === 0 && !isLoading && (
            <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
          )}
          {topByScore.map((s, i) => (
            <button
              key={s.symbol}
              type="button"
              onClick={() => openSymbol(s.symbol)}
              className="flex items-center gap-2 text-left text-[10px] py-1 border-b border-border-soft hover:bg-surface-2"
            >
              <span className="w-6 text-ink-4">{i + 1}</span>
              <span className="w-14 text-ink font-medium">{s.symbol}</span>
              <div className="flex-1 h-2 bg-surface-2 rounded overflow-hidden">
                <div className="h-full bg-gold" style={{ width: `${Math.min(100, (s.total_score ?? 0) / 100 * 100)}%` }} />
              </div>
              <span className="w-10 text-right font-mono num text-ink-2">{s.total_score ?? 0}</span>
              <span className={`w-8 text-right ${signalCls(s.signal)}`}>{signalLabel(s.signal)}</span>
            </button>
          ))}
        </div>
      </V17Card>
    </div>
  );
}

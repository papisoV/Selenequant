"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { useMarketStore } from "@/lib/store/market";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";

// 符号详情 — /api/v1/us-radar/symbols/{symbol}
// shape: { symbol, radar_score, signal, event_signals[], evidence, risk_flags[], relationship_chains[], event_scenarios[], timestamp }
// radar_score: { total_score, dimensions{}, signals[], weights{} }
// evidence: { quote, news_count, chart_points }

interface SymbolDetailData {
  symbol?: string;
  radar_score?: {
    total_score?: number;
    dimensions?: Record<string, number>;
    signals?: string[];
    weights?: Record<string, number>;
  };
  signal?: string;
  event_signals?: string[];
  evidence?: {
    quote?: Record<string, unknown>;
    news_count?: number;
    chart_points?: number;
  };
  risk_flags?: string[];
  relationship_chains?: unknown[];
  event_scenarios?: unknown[];
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

function signalCls(sig?: string): string {
  if (sig === "long" || sig === "bullish" || sig === "positive") return "text-pos";
  if (sig === "short" || sig === "bearish" || sig === "negative") return "text-neg";
  return "text-ink-3";
}

function fmtTime(ts?: number): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString("en-US", { hour12: false });
}

export function SymbolsSymbolPage() {
  const { useApi } = useApiPage({ distinguish: true });
  const { pageFilter } = useMarketStore();
  const symbol = bare(pageFilter.symbol) || "BTC";
  const { data, isLoading, mutate } = useApi<SymbolDetailData | null>(
    `/api/v1/us-radar/symbols/${symbol}`,
  );
  const [refreshing, setRefreshing] = useState(false);
  const d = data ?? null;

  const dims = Object.entries(d?.radar_score?.dimensions ?? {});
  const signals = d?.event_signals ?? [];
  const riskFlags = d?.risk_flags ?? [];

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  const openBreakdown = () => {
    window.location.href = "/watch/us-radar-scores-symbol-breakdown";
  };
  const openAiConsole = () => {
    window.location.href = "/watch/us-radar-symbols-symbol-ai-console";
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={`符号详情 · ${d?.symbol ?? symbol}`}
        right={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={openBreakdown}
              className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3 hover:text-ink-2"
            >
              评分分解
            </button>
            <button
              type="button"
              onClick={openAiConsole}
              className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3 hover:text-ink-2"
            >
              AI 控制台
            </button>
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
          <StatBlock label="雷达分" value={d?.radar_score?.total_score != null ? String(d.radar_score.total_score) : "—"} cls="text-gold" />
          <StatBlock label="信号" value={d?.signal ?? "—"} cls={signalCls(d?.signal)} />
          <StatBlock label="新闻" value={String(d?.evidence?.news_count ?? 0)} />
          <StatBlock label="K线点" value={String(d?.evidence?.chart_points ?? 0)} />
        </div>
      </V17Card>

      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(2, 1fr)" }}>
        <V17Card title="8 维雷达">
          {dims.length === 0 ? (
            isLoading ? <div className="text-center text-ink-4 text-[11px] py-3">加载中…</div> : <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
          ) : (
            <div className="flex flex-col">
              {dims.map(([dim, score]) => (
                <div key={dim} className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft">
                  <span className="w-14 text-ink-3">{DIM_LABEL[dim] ?? dim}</span>
                  <div className="flex-1 h-2 bg-surface-2 rounded overflow-hidden">
                    <div className={`h-full ${(score ?? 0) >= 55 ? "bg-pos" : (score ?? 0) <= 45 ? "bg-neg" : "bg-gold"}`} style={{ width: `${Math.min(100, (score ?? 0))}%` }} />
                  </div>
                  <span className={`w-8 text-right font-mono num ${(score ?? 0) >= 55 ? "text-pos" : (score ?? 0) <= 45 ? "text-neg" : "text-ink-2"}`}>{score ?? 0}</span>
                </div>
              ))}
            </div>
          )}
        </V17Card>

        <V17Card title="风险标记">
          {riskFlags.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-3">无风险标记</div>
          ) : (
            <div className="flex flex-col gap-1">
              {riskFlags.map((f) => (
                <span key={f} className="rounded bg-neg/15 px-2 py-1 text-[9px] text-neg">{f}</span>
              ))}
            </div>
          )}
        </V17Card>
      </div>

      <V17Card title="事件信号">
        {signals.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-3">暂无信号</div>
        ) : (
          <div className="flex flex-col">
            {signals.map((s, i) => (
              <div key={i} className="border-b border-border-soft py-1 text-[10px] text-ink-2">
                <span className="font-mono text-gold">{s}</span>
              </div>
            ))}
          </div>
        )}
      </V17Card>
    </div>
  );
}

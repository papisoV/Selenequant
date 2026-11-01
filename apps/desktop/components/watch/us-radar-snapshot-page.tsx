"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { useMarketStore } from "@/lib/store/market";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"
import type { Translate } from "@/lib/i18n/types"

// 快照 — /api/v1/us-radar/snapshot
// shape: { indices[], movers{gainers,losers}, event_signals[], radar_scores{}, relationship_chains[], event_scenarios[], ai_summary, timestamp }
// event_signal: { symbol, signal, score, dimensions{} }

interface RadarIndex {
  symbol?: string;
  shortName?: string;
  regularMarketPrice?: number;
  regularMarketChangePercent?: number;
}
interface SnapshotSignal {
  symbol?: string;
  signal?: string;
  score?: number;
  dimensions?: Record<string, number>;
}
interface SnapshotData {
  indices?: RadarIndex[];
  event_signals?: SnapshotSignal[];
  radar_scores?: Record<string, { total_score?: number; signal?: string }>;
  ai_summary?: string | null;
  timestamp?: number;
}

const bare = (s: string): string => s.replace(/[-/:].*$/, "");

function signalCls(sig?: string): string {
  if (sig === "long" || sig === "bullish" || sig === "positive") return "text-pos";
  if (sig === "short" || sig === "bearish" || sig === "negative") return "text-neg";
  return "text-ink-3";
}

function signalLabel(sig: string | undefined, tr: Translate): string {
  if (sig === "long" || sig === "bullish") return tr("多");
  if (sig === "short" || sig === "bearish") return tr("空");
  return tr("中");
}

function fmtTime(ts?: number): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString("en-US", { hour12: false });
}

export function SnapshotPage() {
  const tr = useUiText()

  const { useApi } = useApiPage({ distinguish: true });
  const { data, isLoading, mutate } = useApi<SnapshotData | null>("/api/v1/us-radar/snapshot");
  const [refreshing, setRefreshing] = useState(false);
  const { setSymBase } = useMarketStore();
  const d = data ?? null;

  const indices = d?.indices ?? [];
  const signals = d?.event_signals ?? [];
  const radarScores = Object.entries(d?.radar_scores ?? {});

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  const openSymbol = (sym: string) => {
    setSymBase(bare(sym));
    window.location.href = "/watch/us-radar-symbols-symbol";
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("US Radar 快照")}
        right={
          <div className="flex items-center gap-2">
            <span className="text-[9px] text-ink-4 font-mono">{fmtTime(d?.timestamp)}</span>
            <button
              type="button"
              onClick={handleRefresh}
              disabled={refreshing || isLoading}
              aria-label={tr("刷新")}
              className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
            >
              <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
            </button>
          </div>
        }
      >
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label={tr("指数")} value={String(indices.length)} />
          <StatBlock label={tr("事件信号")} value={String(signals.length)} cls="text-gold" />
          <StatBlock label={tr("评分标的")} value={String(radarScores.length)} />
          <StatBlock label={tr("数据源")} value="Yahoo" cls="text-ink-3" />
        </div>
        {d?.ai_summary && (
          <div className="mt-2 rounded bg-surface-2 px-2 py-1.5 text-[10px] text-ink-2">{d.ai_summary}</div>
        )}
      </V17Card>

      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(2, 1fr)" }}>
        <V17Card title={tr("指数")}>
          {indices.length === 0 ? (
            isLoading ? <div className="text-center text-ink-4 text-[11px] py-3">{tr("加载中…")}</div> : <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
          ) : (
            <div className="flex flex-col">
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
          )}
        </V17Card>

        <V17Card title={tr("事件信号")}>
          {signals.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
          ) : (
            <div className="flex flex-col">
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
                  <span className={`min-w-10 shrink-0 text-right ${signalCls(s.signal)}`}>{signalLabel(s.signal, tr)}</span>
                </button>
              ))}
            </div>
          )}
        </V17Card>
      </div>

      <V17Card title={tr("全量评分")}>
        {radarScores.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
        ) : (
          <div className="flex flex-col">
            {radarScores.map(([sym, sc]) => (
              <button
                key={sym}
                type="button"
                onClick={() => openSymbol(sym)}
                className="flex items-center gap-2 text-left text-[10px] py-1 border-b border-border-soft hover:bg-surface-2"
              >
                <span className="w-16 truncate font-mono text-ink font-medium">{sym}</span>
                <div className="flex-1 h-2 bg-surface-2 rounded overflow-hidden">
                  <div className="h-full bg-gold" style={{ width: `${Math.min(100, (sc.total_score ?? 0) / 100 * 100)}%` }} />
                </div>
                <span className="w-10 text-right font-mono num text-ink-2">{sc.total_score ?? 0}</span>
                <span className={`min-w-10 shrink-0 text-right ${signalCls(sc.signal)}`}>{signalLabel(sc.signal, tr)}</span>
              </button>
            ))}
          </div>
        )}
      </V17Card>
    </div>
  );
}

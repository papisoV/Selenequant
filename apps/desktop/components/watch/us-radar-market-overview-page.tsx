"use client";

import { useState } from "react";
import { RefreshCw, TrendingUp, TrendingDown } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 市场概览 — /api/v1/us-radar/market/overview
// shape: { indices[], sector_scores{}, gainers[], losers[], timestamp }
// index: { symbol, shortName, regularMarketPrice, regularMarketChangePercent }
// mover: { symbol, name, change_pct }

interface RadarIndex {
  symbol?: string;
  shortName?: string;
  regularMarketPrice?: number;
  regularMarketChangePercent?: number;
}
interface Mover {
  symbol?: string;
  name?: string;
  change_pct?: number;
}
interface SectorScore {
  price?: number;
  change_pct?: number;
  signal?: string;
}
interface MarketOverviewData {
  indices?: RadarIndex[];
  sector_scores?: Record<string, SectorScore>;
  gainers?: Mover[];
  losers?: Mover[];
  timestamp?: number;
}

function fmtTime(ts?: number): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString("en-US", { hour12: false });
}

export function MarketOverviewPage() {
  const tr = useUiText()

  const { useApi } = useApiPage({ distinguish: true });
  const { data, isLoading, mutate } = useApi<MarketOverviewData | null>("/api/v1/us-radar/market/overview");
  const [refreshing, setRefreshing] = useState(false);
  const d = data ?? null;

  const indices = d?.indices ?? [];
  const sectorScores = Object.entries(d?.sector_scores ?? {});
  const gainers = d?.gainers ?? [];
  const losers = d?.losers ?? [];

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  const renderMovers = (list: Mover[], positive: boolean) =>
    list.length === 0 ? (
      <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
    ) : (
      list.map((m, i) => (
        <div key={i} className="flex items-center justify-between gap-2 text-[10px] py-1 border-b border-border-soft">
          <div className="flex items-center gap-2 min-w-0">
            <span className="w-12 truncate font-mono text-ink font-medium">{m.symbol}</span>
            <span className="truncate text-ink-4">{m.name}</span>
          </div>
          <span className={`font-mono num ${positive ? "text-pos" : "text-neg"}`}>
            {positive ? "+" : ""}{m.change_pct?.toFixed(2) ?? "—"}%
          </span>
        </div>
      ))
    );

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("市场概览")}
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
          <StatBlock label={tr("板块")} value={String(sectorScores.length)} cls="text-gold" />
          <StatBlock label={tr("领涨")} value={String(gainers.length)} cls="text-pos" />
          <StatBlock label={tr("领跌")} value={String(losers.length)} cls="text-neg" />
        </div>
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

        <V17Card title={tr("板块信号")}>
          {sectorScores.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
          ) : (
            <div className="flex flex-col">
              {sectorScores.map(([sym, sc]) => (
                <div key={sym} className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft">
                  <span className="w-14 truncate font-mono text-ink font-medium">{sym}</span>
                  <div className="flex-1 h-2 bg-surface-2 rounded overflow-hidden">
                    <div className="h-full bg-gold" style={{ width: `${Math.min(100, Math.abs(sc.change_pct ?? 0) * 4)}%` }} />
                  </div>
                  <span className={`font-mono num ${(sc.change_pct ?? 0) >= 0 ? "text-pos" : "text-neg"}`}>
                    {(sc.change_pct ?? 0) > 0 ? "+" : ""}{(sc.change_pct ?? 0).toFixed(2)}%
                  </span>
                  <span className="w-10 text-right text-[9px] text-ink-4">{sc.signal}</span>
                </div>
              ))}
            </div>
          )}
        </V17Card>
      </div>

      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(2, 1fr)" }}>
        <V17Card title={tr("领涨")} right={<TrendingUp className="h-3 w-3 text-pos" />}>
          {renderMovers(gainers, true)}
        </V17Card>
        <V17Card title={tr("领跌")} right={<TrendingDown className="h-3 w-3 text-neg" />}>
          {renderMovers(losers, false)}
        </V17Card>
      </div>
    </div>
  );
}

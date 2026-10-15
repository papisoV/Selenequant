"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useApi } from "@/lib/hooks/use-api";

// 数据层: /api/v1/derivatives/funding-rates → { rates: { SYM: FundingRateEvent } }
// FundingRateEvent: { exchange, symbol, funding_rate, mark_price, index_price, next_funding_time, timestamp }
// 视觉骨架: V17Card + StatBlock (新前端)

interface FundingRateRow {
  exchange: string;
  symbol: string;
  funding_rate: number;
  mark_price: number;
  index_price: number;
  next_funding_time: number;
  timestamp: number;
}

interface FundingRatesResponse {
  rates: Record<string, FundingRateRow> | null;
}

function formatRate(val: number): string {
  const pct = val * 100;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(4)}%`;
}

function formatUsd(val: number): string {
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
  return `$${val.toFixed(2)}`;
}

export function FundingRatesPage() {
  const [forceToken, setForceToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const path = `/api/v1/derivatives/funding-rates${forceToken > 0 ? "?force=true" : ""}`;
  const { data } = useApi<FundingRatesResponse>(path);

  const handleRefresh = () => {
    setRefreshing(true);
    setForceToken((t) => t + 1);
    setTimeout(() => setRefreshing(false), 800);
  };

  const rates = data?.rates ?? {};
  const rows = Object.values(rates).sort((a, b) => b.funding_rate - a.funding_rate);
  const positive = rows.filter((r) => r.funding_rate > 0).length;
  const negative = rows.length - positive;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="资金费率 (WS)"
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
          {rows.length > 0 ? `${rows.length} symbols · 正${positive} / 负${negative}` : "暂无数据"}
        </div>
        {rows.length === 0 ? (
          <div className="flex items-center justify-center h-40 text-[10px] text-ink-4">暂无资金费率数据</div>
        ) : (
          <div className="overflow-auto max-h-96">
            <table className="w-full text-left text-[10px]">
              <thead>
                <tr className="text-ink-4">
                  <th className="py-1 pr-2 font-normal">Symbol</th>
                  <th className="py-1 pr-2 font-normal">费率</th>
                  <th className="py-1 pr-2 font-normal">标记价</th>
                  <th className="py-1 pr-2 font-normal">指数价</th>
                  <th className="py-1 pr-2 font-normal">交易所</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 50).map((r) => (
                  <tr key={r.symbol} className="border-t border-border-soft/60">
                    <td className="py-1 pr-2 text-ink">{r.symbol}</td>
                    <td className={`py-1 pr-2 ${r.funding_rate >= 0 ? "text-pos" : "text-neg"}`}>
                      {formatRate(r.funding_rate)}
                    </td>
                    <td className="py-1 pr-2 text-ink-2">{formatUsd(r.mark_price)}</td>
                    <td className="py-1 pr-2 text-ink-3">{formatUsd(r.index_price)}</td>
                    <td className="py-1 pr-2 text-ink-3">{r.exchange}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </V17Card>
      <V17Card title="费率分布">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label="跟踪数" value={rows.length} />
          <StatBlock label="正费率" value={positive} cls="text-pos" />
          <StatBlock label="负费率" value={negative} cls="text-neg" />
        </div>
      </V17Card>
    </div>
  );
}

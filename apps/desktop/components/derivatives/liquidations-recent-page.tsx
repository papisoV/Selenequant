"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useApi } from "@/lib/hooks/use-api";
import { useUiText } from "@/components/i18n/use-ui-text"

// 数据层: /api/v1/derivatives/liquidations/recent?limit=50 → { events: [LiquidationEvent], count }
// LiquidationEvent: { exchange, symbol, market_type, side(long|short), quantity, price, avg_price,
//   notional_usd, status, timestamp }

interface LiquidationEvent {
  exchange: string;
  symbol: string;
  market_type: string;
  side: "long" | "short";
  quantity: number;
  price: number;
  avg_price: number;
  notional_usd: number;
  status: string;
  timestamp: number;
}

interface LiquidationsResponse {
  events: LiquidationEvent[];
  count: number;
}

const LIMIT_OPTIONS = [20, 50, 100, 200] as const;

function formatUsd(val: number): string {
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
  return `$${val.toFixed(2)}`;
}

function formatPrice(val: number): string {
  if (val >= 1000) return val.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (val >= 1) return val.toFixed(2);
  return val.toFixed(4);
}

function formatTime(ms: number): string {
  const d = new Date(ms);
  return d.toLocaleTimeString(undefined, { hour12: false });
}

export function LiquidationsRecentPage() {
  const tr = useUiText()

  const [limit, setLimit] = useState<number>(50);
  const [forceToken, setForceToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const path = `/api/v1/derivatives/liquidations/recent?limit=${limit}${forceToken > 0 ? "&force=true" : ""}`;
  const { data } = useApi<LiquidationsResponse>(path);

  const handleRefresh = () => {
    setRefreshing(true);
    setForceToken((t) => t + 1);
    setTimeout(() => setRefreshing(false), 800);
  };

  const events = data?.events ?? [];
  const longNotional = events.filter((e) => e.side === "long").reduce((s, e) => s + e.notional_usd, 0);
  const shortNotional = events.filter((e) => e.side === "short").reduce((s, e) => s + e.notional_usd, 0);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("近期爆仓")}
        right={
          <div className="flex items-center gap-1">
            <div className="flex gap-1">
              {LIMIT_OPTIONS.map((n) => (
                <button
                  key={n}
                  onClick={() => setLimit(n)}
                  className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
                    limit === n
                      ? "bg-gold/20 text-gold"
                      : "text-ink-3 hover:bg-surface-2 hover:text-ink-2"
                  }`}
                >
                  {n}
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
          {events.length > 0
            ? `${events.length} 笔 · 多 ${formatUsd(longNotional)} / 空 ${formatUsd(shortNotional)}`
            : tr("暂无数据")}
        </div>
        {events.length === 0 ? (
          <div className="flex items-center justify-center h-40 text-[10px] text-ink-4">{tr("暂无爆仓事件数据")}</div>
        ) : (
          <div className="overflow-auto max-h-96">
            <table className="w-full text-left text-[10px]">
              <thead>
                <tr className="text-ink-4">
                  <th className="py-1 pr-2 font-normal">{tr("时间")}</th>
                  <th className="py-1 pr-2 font-normal">Symbol</th>
                  <th className="py-1 pr-2 font-normal">{tr("方向")}</th>
                  <th className="py-1 pr-2 font-normal">{tr("价格")}</th>
                  <th className="py-1 pr-2 font-normal">{tr("数量")}</th>
                  <th className="py-1 pr-2 font-normal">{tr("金额")}</th>
                  <th className="py-1 pr-2 font-normal">{tr("交易所")}</th>
                </tr>
              </thead>
              <tbody>
                {events.map((e, i) => (
                  <tr key={`${e.symbol}-${e.timestamp}-${i}`} className="border-t border-border-soft/60">
                    <td className="py-1 pr-2 text-ink-3">{formatTime(e.timestamp)}</td>
                    <td className="py-1 pr-2 text-ink">{e.symbol}</td>
                    <td className={`py-1 pr-2 ${e.side === "long" ? "text-pos" : "text-neg"}`}>
                      {e.side.toUpperCase()}
                    </td>
                    <td className="py-1 pr-2 text-ink-2">{formatPrice(e.price)}</td>
                    <td className="py-1 pr-2 text-ink-2">{e.quantity.toFixed(4)}</td>
                    <td className="py-1 pr-2 text-ink">{formatUsd(e.notional_usd)}</td>
                    <td className="py-1 pr-2 text-ink-3">{e.exchange}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </V17Card>
      <V17Card title={tr("爆仓分布")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("总笔数")} value={data?.count ?? 0} />
          <StatBlock label={tr("多单金额")} value={formatUsd(longNotional)} cls="text-pos" />
          <StatBlock label={tr("空单金额")} value={formatUsd(shortNotional)} cls="text-neg" />
        </div>
      </V17Card>
    </div>
  );
}

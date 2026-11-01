"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useApi } from "@/lib/hooks/use-api";
import { useUiText } from "@/components/i18n/use-ui-text"

// 数据层: /api/v1/derivatives/funding-rates/all-exchanges?exchange=all|binance|okx&force=
// → { rates: [FundingRateItem], count, exchanges }
// FundingRateItem 额外带 open_interest / liquidation_volume_24h / long_short_ratio

interface AllExchangeRateRow {
  exchange: string;
  symbol: string;
  funding_rate: number;
  mark_price: number;
  index_price: number;
  next_funding_time: number;
  timestamp: number;
  open_interest?: number | null;
  liquidation_volume_24h?: number | null;
  long_short_ratio?: number | null;
}

interface AllExchangesResponse {
  rates: AllExchangeRateRow[];
  count: number;
  exchanges: string[];
}

const EXCHANGE_OPTIONS = ["all", "binance", "okx"] as const;

function formatRate(val: number): string {
  const pct = val * 100;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(4)}%`;
}

function formatUsd(val: number): string {
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
  return `$${val.toFixed(2)}`;
}

function formatNum(val: number): string {
  if (val >= 1_000_000) return `${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `${(val / 1_000).toFixed(1)}K`;
  return val.toFixed(2);
}

export function FundingRatesAllExchangesPage() {
  const tr = useUiText()

  const [exchange, setExchange] = useState<string>("all");
  const [forceToken, setForceToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const path = `/api/v1/derivatives/funding-rates/all-exchanges?exchange=${exchange}${forceToken > 0 ? "&force=true" : ""}`;
  const { data } = useApi<AllExchangesResponse>(path);

  const handleRefresh = () => {
    setRefreshing(true);
    setForceToken((t) => t + 1);
    setTimeout(() => setRefreshing(false), 800);
  };

  const rates = data?.rates ?? [];
  const positive = rates.filter((r) => r.funding_rate > 0).length;
  const negative = rates.length - positive;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("全交易所资金费率 (REST)")}
        right={
          <div className="flex items-center gap-1">
            <div className="flex gap-1">
              {EXCHANGE_OPTIONS.map((ex) => (
                <button
                  key={ex}
                  onClick={() => setExchange(ex)}
                  className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
                    exchange === ex
                      ? "bg-gold/20 text-gold"
                      : "text-ink-3 hover:bg-surface-2 hover:text-ink-2"
                  }`}
                >
                  {ex}
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
          {rates.length > 0
            ? `${rates.length} symbols (${[...new Set(rates.map((r) => r.exchange))].join(", ")}) · 正${positive} / 负${negative}`
            : tr("暂无数据")}
        </div>
        {rates.length === 0 ? (
          <div className="flex items-center justify-center h-40 text-[10px] text-ink-4">{tr("暂无资金费率数据")}</div>
        ) : (
          <div className="overflow-auto max-h-96">
            <table className="w-full text-left text-[10px]">
              <thead>
                <tr className="text-ink-4">
                  <th className="py-1 pr-2 font-normal">Symbol</th>
                  <th className="py-1 pr-2 font-normal">{tr("交易所")}</th>
                  <th className="py-1 pr-2 font-normal">{tr("费率")}</th>
                  <th className="py-1 pr-2 font-normal">{tr("标记价")}</th>
                  <th className="py-1 pr-2 font-normal">{tr("持仓量")}</th>
                  <th className="py-1 pr-2 font-normal">{tr("多空比")}</th>
                </tr>
              </thead>
              <tbody>
                {rates.slice(0, 100).map((r, i) => (
                  <tr key={`${r.exchange}-${r.symbol}-${i}`} className="border-t border-border-soft/60">
                    <td className="py-1 pr-2 text-ink">{r.symbol}</td>
                    <td className="py-1 pr-2 text-ink-3">{r.exchange}</td>
                    <td className={`py-1 pr-2 ${r.funding_rate >= 0 ? "text-pos" : "text-neg"}`}>
                      {formatRate(r.funding_rate)}
                    </td>
                    <td className="py-1 pr-2 text-ink-2">{formatUsd(r.mark_price)}</td>
                    <td className="py-1 pr-2 text-ink-3">
                      {r.open_interest != null ? formatNum(r.open_interest) : "—"}
                    </td>
                    <td className="py-1 pr-2 text-ink-3">
                      {r.long_short_ratio != null ? r.long_short_ratio.toFixed(3) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </V17Card>
      <V17Card title={tr("统计")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("总费率数")} value={data?.count ?? 0} />
          <StatBlock label={tr("正费率")} value={positive} cls="text-pos" />
          <StatBlock label={tr("负费率")} value={negative} cls="text-neg" />
        </div>
      </V17Card>
    </div>
  );
}

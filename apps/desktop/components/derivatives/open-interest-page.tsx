"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useApi } from "@/lib/hooks/use-api";
import { useUiText } from "@/components/i18n/use-ui-text"

// 数据层: /api/v1/derivatives/open-interest → { snapshots: { SYM: OpenInterestSnapshot } }
// OpenInterestSnapshot: { exchange, symbol, open_interest, open_interest_usd, timestamp }

interface OpenInterestSnapshot {
  exchange: string;
  symbol: string;
  open_interest: number;
  open_interest_usd: number;
  timestamp: number;
}

interface OpenInterestResponse {
  snapshots: Record<string, OpenInterestSnapshot>;
}

function formatNum(val: number): string {
  if (val >= 1_000_000) return `${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `${(val / 1_000).toFixed(1)}K`;
  return val.toFixed(2);
}

function formatUsd(val: number): string {
  if (val >= 1_000_000_000) return `$${(val / 1_000_000_000).toFixed(2)}B`;
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
  return `$${val.toFixed(2)}`;
}

export function OpenInterestPage() {
  const tr = useUiText()

  const [forceToken, setForceToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const path = `/api/v1/derivatives/open-interest${forceToken > 0 ? "?force=true" : ""}`;
  const { data } = useApi<OpenInterestResponse>(path);

  const handleRefresh = () => {
    setRefreshing(true);
    setForceToken((t) => t + 1);
    setTimeout(() => setRefreshing(false), 800);
  };

  const snapshots = data?.snapshots ?? {};
  const rows = Object.values(snapshots).sort((a, b) => b.open_interest_usd - a.open_interest_usd);
  const totalOiUsd = rows.reduce((s, r) => s + r.open_interest_usd, 0);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("未平仓量")}
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
          {rows.length > 0 ? `${rows.length} symbols · 总计 ${formatUsd(totalOiUsd)}` : tr("暂无数据")}
        </div>
        {rows.length === 0 ? (
          <div className="flex items-center justify-center h-40 text-[10px] text-ink-4">{tr("暂无未平仓量数据")}</div>
        ) : (
          <div className="overflow-auto max-h-96">
            <table className="w-full text-left text-[10px]">
              <thead>
                <tr className="text-ink-4">
                  <th className="py-1 pr-2 font-normal">Symbol</th>
                  <th className="py-1 pr-2 font-normal">{tr("持仓量")}</th>
                  <th className="py-1 pr-2 font-normal">{tr("USD 价值")}</th>
                  <th className="py-1 pr-2 font-normal">{tr("交易所")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 50).map((r) => (
                  <tr key={r.symbol} className="border-t border-border-soft/60">
                    <td className="py-1 pr-2 text-ink">{r.symbol}</td>
                    <td className="py-1 pr-2 text-ink-2">{formatNum(r.open_interest)}</td>
                    <td className="py-1 pr-2 text-ink">{formatUsd(r.open_interest_usd)}</td>
                    <td className="py-1 pr-2 text-ink-3">{r.exchange}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </V17Card>
      <V17Card title={tr("持仓统计")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("跟踪数")} value={rows.length} />
          <StatBlock label={tr("总 OI (USD)")} value={formatUsd(totalOiUsd)} cls="text-gold" />
          <StatBlock label={tr("最大 OI")} value={rows.length > 0 ? formatUsd(rows[0].open_interest_usd) : "—"} />
        </div>
      </V17Card>
    </div>
  );
}

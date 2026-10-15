"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useApi } from "@/lib/hooks/use-api";

// 数据层: /api/v1/markets?exchange=binance&market_type=futures → { symbols: [], count, exchange, market_type }

interface MarketsResponse {
  symbols: string[];
  count: number;
  exchange: string;
  market_type: string;
}

const EXCHANGE_OPTIONS = ["binance", "okx"] as const;
const MARKET_TYPE_OPTIONS = ["spot", "futures"] as const;

export function MarketsPage() {
  const [exchange, setExchange] = useState<string>("binance");
  const [marketType, setMarketType] = useState<string>("futures");
  const [forceToken, setForceToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const path = `/api/v1/markets?exchange=${exchange}&market_type=${marketType}${forceToken > 0 ? "&force=true" : ""}`;
  const { data } = useApi<MarketsResponse>(path);

  const handleRefresh = () => {
    setRefreshing(true);
    setForceToken((t) => t + 1);
    setTimeout(() => setRefreshing(false), 800);
  };

  const symbols = data?.symbols ?? [];
  const baseCount = new Set(symbols.map((s) => s.split("-")[0].split("/")[0].split(":")[0].toUpperCase())).size;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="市场符号"
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
            <div className="flex gap-1">
              {MARKET_TYPE_OPTIONS.map((mt) => (
                <button
                  key={mt}
                  onClick={() => setMarketType(mt)}
                  className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
                    marketType === mt
                      ? "bg-gold/20 text-gold"
                      : "text-ink-3 hover:bg-surface-2 hover:text-ink-2"
                  }`}
                >
                  {mt}
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
          {symbols.length > 0
            ? `${symbols.length} 符号 (${baseCount} 基础币) · ${exchange}/${marketType}`
            : "暂无数据"}
        </div>
        {symbols.length === 0 ? (
          <div className="flex items-center justify-center h-40 text-[10px] text-ink-4">暂无市场符号数据</div>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {symbols.map((s) => (
              <span key={s} className="rounded bg-surface-2 px-2 py-0.5 text-[10px] text-ink-2">
                {s}
              </span>
            ))}
          </div>
        )}
      </V17Card>
      <V17Card title="市场统计">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label="总符号数" value={data?.count ?? 0} />
          <StatBlock label="基础币数" value={baseCount} cls="text-gold" />
          <StatBlock label="交易所" value={data?.exchange ?? "—"} />
        </div>
      </V17Card>
    </div>
  );
}

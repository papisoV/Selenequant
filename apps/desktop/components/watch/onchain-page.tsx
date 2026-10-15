"use client";

import { useState } from "react";
import useSWR from "swr";
import { RefreshCw, TrendingUp, TrendingDown, Wallet, BarChart3 } from "lucide-react";
import { V17Card } from "@/components/shared/page-filter-bar";
import { useContextStore } from "@/lib/store/context";
import { MetricBlock, type MetricData } from "./parts/metric-block";

// 旧代码 markets2/onchain-metrics-panel.tsx (251 行) 吸收
// 数据层: /api/v1/onchain/metrics?symbol=X&force=true
// 视觉骨架: V17Card + StatBlock + MetricBlock (新前端)
// 对接逻辑: 3 指标块 (active_addresses/nvt_ratio/sopr) + force refresh + lastUpdate

interface OnchainMetrics {
  active_addresses?: MetricData;
  nvt_ratio?: MetricData;
  sopr?: MetricData;
}

function normalizeSymbol(sym: string): string {
  return sym.split("-")[0] || "BTC";
}

async function fetcher<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" } });
    if (!res.ok) return null;
    const body = await res.json();
    if (body && typeof body === "object" && "data" in body && "meta" in body) {
      if (body.error) return null;
      return (body.data?.metrics ?? body.data ?? null) as T;
    }
    return body as T;
  } catch {
    return null;
  }
}

function formatValue(value: number | undefined, decimals = 2): string {
  if (value === undefined || value === null) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(decimals)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(decimals)}K`;
  return value.toFixed(decimals);
}

export function OnchainPage() {
  const { pageFilter } = useContextStore();
  const baseSym = normalizeSymbol(pageFilter.symbol);
  const [forceToken, setForceToken] = useState(0);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const swrKey = `onchain-${baseSym}-${forceToken}`;
  const path = `/api/v1/onchain/metrics?symbol=${baseSym}${forceToken > 0 ? "&force=true" : ""}`;

  const { data } = useSWR<OnchainMetrics | null>(swrKey, () => fetcher<OnchainMetrics>(path), {
    revalidateOnFocus: false,
    dedupingInterval: 5000,
    onSuccess: () => setLastUpdate(new Date()),
  });

  const d = data ?? null;

  const handleRefresh = () => {
    setRefreshing(true);
    setForceToken((t) => t + 1);
    setTimeout(() => setRefreshing(false), 800);
  };

  const soprValue = d?.sopr?.value;
  const soprPositive = soprValue !== undefined && soprValue >= 1;
  const hasData = d != null;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="链上指标概览"
        right={
          <div className="flex items-center gap-2">
            {lastUpdate && (
              <span className="text-[9px] text-ink-4 font-mono">
                {lastUpdate.toLocaleTimeString("en-US", { hour12: false })}
              </span>
            )}
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
        {!hasData ? (
          <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
        ) : (
          <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
            <MetricBlock
              icon={Wallet}
              iconCls="text-pos"
              label="活跃地址"
              value={formatValue(d?.active_addresses?.value, 0)}
              change24h={d?.active_addresses?.change_24h}
              description="24h 活跃地址数"
            />
            <MetricBlock
              icon={BarChart3}
              iconCls="text-purple-400"
              label="NVT 比率"
              value={formatValue(d?.nvt_ratio?.value, 1)}
              change24h={d?.nvt_ratio?.change_24h}
              description="网络价值交易比"
            />
            <MetricBlock
              icon={soprPositive ? TrendingUp : TrendingDown}
              iconCls={soprPositive ? "text-pos" : "text-neg"}
              label="SOPR"
              value={formatValue(d?.sopr?.value, 3)}
              change24h={d?.sopr?.change_24h}
              description={soprPositive ? "整体盈利 (>=1)" : "整体亏损 (<1)"}
              valueCls={soprPositive ? "text-pos" : "text-neg"}
            />
          </div>
        )}
      </V17Card>
      <V17Card title="数据源">
        <div className="flex flex-col gap-1 text-[10px]">
          <div className="flex justify-between">
            <span className="text-ink-3">数据源</span>
            <span className="text-ink-2 font-mono">Glassnode</span>
          </div>
          <div className="flex justify-between">
            <span className="text-ink-3">缓存</span>
            <span className="text-ink-2 font-mono">5 分钟</span>
          </div>
          <div className="flex justify-between border-t border-border-soft pt-1">
            <span className="text-ink-3">指标</span>
            <span className="text-ink-2 font-mono">
              {[d?.active_addresses, d?.nvt_ratio, d?.sopr].filter(Boolean).length}/3
            </span>
          </div>
          <div className="text-[9px] text-ink-4 pt-1">
            点右上刷新强制绕过缓存重拉 Glassnode
          </div>
        </div>
      </V17Card>
    </div>
  );
}

"use client";

import { useState } from "react";
import useSWR from "swr";
import { RefreshCw, TrendingUp, TrendingDown, Wallet, BarChart3 } from "lucide-react";
import { V17Card } from "@/components/shared/page-filter-bar";
import { useContextStore } from "@/lib/store/context";
import { MetricBlock, type MetricData } from "./parts/metric-block";
import { useUiText } from "@/components/i18n/use-ui-text"

// 孤儿页 onchain-metrics → /api/v1/onchain/metrics?symbol=X&force=Y
// 与 onchain-page 同数据端点,独立组件:3 指标块 + force refresh
// 返回 APIResponse{ data: { symbol, metrics: { active_addresses, nvt_ratio, sopr } } }

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

export function OnchainMetricsPage() {
  const tr = useUiText()

  const { pageFilter } = useContextStore();
  const baseSym = normalizeSymbol(pageFilter.symbol);
  const [forceToken, setForceToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const swrKey = `onchain-metrics-${baseSym}-${forceToken}`;
  const path = `/api/v1/onchain/metrics?symbol=${baseSym}${forceToken > 0 ? "&force=true" : ""}`;

  const { data } = useSWR<OnchainMetrics | null>(swrKey, () => fetcher<OnchainMetrics>(path), {
    revalidateOnFocus: false,
    dedupingInterval: 5000,
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
        title={tr("链上指标")}
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
        {!hasData ? (
          <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
        ) : (
          <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
            <MetricBlock
              icon={Wallet}
              iconCls="text-pos"
              label={tr("活跃地址")}
              value={formatValue(d?.active_addresses?.value, 0)}
              change24h={d?.active_addresses?.change_24h}
              description={tr("24h 活跃地址数")}
            />
            <MetricBlock
              icon={BarChart3}
              iconCls="text-purple-400"
              label={tr("NVT 比率")}
              value={formatValue(d?.nvt_ratio?.value, 1)}
              change24h={d?.nvt_ratio?.change_24h}
              description={tr("网络价值交易比")}
            />
            <MetricBlock
              icon={soprPositive ? TrendingUp : TrendingDown}
              iconCls={soprPositive ? "text-pos" : "text-neg"}
              label="SOPR"
              value={formatValue(d?.sopr?.value, 3)}
              change24h={d?.sopr?.change_24h}
              description={soprPositive ? tr("整体盈利 (>=1)") : tr("整体亏损 (<1)")}
              valueCls={soprPositive ? "text-pos" : "text-neg"}
            />
          </div>
        )}
      </V17Card>
      <V17Card title={tr("数据源")}>
        <div className="flex flex-col gap-1 text-[10px]">
          <div className="flex justify-between">
            <span className="text-ink-3">{tr("数据源")}</span>
            <span className="text-ink-2 font-mono">Glassnode</span>
          </div>
          <div className="flex justify-between">
            <span className="text-ink-3">{tr("缓存")}</span>
            <span className="text-ink-2 font-mono">{tr("5 分钟")}</span>
          </div>
          <div className="flex justify-between border-t border-border-soft pt-1">
            <span className="text-ink-3">{tr("指标")}</span>
            <span className="text-ink-2 font-mono">
              {[d?.active_addresses, d?.nvt_ratio, d?.sopr].filter(Boolean).length}/3
            </span>
          </div>
          <div className="text-[9px] text-ink-4 pt-1">{tr("点右上刷新强制绕过缓存重拉 Glassnode")}</div>
        </div>
      </V17Card>
    </div>
  );
}

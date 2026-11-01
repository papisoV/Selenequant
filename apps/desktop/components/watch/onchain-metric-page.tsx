"use client";

import { useState } from "react";
import useSWR from "swr";
import { RefreshCw, Wallet, BarChart3, TrendingUp } from "lucide-react";
import { useContextStore } from "@/lib/store/context";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 孤儿页 onchain-metrics-active-addresses / nvt-ratio / sopr 共用
// 后端单指标端点 /api/v1/onchain/metrics/{metric}?symbol=X&force=Y
// 返回 APIResponse{ data: { symbol, metric, value, timestamp }, ... }
// 用裸 useSWR(自定义 key + forceBump)而非 useApi:force 参数在 URL 里,
// 若 key=URL 则刷新时 key 会变,SWR 无法就地 revalidate —— bump 让 SWR 视作新 key 重新拉取。

interface OnchainMetricDump {
  symbol?: string;
  metric?: string;
  value?: number | null;
  timestamp?: number;
}

export const ONCHAIN_METRIC_META: Record<
  string,
  { label: string; icon: typeof Wallet; iconCls: string; desc: string }
> = {
  "active-addresses": {
    label: "活跃地址",
    icon: Wallet,
    iconCls: "text-pos",
    desc: "24h 活跃地址数",
  },
  "nvt-ratio": {
    label: "NVT 比率",
    icon: BarChart3,
    iconCls: "text-purple-400",
    desc: "网络价值交易比",
  },
  sopr: {
    label: "SOPR",
    icon: TrendingUp,
    iconCls: "text-gold",
    desc: "花费产出利润率",
  },
};

function normalizeSymbol(sym: string): string {
  return sym.split("-")[0] || "BTC";
}

function fmtValue(v: number | null | undefined, decimals = 2): string {
  if (v === undefined || v === null) return "—";
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(decimals)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(decimals)}K`;
  return v.toFixed(decimals);
}

async function fetcher<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" } });
    if (!res.ok) return null;
    const body = await res.json();
    if (body && typeof body === "object" && "data" in body && "meta" in body) {
      if (body.error) return null;
      return (body.data ?? null) as T;
    }
    return body as T;
  } catch {
    return null;
  }
}

type OnchainMetricKey = "active-addresses" | "nvt-ratio" | "sopr";

export function OnchainMetricPage({ metric }: { metric: OnchainMetricKey }) {
  const tr = useUiText()

  const { pageFilter } = useContextStore();
  const baseSym = normalizeSymbol(pageFilter.symbol);
  const [forceBump, setForceBump] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const path = `/api/v1/onchain/metrics/${metric}?symbol=${baseSym}&force=${forceBump > 0 ? "true" : "false"}`;
  // 自定义稳定 key:force 变不换 key,靠 bump 强制重拉;symbol 变才换 key
  const key = `onchain-${metric}-${baseSym}-${forceBump}`;
  const { data } = useSWR<OnchainMetricDump | null>(key, () => fetcher<OnchainMetricDump>(path), {
    revalidateOnFocus: false,
    dedupingInterval: 5000,
  });

  const d = data ?? null;
  const meta = ONCHAIN_METRIC_META[metric];
  const Icon = meta.icon;

  const handleRefresh = () => {
    setRefreshing(true);
    setForceBump((k) => k + 1); // 新 key → SWR 立即重新拉取(URL 带 force=true 绕过后端缓存)
    setTimeout(() => setRefreshing(false), 700);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={`${tr(meta.label)} — ${baseSym}`}
        right={
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
            aria-label="refresh"
          >
            <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
          </button>
        }
      >
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr(meta.label)} value={fmtValue(d?.value, metric === "sopr" ? 3 : 1)} cls="text-gold" />
          <StatBlock label={tr("指标")} value={d?.metric ?? metric} />
          <StatBlock
            label={tr("时间戳")}
            value={d?.timestamp ? new Date(d.timestamp * 1000).toLocaleTimeString("en-US", { hour12: false }) : "—"}
          />
        </div>
        <div className="pt-2 text-[9px] text-ink-4">
          <Icon className="inline h-3 w-3 mr-1" style={{ color: "inherit" }} />
          {tr(meta.desc)} {tr("· 刷新强制绕过后端缓存")}
        </div>
      </V17Card>
    </div>
  );
}

// WIDGET_LOADERS 以无 prop 渲染,为三个单指标 slug 各导出固定 metric 包装
export function OnchainActiveAddressesPage() {
  return <OnchainMetricPage metric="active-addresses" />;
}
export function OnchainNvtRatioPage() {
  return <OnchainMetricPage metric="nvt-ratio" />;
}
export function OnchainSoprPage() {
  return <OnchainMetricPage metric="sopr" />;
}

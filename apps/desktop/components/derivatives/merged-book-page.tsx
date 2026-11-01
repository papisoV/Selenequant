"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useContextStore } from "@/lib/store/context";
import { useApi } from "@/lib/hooks/use-api";
import dynamic from "next/dynamic";
import type { MergedBookSnapshot } from "./parts/orderbook-depth-heatmap";
import { formatPrice } from "@/lib/format";
import { useUiText } from "@/components/i18n/use-ui-text"

// 旧代码 kingfisher/merged-book-panel.tsx 吸收
// 数据层: /api/v1/derivatives/merged-book?symbol=BTC/USDT
// 视觉骨架: V17Card + StatBlock (新前端)
// 对接逻辑: force refresh + canvas + 4 StatBlock (best_bid/ask/spread/bid_ask_ratio)

const OrderbookDepthHeatmap = dynamic(() => import("./parts/orderbook-depth-heatmap"), { ssr: false }) as React.ComponentType<{
  data: MergedBookSnapshot | null;
  className?: string;
}>;

function normalizeSymbol(sym: string): string {
  const base = sym.split("-")[0] || "BTC";
  return `${base}/USDT`;
}

function formatUSD(val: number): string {
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
  return `$${val.toFixed(0)}`;
}

export function MergedBookPage() {
  const tr = useUiText()

  const { pageFilter } = useContextStore();
  const normSym = normalizeSymbol(pageFilter.symbol);
  const [forceToken, setForceToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const path = `/api/v1/derivatives/merged-book?symbol=${encodeURIComponent(normSym)}${forceToken > 0 ? "&force=true" : ""}`;

  const { data, isLoading } = useApi<MergedBookSnapshot>(path);

  const handleRefresh = () => {
    setRefreshing(true);
    setForceToken((t) => t + 1);
    setTimeout(() => setRefreshing(false), 800);
  };

  const d = data;
  const ratioCls = d && d.bid_ask_ratio >= 1 ? "text-pos" : "text-neg";

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("合并盘口")}
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
          {d ? `${d.symbol} · ${d.exchanges.join("/")} · bid ${d.bids.length} levels · ask ${d.asks.length} levels` : (isLoading ? tr("加载中...") : tr("暂无数据"))}
        </div>
        <div className="h-72 rounded-md overflow-hidden bg-surface-2">
          {d ? (
            <OrderbookDepthHeatmap data={d} className="w-full h-full" />
          ) : (
            <div className="flex items-center justify-center h-full text-[10px] text-ink-4">{tr("暂无合并盘口数据")}</div>
          )}
        </div>
      </V17Card>
      <V17Card title={tr("盘口统计")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label="best bid" value={d ? formatPrice(d.best_bid) : "—"} cls="text-pos" />
          <StatBlock label="best ask" value={d ? formatPrice(d.best_ask) : "—"} cls="text-neg" />
          <StatBlock label="spread" value={d ? formatPrice(d.spread) : "—"} />
          <StatBlock label={tr("bid/ask 比")} value={d ? d.bid_ask_ratio?.toFixed(3) ?? "—" : "—"} cls={ratioCls} />
        </div>
      </V17Card>
      <V17Card title={tr("深度统计")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("买盘深度")} value={d ? formatUSD(d.bid_depth_usd) : "—"} cls="text-pos" />
          <StatBlock label={tr("卖盘深度")} value={d ? formatUSD(d.ask_depth_usd) : "—"} cls="text-neg" />
          <StatBlock label={tr("中间价")} value={d ? formatPrice(d.mid_price) : "—"} cls="text-gold" />
        </div>
      </V17Card>
    </div>
  );
}

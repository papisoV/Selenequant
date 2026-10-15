"use client";

import { useContextStore } from "@/lib/store/context";
import { preload } from "swr";
import { useApi, fetcher } from "@/lib/hooks/use-api";
import { PageFilterBar, V17Card } from "@/components/shared/page-filter-bar";

// 后端 /api/v1/liqratio/current 返回 LiqRatioItem[]
// (derivatives.py:334 — long_liq_weight / short_liq_weight 是多空**爆仓**密度权重,非持仓量)
interface RatioEntry {
  symbol?: string;
  long_liq_weight?: number;
  short_liq_weight?: number;
  net_direction?: number;
  leverage_type?: string;
}

const INTERVALS_HINT = "多空爆仓比 = 多头爆仓密度 / 空头爆仓密度";

const SKEW_LABEL: Record<string, string> = {
  long: "多头拥挤",
  short: "空头拥挤",
  balanced: "均衡",
};

// 设计稿 L4381 renderPageLiqRatio — 多空爆仓比
// hover 预载:预热静态端点进 SWR global cache
export function preloadData() {
  preload("/api/v1/liqratio/current", fetcher).catch(() => {});
}

export function LiqRatioPage() {
  const { pageFilter } = useContextStore();
  const symbols = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK"];

  const { data: realData, isLoading } = useApi<RatioEntry[] | null>(
    `/api/v1/liqratio/current`
  );

  const rows = (() => {
    if (realData && Array.isArray(realData) && realData.length > 0) {
      return realData.map((d) => {
        const longW = Number(d.long_liq_weight ?? 0);
        const shortW = Number(d.short_liq_weight ?? 0);
        const total = longW + shortW;
        const longPct = total > 0 ? ((longW / total) * 100).toFixed(1) : "0.0";
        const shortPct = total > 0 ? ((shortW / total) * 100).toFixed(1) : "0.0";
        const ratio = shortW > 0 ? longW / shortW : longW > 0 ? Infinity : 0;
        const ratioLabel = !Number.isFinite(ratio) ? "∞" : ratio === 0 ? "—" : ratio.toFixed(2);
        const skew = ratio > 1.5 ? "long" : ratio < 0.8 ? "short" : "balanced";
        return { s: d.symbol ?? "?", longPct, shortPct, skew, ratioLabel };
      });
    }
    return [];
  })();

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="多空持仓比">
        <PageFilterBar tfs={["1h", "4h", "24h"]} symbols={symbols} showEx={false} />
        <div className="lr-list flex flex-col gap-1 mt-2">
          {rows.length === 0 ? (
            <div className="py-6 text-center text-[10px] text-ink-4">
              {isLoading ? "加载中..." : "暂无多空比数据"}
            </div>
          ) : rows.map((r) => {
            const skewColor =
              r.skew === "long"
                ? "text-pos"
                : r.skew === "short"
                  ? "text-neg"
                  : "text-ink-4";
            return (
              <div key={r.s} className="lr-row flex items-center gap-2 text-[10px]">
                <span className="lr-sym w-10 text-ink-2">{r.s}</span>
                <div className="lr-bar flex h-3 flex-1 rounded-sm overflow-hidden bg-surface-2">
                  <div className="lr-bar-long bg-pos" style={{ width: `${r.longPct}%` }} />
                  <div className="lr-bar-short bg-neg" style={{ width: `${r.shortPct}%` }} />
                </div>
                <span className="lr-ratio num w-12 text-right font-mono text-ink">{r.ratioLabel}</span>
                <span className={`lr-skew w-14 text-right ${skewColor}`}>{SKEW_LABEL[r.skew] ?? r.skew}</span>
              </div>
            );
          })}
        </div>
      </V17Card>
      <V17Card title="说明">
        <div className="text-[10px] text-ink-4 leading-relaxed">
          {INTERVALS_HINT}(来自爆仓热力图密度,非持仓量)。<br />
          比值 &gt; 1.5: 多头爆仓密集,多头拥挤<br />
          比值 &lt; 0.8: 空头爆仓密集,空头拥挤<br />
          数据源: 各交易所聚合 ({pageFilter.tf} 聚合窗口)
        </div>
      </V17Card>
    </div>
  );
}

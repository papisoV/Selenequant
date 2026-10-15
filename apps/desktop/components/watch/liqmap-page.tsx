"use client";

import { useContextStore } from "@/lib/store/context";
import { preload } from "swr";
import { useApi, fetcher } from "@/lib/hooks/use-api";
import { PageFilterBar, V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { WorkspaceLink } from "@/components/shared/workspace-link";

// 后端 /api/v1/liqmap/current 返回 {long, short, total} 或数组 [{symbol, long, short, total}, ...]
interface LiqItem { symbol?: string; long?: number; short?: number; total?: number; }
interface LiqResponse { long?: number; short?: number; total?: number; items?: LiqItem[]; }

// 设计稿 L4349 renderPageLiqmap — 爆仓热力地图
// exchange × symbol 矩阵,色块强度=爆仓量,绿多红空
// hover 预载:预热静态端点进 SWR global cache
export function preloadData() {
  preload("/api/v1/liqmap/current", fetcher).catch(() => {});
}

export function LiqmapPage() {
  const { data: liqReal, isLoading } = useApi<LiqResponse | LiqItem[]>(
    "/api/v1/liqmap/current"
  );
  const { pageFilter } = useContextStore();
  const exchanges = ["Binance", "OKX", "Bybit", "Bitget", "Hyperliquid"];
  const symbols = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK"];

  // 构建按 symbol 索引的真实数据 map
  const realBySym = new Map<string, LiqItem>();
  if (liqReal) {
    if (Array.isArray(liqReal)) {
      liqReal.forEach((it) => {
        if (it.symbol) realBySym.set(it.symbol, it);
      });
    } else if (liqReal.items) {
      liqReal.items.forEach((it) => {
        if (it.symbol) realBySym.set(it.symbol, it);
      });
    }
  }

  const cells: { sym: string; ex: string; total: number; hue: number; intensity: number }[] = [];
  exchanges.forEach((ex) => {
    symbols.forEach((s) => {
      const realItem = realBySym.get(s);
      const longLiq = Number(realItem?.long ?? 0);
      const shortLiq = Number(realItem?.short ?? 0);
      const total = longLiq + shortLiq;
      const intensity = Math.min(1, total / 100);
      const hue = longLiq > shortLiq ? 145 : 25;
      cells.push({ sym: s, ex, total, hue, intensity });
    });
  });

  const realTotal = Array.from(realBySym.values()).reduce((s, it) => s + Number(it.total ?? 0), 0);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="爆仓热力地图">
        <div className="flex items-center justify-between gap-2">
          <PageFilterBar tfs={["1h", "4h", "24h"]} symbols={symbols} exchanges={exchanges} />
          <WorkspaceLink source="liqmap" />
        </div>
        <div className="flex items-center gap-3 text-[9px] text-ink-4 mt-2 mb-2">
          <span className="flex items-center gap-1">
            <span className="inline-block w-2 h-2 rounded-sm" style={{ background: "#189a30" }} />
            多头拥挤
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-2 h-2 rounded-sm" style={{ background: "#d74745" }} />
            空头拥挤
          </span>
          <span>色块强度 = 爆仓量</span>
        </div>
        {realBySym.size === 0 && !isLoading ? (
          <div className="py-6 text-center text-[10px] text-ink-4">暂无爆仓热力数据</div>
        ) : (
          <div
            className="grid gap-[3px]"
            style={{ gridTemplateColumns: "repeat(8, 1fr)" }}
          >
            {cells.map((c) => (
              <div
                key={`${c.ex}-${c.sym}`}
                className="liq-cell flex flex-col items-center justify-center rounded-sm text-[9px]"
                style={{
                  background: `hsla(${c.hue}, 70%, 45%, ${(0.15 + c.intensity * 0.7).toFixed(2)})`,
                }}
                title={`${c.ex} ${c.sym} 多空爆仓 ${c.total.toFixed(1)}M`}
              >
                <span className="text-ink-2 font-medium">{c.sym}</span>
                <span className="num text-ink-3">{c.total.toFixed(0)}M</span>
              </div>
            ))}
          </div>
        )}
      </V17Card>
      <V17Card title="24h 统计">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label={`总爆仓(${pageFilter.tf})`} value={realTotal > 0 ? `${realTotal.toFixed(0)}M USDT` : "—"} />
          <StatBlock label="多头占比" value={realTotal > 0 ? "—" : "—"} cls="text-pos" />
          <StatBlock label="最大单笔" value="—" cls="text-gold" />
          <StatBlock label="活跃交易所" value={exchanges.length} />
        </div>
      </V17Card>
    </div>
  );
}

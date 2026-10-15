"use client";

import { useContextStore } from "@/lib/store/context";
import { useApi } from "@/lib/hooks/use-api";
import { PageFilterBar, V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { toSymbolPair } from "@/lib/symbol-utils";

// 后端 /api/v1/agg-orderbook/current 返回 {bids: [{price, total_quantity, exchange_breakdown}], asks: [...], spread, mid_price}
interface AggLevel {
  price?: number;
  total_quantity?: number;
  exchange_breakdown?: Record<string, number>;
}
interface AggOrderbook {
  bids?: AggLevel[];
  asks?: AggLevel[];
  spread?: number;
  mid_price?: number;
}

// 设计稿 L4425 renderPageAgg — 聚合盘口
export function AggPage() {
  const { pageFilter } = useContextStore();
  const sym = toSymbolPair(pageFilter.symbol);
  const { data: aggReal, isLoading } = useApi<AggOrderbook>(
    `/api/v1/agg-orderbook/current?symbol=${encodeURIComponent(sym)}&depth=25`
  );

  // 从真实聚合 orderbook 提取每个 exchange 的 best bid/ask
  const realBids: { ex: string; price: number; size: number }[] = [];
  const realAsks: { ex: string; price: number; size: number }[] = [];
  if (aggReal?.bids || aggReal?.asks) {
    const exSet = new Set<string>();
    (aggReal.asks || []).forEach((lvl) => {
      if (lvl.exchange_breakdown) {
        Object.entries(lvl.exchange_breakdown).forEach(([ex, qty]) => {
          if (!exSet.has(ex)) {
            exSet.add(ex);
            realAsks.push({ ex, price: Number(lvl.price ?? 0), size: Number(qty ?? 0) });
          }
        });
      }
    });
    const bidExSet = new Set<string>();
    (aggReal.bids || []).forEach((lvl) => {
      if (lvl.exchange_breakdown) {
        Object.entries(lvl.exchange_breakdown).forEach(([ex, qty]) => {
          if (!bidExSet.has(ex)) {
            bidExSet.add(ex);
            realBids.push({ ex, price: Number(lvl.price ?? 0), size: Number(qty ?? 0) });
          }
        });
      }
    });
  }

  const sortedBids = [...realBids].sort((a, b) => b.price - a.price);
  const sortedAsks = [...realAsks].sort((a, b) => a.price - b.price);
  const bestBid = realBids.length > 0 ? Math.max(...realBids.map((b) => b.price)) : 0;
  const bestAsk = realAsks.length > 0 ? Math.min(...realAsks.map((a) => a.price)) : 0;
  const spread = aggReal?.spread != null ? Number(aggReal.spread) : (bestBid > 0 && bestAsk > 0 ? bestAsk - bestBid : 0);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={`聚合盘口 (${sym}USDT Perp)`}>
        <PageFilterBar tfs={["1h", "4h", "24h"]} showSym={false} />
        {realBids.length === 0 && realAsks.length === 0 ? (
          <div className="py-6 text-center text-[10px] text-ink-4">
            {isLoading ? "加载中..." : "暂无聚合盘口数据"}
          </div>
        ) : (
          <div className="grid gap-3 mt-2" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <div>
              <div className="text-[9px] text-ink-4 mb-1">BIDS</div>
              {sortedBids.map((b) => (
                <div key={b.ex} className="agg-row bid flex justify-between text-[10px] py-0.5 border-b border-border-soft">
                  <span className="agg-ex text-ink-2">{b.ex}</span>
                  <span className="agg-price num text-pos font-mono">{b.price.toFixed(1)}</span>
                  <span className="agg-size num text-ink-3 font-mono">{b.size.toFixed(3)}</span>
                </div>
              ))}
            </div>
            <div>
              <div className="text-[9px] text-ink-4 mb-1">ASKS</div>
              {sortedAsks.map((a) => (
                <div key={a.ex} className="agg-row ask flex justify-between text-[10px] py-0.5 border-b border-border-soft">
                  <span className="agg-ex text-ink-2">{a.ex}</span>
                  <span className="agg-price num text-neg font-mono">{a.price.toFixed(1)}</span>
                  <span className="agg-size num text-ink-3 font-mono">{a.size.toFixed(3)}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </V17Card>
      <V17Card title="最优价差">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label="最优买" value={bestBid > 0 ? bestBid.toFixed(1) : "—"} cls="text-pos" />
          <StatBlock label="最优卖" value={bestAsk > 0 ? bestAsk.toFixed(1) : "—"} cls="text-neg" />
          <StatBlock label="价差" value={spread > 0 ? spread.toFixed(2) : "—"} />
        </div>
      </V17Card>
    </div>
  );
}

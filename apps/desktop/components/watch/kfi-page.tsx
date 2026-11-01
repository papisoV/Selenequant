"use client";

import { useContextStore } from "@/lib/store/context";
import { useApi } from "@/lib/hooks/use-api";
import { PageFilterBar, V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

interface KfIndexRow {
  s: string;
  inflow: number;
  outflow: number;
  net: number;
  score: number;
}

// 设计稿 L3994 kfi 主力指数 — whale-flow / 大单流入流出
// 后端 /api/v1/kf-index/list (APIResponse)
export function KfiPage() {
  const tr = useUiText()

  const { pageFilter } = useContextStore();
  const symbols = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK"];

  const { data: realData, isLoading } = useApi<{ symbol?: string; inflow?: number; outflow?: number; net?: number; score?: number }[]>(
    "/api/v1/kf-index/list"
  );

  const rows: KfIndexRow[] = (() => {
    if (realData && realData.length > 0) {
      return realData.map((d) => {
        const inflow = Number(d.inflow ?? 0);
        const outflow = Number(d.outflow ?? 0);
        const net = Number(d.net ?? inflow - outflow);
        const score = Math.min(100, Math.abs(net) / 2);
        return { s: d.symbol ?? "?", inflow, outflow, net, score };
      });
    }
    return [];
  })();

  const totalNet = rows.reduce((s, r) => s + r.net, 0);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("主力资金流")}>
        <PageFilterBar tfs={["1h", "4h", "24h"]} symbols={symbols} showEx={false} />
        <div className="flex flex-col mt-2">
          <div className="grid grid-cols-[80px_1fr_1fr_1fr_60px] gap-2 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono">
            <span>SYMBOL</span>
            <span className="text-right">INFLOW (M)</span>
            <span className="text-right">OUTFLOW (M)</span>
            <span className="text-right">NET (M)</span>
            <span className="text-right">SCORE</span>
          </div>
          {rows.length === 0 ? (
            <div className="py-6 text-center text-[10px] text-ink-4">
              {isLoading ? tr("加载中...") : tr("暂无主力资金流数据")}
            </div>
          ) : rows.map((r) => (
            <div
              key={r.s}
              className="grid grid-cols-[80px_1fr_1fr_1fr_60px] gap-2 text-[10px] py-1 border-b border-border-soft items-center"
            >
              <span className="text-gold font-mono">{r.s}</span>
              <span className="num text-right text-pos font-mono">+{r.inflow.toFixed(1)}</span>
              <span className="num text-right text-neg font-mono">-{r.outflow.toFixed(1)}</span>
              <span className={`num text-right font-mono ${r.net >= 0 ? "text-pos" : "text-neg"}`}>
                {r.net >= 0 ? "+" : ""}{r.net.toFixed(1)}
              </span>
              <span className="num text-right text-ink-3 font-mono">{r.score.toFixed(0)}</span>
            </div>
          ))}
        </div>
      </V17Card>
      <V17Card title={tr("统计")}>
        <div className="grid grid-cols-4 gap-2">
          <StatBlock label={tr("总净流入")} value={rows.length > 0 ? `${totalNet.toFixed(1)}M` : "—"} cls={totalNet >= 0 ? "text-pos" : "text-neg"} />
          <StatBlock label={tr("最大单笔")} value="—" cls="text-gold" />
          <StatBlock label={tr("活跃币种")} value={rows.length > 0 ? rows.length : "—"} />
          <StatBlock label={tr("数据源")} value="whale-flow" />
        </div>
      </V17Card>
    </div>
  );
}

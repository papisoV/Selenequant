"use client";

import { useEffect, useRef } from "react";
import { useContextStore } from "@/lib/store/context";
import { useApi } from "@/lib/hooks/use-api";
import { PageFilterBar, V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { WorkspaceLink } from "@/components/shared/workspace-link";
import { toSymbolPair } from "@/lib/symbol-utils";

// 后端 /api/v1/vchart 返回 {closes: [], indicators: {rsi14, macd, atr14, ema200}, last_price, close_times}
interface VchartData {
  closes?: number[];
  indicators?: {
    rsi14?: number;
    macd?: number;
    macd_signal?: number;
    macd_hist?: number;
    atr14?: number;
    ema200?: number;
  };
  last_price?: number;
  close_times?: number[];
}

// 设计稿 L4487 renderPageVChart + L4500 renderVchartEchart — 专业图表
// 使用 ECharts 渲染 K 线 (设计稿用 ECharts,不用 lightweight-charts)
export function VchartPage() {
  const { pageFilter } = useContextStore();
  const symPair = toSymbolPair(pageFilter.symbol);
  const base = symPair.split("/")[0];
  const apiSym = `${base}-USDT-SWAP`;
  const interval = pageFilter.tf === "1h" ? "1h" : pageFilter.tf === "4h" ? "4h" : pageFilter.tf === "1d" ? "1d" : "15m";
  const { data: vchartReal, isLoading } = useApi<VchartData>(
    `/api/v1/vchart?symbol=${encodeURIComponent(apiSym)}&interval=${interval}`
  );
  const hostRef = useRef<HTMLDivElement>(null);

  const realCloses = vchartReal?.closes;
  const ind = vchartReal?.indicators;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    if (!realCloses || realCloses.length === 0) return;
    // 动态 import echarts,避免 SSR 报错
    import("echarts").then((echarts) => {
      const n = pageFilter.tf === "1h" ? 90 : pageFilter.tf === "4h" ? 60 : 30;
      const candles = realCloses.slice(-n).map((c, i) => {
        const close = Number(c);
        const open = i > 0 ? Number(realCloses[realCloses.length - n + i - 1] ?? c) : close;
        const high = Math.max(open, close) * 1.001;
        const low = Math.min(open, close) * 0.999;
        return { open, close, high, low, time: `T${i}` };
      });
      const chart = echarts.init(host, "dark", { renderer: "canvas" });
      chart.setOption({
        backgroundColor: "transparent",
        animation: false,
        grid: { left: 50, right: 20, top: 10, bottom: 20 },
        xAxis: {
          type: "category",
          data: candles.map((c) => c.time),
          axisLabel: { color: "#6e7276", fontSize: 9 },
          axisLine: { lineStyle: { color: "#1f2225" } },
        },
        yAxis: {
          type: "value",
          scale: true,
          axisLabel: { color: "#6e7276", fontSize: 9 },
          splitLine: { lineStyle: { color: "#15191d" } },
        },
        series: [
          {
            type: "candlestick",
            data: candles.map((c) => [c.open, c.close, c.low, c.high]),
            itemStyle: {
              color: "#5bb661",
              color0: "#ed756e",
              borderColor: "#5bb661",
              borderColor0: "#ed756e",
            },
          },
        ],
      });
      return () => chart.dispose();
    });
  }, [symPair, pageFilter.tf, realCloses]);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <PageFilterBar tfs={["1h", "4h", "24h"]} showEx={false} />
        <WorkspaceLink source="vchart" />
      </div>
      <div
        ref={hostRef}
        id="vchartHost"
        style={{ width: "100%", height: 340, background: "var(--surface-1)", borderRadius: 3, display: "flex", alignItems: "center", justifyContent: "center" }}
      >
        {(!realCloses || realCloses.length === 0) && (
          <div className="text-[10px] text-ink-4">
            {isLoading ? "加载中..." : "暂无 K 线数据"}
          </div>
        )}
      </div>
      <V17Card title="指标">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label="RSI(14)" value={ind?.rsi14 != null ? ind.rsi14.toFixed(1) : "—"} />
          <StatBlock label="MACD" value={ind?.macd != null ? `${ind.macd >= 0 ? "+" : ""}${ind.macd.toFixed(1)}` : "—"} cls={ind?.macd != null && ind.macd >= 0 ? "text-pos" : "text-neg"} />
          <StatBlock label="ATR(14)" value={ind?.atr14 != null ? ind.atr14.toFixed(2) : "—"} />
          <StatBlock label="EMA200" value={ind?.ema200 != null ? ind.ema200.toFixed(1) : "—"} />
        </div>
      </V17Card>
    </div>
  );
}

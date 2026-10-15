"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useContextStore } from "@/lib/store/context";
import { useApi } from "@/lib/hooks/use-api";
import dynamic from "next/dynamic";
import type { FootprintBarEvent } from "./parts/footprint-canvas";

// 旧代码 kingfisher/footprint-panel.tsx 吸收
// 数据层: /api/v1/derivatives/footprint?symbol=BTC/USDT&interval=5m&exchange=Binance
// 视觉骨架: V17Card + StatBlock (新前端)
// 对接逻辑: interval 4 按钮 + exchange 3 按钮 + force refresh + canvas
// derivatives-footprint-history 复用此 page, endpoint 加 /history 后缀

const FootprintCanvas = dynamic(() => import("./parts/footprint-canvas"), { ssr: false }) as React.ComponentType<{
  data: FootprintBarEvent | null;
  className?: string;
}>;

const INTERVAL_OPTIONS = [
  { value: "5m", label: "5m" },
  { value: "15m", label: "15m" },
  { value: "1h", label: "1H" },
  { value: "4h", label: "4H" },
] as const;

const EXCHANGE_OPTIONS = ["Binance", "OKX", "Bybit"] as const;

function normalizeSymbol(sym: string): string {
  const base = sym.split("-")[0] || "BTC";
  return `${base}/USDT`;
}

function formatVol(val: number): string {
  if (val >= 1_000_000) return `${(val / 1_000_000).toFixed(2)}M`;
  if (val >= 1_000) return `${(val / 1_000).toFixed(1)}K`;
  return val.toFixed(2);
}

function formatPrice(val: number): string {
  if (val >= 1000) return val.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (val >= 1) return val.toFixed(2);
  return val.toFixed(4);
}

interface Props {
  history?: boolean;
}

export function FootprintPage({ history = false }: Props = {}) {
  const { pageFilter } = useContextStore();
  const normSym = normalizeSymbol(pageFilter.symbol);
  const [interval, setInterval_] = useState<string>("5m");
  const [exchange, setExchange] = useState<string>("Binance");
  const [forceToken, setForceToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const basePath = history ? "/api/v1/derivatives/footprint/history" : "/api/v1/derivatives/footprint";
  const path = `${basePath}?symbol=${encodeURIComponent(normSym)}&interval=${interval}&exchange=${exchange}${forceToken > 0 ? "&force=true" : ""}`;

  // history 端点返回 {bars: FootprintBarEvent[]}, live 端点返回单条 — 用 unknown 类型再 narrow
  const { data: rawData } = useApi<unknown>(path);

  const handleRefresh = () => {
    setRefreshing(true);
    setForceToken((t) => t + 1);
    setTimeout(() => setRefreshing(false), 800);
  };

  // history 模式 rawData 是 {bars: FootprintBarEvent[]}, 取最新一条; live 模式 rawData 是单条
  const historyBars: FootprintBarEvent[] = history && rawData && typeof rawData === "object" && "bars" in rawData
    ? (rawData as { bars: FootprintBarEvent[] }).bars ?? []
    : [];
  const liveData = history
    ? (historyBars[historyBars.length - 1] ?? null)
    : (rawData as FootprintBarEvent | null);
  const d = liveData;
  const delta = d?.delta ?? (d ? d.total_buy_volume - d.total_sell_volume : 0);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={history ? "成交足迹 (历史)" : "成交足迹"}
        right={
          <div className="flex items-center gap-1">
            <div className="flex gap-1">
              {INTERVAL_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setInterval_(opt.value)}
                  className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
                    interval === opt.value
                      ? "bg-gold/20 text-gold"
                      : "text-ink-3 hover:bg-surface-2 hover:text-ink-2"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
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
          {d ? `${d.symbol} · ${d.interval} · ${d.exchange} · ${d.levels.length} levels` : "暂无数据"}
        </div>
        <div className="h-72 rounded-md overflow-hidden bg-surface-2">
          {d ? (
            <FootprintCanvas data={d} className="w-full h-full" />
          ) : (
            <div className="flex items-center justify-center h-full text-[10px] text-ink-4">暂无成交足迹数据</div>
          )}
        </div>
      </V17Card>
      <V17Card title="bar 统计">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock
            label="Delta"
            value={d ? `${delta > 0 ? "+" : ""}${formatVol(delta)}` : "—"}
            cls={delta >= 0 ? "text-pos" : "text-neg"}
          />
          <StatBlock label="最高" value={d ? formatPrice(d.high_price) : "—"} />
          <StatBlock label="最低" value={d ? formatPrice(d.low_price) : "—"} />
          <StatBlock label="总成交" value={d ? formatVol(d.total_buy_volume + d.total_sell_volume) : "—"} cls="text-gold" />
        </div>
      </V17Card>
    </div>
  );
}

export function FootprintHistoryPage() {
  return <FootprintPage history />;
}

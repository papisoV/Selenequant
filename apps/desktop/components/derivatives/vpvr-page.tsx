"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useContextStore } from "@/lib/store/context";
import { useApi } from "@/lib/hooks/use-api";
import dynamic from "next/dynamic";
import type { VPVRData } from "./parts/vpvr-canvas";
import { useUiText } from "@/components/i18n/use-ui-text"

// 旧代码 kingfisher/vpvr-panel.tsx 吸收
// 数据层: /api/v1/derivatives/vpvr?symbol=BTC/USDT&window=1h
// 视觉骨架: V17Card + StatBlock (新前端)
// 对接逻辑: window 5 按钮 + force refresh + POC/VA canvas

const VPVRCanvas = dynamic(() => import("./parts/vpvr-canvas"), { ssr: false }) as React.ComponentType<{
  data: VPVRData | null;
  className?: string;
}>;

const WINDOW_OPTIONS = [
  { value: "15m", label: "15m" },
  { value: "30m", label: "30m" },
  { value: "1h", label: "1H" },
  { value: "4h", label: "4H" },
  { value: "24h", label: "24H" },
] as const;

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

export function VpvrPage() {
  const tr = useUiText()

  const { pageFilter } = useContextStore();
  const normSym = normalizeSymbol(pageFilter.symbol);
  const [window_, setWindow] = useState<string>("1h");
  const [forceToken, setForceToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const path = `/api/v1/derivatives/vpvr?symbol=${encodeURIComponent(normSym)}&window=${window_}${forceToken > 0 ? "&force=true" : ""}`;

  const { data } = useApi<VPVRData>(path);

  const handleRefresh = () => {
    setRefreshing(true);
    setForceToken((t) => t + 1);
    setTimeout(() => setRefreshing(false), 800);
  };

  const d = data;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("成交量分布")}
        right={
          <div className="flex items-center gap-1">
            <div className="flex gap-1">
              {WINDOW_OPTIONS.map((w) => (
                <button
                  key={w.value}
                  onClick={() => setWindow(w.value)}
                  className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
                    window_ === w.value
                      ? "bg-gold/20 text-gold"
                      : "text-ink-3 hover:bg-surface-2 hover:text-ink-2"
                  }`}
                >
                  {w.label}
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
          {d ? `${d.symbol} · ${d.window} · ${d.exchange} · ${d.levels.length} levels` : tr("暂无数据")}
        </div>
        <div className="h-72 rounded-md overflow-hidden bg-surface-2">
          {d ? (
            <VPVRCanvas data={d} className="w-full h-full" />
          ) : (
            <div className="flex items-center justify-center h-full text-[10px] text-ink-4">{tr("暂无成交量分布数据")}</div>
          )}
        </div>
      </V17Card>
      <V17Card title={tr("关键价位")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label="POC" value={d ? formatPrice(d.poc_price) : "—"} cls="text-gold" />
          <StatBlock label="VAH" value={d ? formatPrice(d.va_high) : "—"} cls="text-pos" />
          <StatBlock label="VAL" value={d ? formatPrice(d.va_low) : "—"} cls="text-neg" />
        </div>
      </V17Card>
      <V17Card title={tr("成交量统计")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("总成交")} value={d ? formatVol(d.total_volume) : "—"} />
          <StatBlock label={tr("买单")} value={d ? formatVol(d.total_buy_volume) : "—"} cls="text-pos" />
          <StatBlock label={tr("卖单")} value={d ? formatVol(d.total_sell_volume) : "—"} cls="text-neg" />
        </div>
      </V17Card>
    </div>
  );
}

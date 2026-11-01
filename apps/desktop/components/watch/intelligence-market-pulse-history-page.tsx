"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 市场脉搏历史 — /api/v1/intelligence/market-pulse/history
// shape: { current{timestamp,btc_dominance,fear_greed_index,market_cap}, pulses[{timestamp,btc_dominance,fear_greed_index,market_cap}] }

interface Pulse {
  timestamp?: number;
  btc_dominance?: number;
  fear_greed_index?: number;
  market_cap?: number;
  data_status?: "live" | "stale" | "unavailable";
  fetched_at?: number;
  source?: string;
  reason?: string;
}
interface MarketPulseData {
  current?: Pulse;
  pulses?: Pulse[];
  accumulating?: boolean;
}

type DataStatus = NonNullable<Pulse["data_status"]>;

const STATUS_META: Record<DataStatus, { label: string; cls: string }> = {
  live: { label: "实时", cls: "text-pos" },
  stale: { label: "过期", cls: "text-gold" },
  unavailable: { label: "不可用", cls: "text-ink-4" },
};

function StatusBadge({ p }: { p?: Pulse }) {
  const tr = useUiText()
  const s = p?.data_status;
  if (!s || s === "live") return null;
  const meta = STATUS_META[s];
  const detail = s === "stale" && p?.fetched_at ? ` ${fmtTime(p.fetched_at)}` : s === "unavailable" && p?.reason ? ` ${p.reason}` : "";
  return <span className={`text-[10px] ${meta.cls}`}>[{tr(meta.label)}{detail}]</span>;
}

function fmtTime(ts?: number): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

const fmtCompact = (n?: number): string =>
  n == null ? "—" : n >= 1e9 ? `${(n / 1e9).toFixed(2)}B` : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : String(n);

function fgCls(v?: number): string {
  if (v == null) return "text-ink-3";
  if (v <= 25) return "text-neg";
  if (v >= 75) return "text-pos";
  if (v >= 45 && v <= 55) return "text-gold";
  return "text-ink-2";
}

export function IntelligenceMarketPulseHistoryPage() {
  const tr = useUiText()

  const { useApi } = useApiPage();
  const { data, isLoading, mutate } = useApi<MarketPulseData | null>(
    "/api/v1/intelligence/market-pulse/history",
  );
  const [refreshing, setRefreshing] = useState(false);
  const d = data ?? null;
  const pulses = d?.pulses ?? [];

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("市场脉搏")}
        right={
          <button
            type="button"
            onClick={handleRefresh}
            disabled={refreshing || isLoading}
            aria-label={tr("刷新")}
            className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
          >
            <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
          </button>
        }
      >
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label={tr("BTC 占比")} value={d?.current?.btc_dominance != null ? `${d.current.btc_dominance}%` : "—"} cls="text-ink" />
          <StatBlock label={tr("恐慌贪婪")} value={d?.current?.fear_greed_index != null ? String(d.current.fear_greed_index) : "—"} cls={fgCls(d?.current?.fear_greed_index)} />
          <StatBlock label={tr("总市值")} value={fmtCompact(d?.current?.market_cap)} cls="text-ink-3" />
          <StatBlock label={tr("更新时间")} value={fmtTime(d?.current?.timestamp)} cls="text-ink-4" />
        </div>
        {(d?.current?.data_status || d?.accumulating) && (
          <div className="text-[10px] text-ink-4">
            <StatusBadge p={d?.current} />
            {d?.current?.source ? <span className="ml-1">{tr("来源:")} {d.current.source}</span> : null}
            {d?.accumulating ? <span className="ml-1">{tr("· 历史累积中")}</span> : null}
          </div>
        )}
      </V17Card>

      <V17Card title={tr("历史脉冲 (最近 24h)")}>
        {pulses.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-3">
            {isLoading ? tr("加载中…") : tr("暂无数据")}
          </div>
        ) : (
          <div className="max-h-96 overflow-auto">
            <table className="w-full text-[10px]">
              <thead>
                <tr className="text-left text-ink-4 border-b border-border-soft">
                  <th className="py-1 pr-2 font-normal">{tr("时间")}</th>
                  <th className="py-1 pr-2 font-normal text-right">{tr("BTC 占比")}</th>
                  <th className="py-1 pr-2 font-normal text-right">{tr("恐慌贪婪")}</th>
                  <th className="py-1 font-normal text-right">{tr("总市值")}</th>
                </tr>
              </thead>
              <tbody>
                {pulses.map((p, i) => (
                  <tr key={i} className="border-b border-border-soft/50">
                    <td className="py-1 pr-2 text-ink-4 font-mono">{fmtTime(p.timestamp)}</td>
                    <td className={`py-1 pr-2 text-right font-mono num text-ink-2`}>{p.btc_dominance?.toFixed(1)}%</td>
                    <td className={`py-1 pr-2 text-right font-mono num ${fgCls(p.fear_greed_index)}`}>{p.fear_greed_index}</td>
                    <td className="py-1 text-right font-mono num text-ink-3">{fmtCompact(p.market_cap)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </V17Card>
    </div>
  );
}

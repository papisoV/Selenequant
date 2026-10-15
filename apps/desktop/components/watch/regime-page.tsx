"use client";

import { useApi } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useContextStore } from "@/lib/store/context";

// 业务域插入页 — 市场体制
// 后端 /api/v1/regime 返回 {regime, annual_return, volatility, symbol, timestamp}
// (分类：bull/bear/sideways/transition)
interface RegimeData {
  regime?: string;
  annual_return?: number;
  volatility?: number;
  symbol?: string;
  timestamp?: number;
}

const REGIME_LABEL: Record<string, string> = {
  bull: "牛市",
  bear: "熊市",
  sideways: "震荡",
  transition: "转换期",
};

const REGIME_CLS: Record<string, string> = {
  bull: "text-pos",
  bear: "text-neg",
  transition: "text-neg",
  sideways: "text-gold",
};

export function RegimePage() {
  const { pageFilter } = useContextStore();
  const { symbol } = pageFilter;
  const { data } = useApi<RegimeData | null>(
    `/api/v1/regime?symbol=${symbol}`
  );
  const d = data ?? null;
  const regime = d?.regime ?? "—";
  const label = REGIME_LABEL[d?.regime ?? ""] ?? "—";
  const cls = REGIME_CLS[d?.regime ?? ""] ?? "text-ink-3";

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="当前体制">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label="体制" value={label} cls={cls} />
          <StatBlock label="年化收益" value={d?.annual_return != null ? `${(d.annual_return * 100).toFixed(1)}%` : "—"} cls={d?.annual_return != null ? (d.annual_return >= 0 ? "text-pos" : "text-neg") : "text-ink-4"} />
          <StatBlock label="波动率" value={d?.volatility != null ? `${(d.volatility * 100).toFixed(1)}%` : "—"} cls="text-gold" />
          <StatBlock label="更新" value={d?.timestamp ? new Date(d.timestamp).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }) : "—"} />
        </div>
        <div className="text-[9px] text-ink-4 mt-1.5">
          数据源: /api/v1/regime · {regime} · {d?.symbol ?? symbol}
        </div>
      </V17Card>
    </div>
  );
}

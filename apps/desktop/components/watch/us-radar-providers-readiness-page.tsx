"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 供应商就绪 — /api/v1/us-radar/providers/readiness
// shape: { providers{}, configured_count, available_count, timestamp }
// provider: { mode, configured, available, reason, capabilities[] }

interface Provider {
  mode?: string;
  configured?: boolean;
  available?: boolean;
  reason?: string | null;
  capabilities?: string[];
}
interface ReadinessData {
  providers?: Record<string, Provider>;
  configured_count?: number;
  available_count?: number;
  timestamp?: number;
}

const MODE_STYLE: Record<string, string> = {
  live: "text-pos",
  offline: "text-ink-4",
};

function fmtTime(ts?: number): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString("en-US", { hour12: false });
}

export function ProvidersReadinessPage() {
  const tr = useUiText()

  const { useApi } = useApiPage({ distinguish: true });
  const { data, isLoading, mutate } = useApi<ReadinessData | null>("/api/v1/us-radar/providers/readiness");
  const [refreshing, setRefreshing] = useState(false);
  const d = data ?? null;

  const providers = Object.entries(d?.providers ?? {});

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("供应商就绪")}
        right={
          <div className="flex items-center gap-2">
            <span className="text-[9px] text-ink-4 font-mono">{fmtTime(d?.timestamp)}</span>
            <button
              type="button"
              onClick={handleRefresh}
              disabled={refreshing || isLoading}
              aria-label={tr("刷新")}
              className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
            >
              <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
            </button>
          </div>
        }
      >
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label={tr("供应商")} value={String(providers.length)} />
          <StatBlock label={tr("已配置")} value={String(d?.configured_count ?? 0)} cls="text-gold" />
          <StatBlock label={tr("可用")} value={String(d?.available_count ?? 0)} cls="text-pos" />
          <StatBlock label={tr("模式")} value={d?.providers?.public?.mode ?? "—"} cls="text-ink-3" />
        </div>
      </V17Card>

      <V17Card title={tr("就绪矩阵")}>
        {providers.length === 0 ? (
          isLoading ? <div className="text-center text-ink-4 text-[11px] py-3">{tr("加载中…")}</div> : <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
        ) : (
          <div className="flex flex-col">
            {providers.map(([name, p]) => (
              <div key={name} className="flex items-center gap-2 border-b border-border-soft py-1.5 text-[10px]">
                <span className="w-20 font-mono font-medium text-ink">{name}</span>
                <span className={`w-14 ${MODE_STYLE[p.mode ?? ""] ?? "text-ink-3"}`}>{p.mode}</span>
                <span className={`w-16 ${p.available ? "text-pos" : "text-neg"}`}>
                  {p.available ? tr("可用") : tr("不可用")}
                </span>
                <span className={`w-12 ${p.configured ? "text-gold" : "text-ink-4"}`}>
                  {p.configured ? tr("已配置") : tr("未配置")}
                </span>
                <span className="flex-1 truncate text-ink-4">{p.reason ?? "—"}</span>
                <span className="font-mono text-[9px] text-ink-4">{(p.capabilities ?? []).join(" / ")}</span>
              </div>
            ))}
          </div>
        )}
      </V17Card>
    </div>
  );
}

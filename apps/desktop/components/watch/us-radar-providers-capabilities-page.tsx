"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { V17Card } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 供应商能力 — /api/v1/us-radar/providers/capabilities
// shape: { providers{}, dimensions_coverage{}, timestamp }
// provider: { status, mode, configured, available, capabilities{}, entitlement_status_by_capability{},
//             l1_entitlement_status, l2_entitlement_status, freshness_sla_ms, observed_lag_ms,
//             limitations[], missing_credentials[], auth, cost }

interface ProviderCaps {
  status?: string;
  mode?: string;
  configured?: boolean;
  available?: boolean;
  capabilities?: Record<string, boolean>;
  entitlement_status_by_capability?: Record<string, string>;
  l1_entitlement_status?: string;
  l2_entitlement_status?: string;
  freshness_sla_ms?: number;
  observed_lag_ms?: number | null;
  limitations?: string[];
  missing_credentials?: string[];
  auth?: string;
  cost?: string;
}
interface CapabilitiesData {
  providers?: Record<string, ProviderCaps>;
  dimensions_coverage?: Record<string, Record<string, string>>;
  timestamp?: number;
}

const STATUS_STYLE: Record<string, string> = {
  primary: "text-pos",
  enabled: "text-pos",
  live: "text-pos",
  not_configured: "text-ink-4",
  offline: "text-ink-4",
  unavailable: "text-neg",
};

const CAPABILITY_LABEL: Record<string, string> = {
  quotes: "报价",
  level1: "L1",
  level2: "L2",
  options: "期权",
  candles: "K线",
  news: "新闻",
  charts: "图表",
  market_summary: "市场摘要",
  trending: "热点",
  movers: "异动",
  filings: "申报",
};

function fmtTime(ts?: number): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString("en-US", { hour12: false });
}

export function ProvidersCapabilitiesPage() {
  const tr = useUiText()

  const { useApi } = useApiPage({ distinguish: true });
  const { data, isLoading, mutate } = useApi<CapabilitiesData | null>("/api/v1/us-radar/providers/capabilities");
  const [refreshing, setRefreshing] = useState(false);
  const d = data ?? null;

  const providers = Object.entries(d?.providers ?? {});
  const dimCoverage = Object.entries(d?.dimensions_coverage ?? {});

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("供应商能力矩阵")}
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
        {providers.length === 0 ? (
          isLoading ? <div className="text-center text-ink-4 text-[11px] py-3">{tr("加载中…")}</div> : <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
        ) : (
          <div className="flex flex-col">
            {providers.map(([name, p]) => (
              <div key={name} className="border-b border-border-soft py-2 text-[10px]">
                <div className="flex items-center gap-2">
                  <span className="w-20 font-mono font-medium text-ink">{name}</span>
                  <span className={`rounded px-1.5 py-0.5 text-[9px] ${STATUS_STYLE[p.status ?? ""] ?? "text-ink-3"}`}>
                    {p.status}
                  </span>
                  <span className="text-ink-4">mode · {p.mode}</span>
                  <span className="text-ink-4">auth · {p.auth}</span>
                  <span className="text-ink-4">cost · {p.cost}</span>
                  <div className="flex-1" />
                  <span className="text-[9px] text-ink-4">
                    SLA {p.freshness_sla_ms != null ? `${p.freshness_sla_ms}ms` : "—"}
                    {p.observed_lag_ms != null ? ` · lag ${p.observed_lag_ms}ms` : ""}
                  </span>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {Object.entries(p.capabilities ?? {}).map(([cap, on]) => {
                    const ent = p.entitlement_status_by_capability?.[cap];
                    return (
                      <span
                        key={cap}
                        className={`rounded px-1.5 py-0.5 text-[9px] ${
                          ent === "enabled"
                            ? "bg-pos/15 text-pos"
                            : ent === "unavailable"
                              ? "bg-surface-3 text-neg"
                              : on
                                ? "bg-surface-3 text-ink-2"
                                : "bg-surface-3 text-ink-4 line-through"
                        }`}
                        title={`entitlement: ${ent ?? "unknown"}`}
                      >
                        {tr(CAPABILITY_LABEL[cap] ?? cap)}
                      </span>
                    );
                  })}
                </div>
                {p.missing_credentials?.length ? (
                  <div className="mt-1 text-[9px] text-neg">
                    {tr("缺凭证 ·")} {p.missing_credentials.join(", ")}
                  </div>
                ) : null}
                {p.limitations?.length ? (
                  <div className="mt-1 text-[9px] text-ink-4">{tr("限制 ·")} {p.limitations.join("; ")}</div>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </V17Card>

      <V17Card title={tr("维度覆盖")}>
        {dimCoverage.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
        ) : (
          <div className="flex flex-col">
            {dimCoverage.map(([dim, provs]) => (
              <div key={dim} className="flex items-center gap-2 border-b border-border-soft py-1 text-[10px]">
                <span className="w-24 font-medium text-ink">{dim}</span>
                <span className="flex-1 text-ink-4">
                  {Object.entries(provs).map(([p, status]) => `${p}: ${status}`).join(" · ")}
                </span>
              </div>
            ))}
          </div>
        )}
      </V17Card>
    </div>
  );
}

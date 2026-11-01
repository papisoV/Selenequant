"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// 研究代理就绪 — /api/v1/us-radar/research-agents/readiness
// shape: { agents[], overall_readiness, available_count, missing_providers[], timestamp }
// agent: { agent_id, display_name, agent_type, status, required_provider_capabilities[],
//          enabled_capabilities[], missing_requirements[], last_run_at_ms, signal_contribution_count }

interface ResearchAgent {
  agent_id?: string;
  display_name?: string;
  agent_type?: string;
  status?: string;
  required_provider_capabilities?: string[];
  enabled_capabilities?: string[];
  missing_requirements?: string[];
  last_run_at_ms?: number | null;
  signal_contribution_count?: number;
}
interface AgentsReadinessData {
  agents?: ResearchAgent[];
  overall_readiness?: string;
  available_count?: number;
  missing_providers?: string[];
  timestamp?: number;
}

const STATUS_STYLE: Record<string, string> = {
  available: "bg-pos/20 text-pos",
  limited: "bg-gold/20 text-gold",
  disabled: "bg-surface-3 text-ink-4",
};

function fmtTime(ts?: number): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString("en-US", { hour12: false });
}

export function ResearchAgentsReadinessPage() {
  const tr = useUiText()

  const { useApi } = useApiPage({ distinguish: true });
  const { data, isLoading, mutate } = useApi<AgentsReadinessData | null>("/api/v1/us-radar/research-agents/readiness");
  const [refreshing, setRefreshing] = useState(false);
  const d = data ?? null;

  const agents = d?.agents ?? [];

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("研究代理就绪")}
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
          <StatBlock label={tr("代理")} value={String(agents.length)} />
          <StatBlock label={tr("可用")} value={String(d?.available_count ?? 0)} cls="text-pos" />
          <StatBlock label={tr("总体就绪")} value={d?.overall_readiness ?? "—"} cls="text-gold" />
          <StatBlock label={tr("缺数据源")} value={String(d?.missing_providers?.length ?? 0)} cls="text-neg" />
        </div>
      </V17Card>

      <V17Card title={tr("代理列表")}>
        {agents.length === 0 ? (
          isLoading ? <div className="text-center text-ink-4 text-[11px] py-3">{tr("加载中…")}</div> : <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
        ) : (
          <div className="flex flex-col">
            {agents.map((a) => (
              <div key={a.agent_id} className="border-b border-border-soft py-2 text-[10px]">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-ink">{a.display_name}</span>
                  <span className={`rounded px-1.5 py-0.5 text-[9px] ${STATUS_STYLE[a.status ?? ""] ?? "bg-surface-3 text-ink-3"}`}>
                    {a.status}
                  </span>
                  <span className="text-ink-4">{a.agent_type}</span>
                  <div className="flex-1" />
                  <span className="text-[9px] text-ink-4">
                    {a.last_run_at_ms ? tr("最后运行 {p0}", { p0: fmtTime(a.last_run_at_ms / 1000) }) : tr("未运行")}
                    {" · "}
                    {tr("贡献")} {a.signal_contribution_count ?? 0}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap gap-2 text-[9px] text-ink-4">
                  {a.enabled_capabilities?.length ? (
                    <span>{tr("已启用 ·")} <span className="text-ink-2">{a.enabled_capabilities.join(", ")}</span></span>
                  ) : null}
                  {a.missing_requirements?.length ? (
                    <span className="text-neg">{tr("缺失 ·")} {a.missing_requirements.join(", ")}</span>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </V17Card>

      {d?.missing_providers?.length ? (
        <V17Card title={tr("缺失数据源")}>
          <div className="flex flex-wrap gap-1.5">
            {d.missing_providers.map((p) => (
              <span key={p} className="rounded bg-surface-3 px-2 py-0.5 text-[9px] text-neg">{p}</span>
            ))}
          </div>
        </V17Card>
      ) : null}
    </div>
  );
}

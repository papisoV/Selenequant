"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";

// 新闻因子流水线 — /api/v1/intelligence/news/factor-pipeline
// shape: { stages[{name,label,status,throughput_per_min,lag_ms,queue_depth}], llm_available, timestamp }

interface Stage {
  name?: string;
  label?: string;
  status?: string;
  throughput_per_min?: number;
  lag_ms?: number | null;
  queue_depth?: number;
}
interface FactorPipelineData {
  stages?: Stage[];
  llm_available?: boolean;
  timestamp?: number;
}

function statusCls(s?: string): string {
  if (s === "active") return "text-success";
  if (s === "idle") return "text-gold";
  if (s === "error" || s === "blocked") return "text-destructive";
  return "text-ink-4";
}

function statusDot(s?: string): string {
  if (s === "active") return "bg-success";
  if (s === "idle") return "bg-gold";
  if (s === "error" || s === "blocked") return "bg-destructive";
  return "bg-ink-4";
}

export function IntelligenceNewsFactorPipelinePage() {
  const { useApi } = useApiPage();
  const { data, isLoading, mutate } = useApi<FactorPipelineData | null>(
    "/api/v1/intelligence/news/factor-pipeline",
  );
  const [refreshing, setRefreshing] = useState(false);
  const d = data ?? null;
  const stages = d?.stages ?? [];

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="新闻因子流水线"
        right={
          <button
            type="button"
            onClick={handleRefresh}
            disabled={refreshing || isLoading}
            aria-label="刷新"
            className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
          >
            <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
          </button>
        }
      >
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label="处理阶段" value={String(stages.length)} cls="text-ink" />
          <StatBlock label="活跃阶段" value={String(stages.filter((s) => s.status === "active").length)} cls="text-success" />
          <StatBlock label="LLM" value={d?.llm_available ? "可用" : "未配置"} cls={d?.llm_available ? "text-success" : "text-gold"} />
        </div>
      </V17Card>

      <V17Card title="处理阶段">
        {stages.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-3">
            {isLoading ? "加载中…" : "暂无数据"}
          </div>
        ) : (
          <div className="flex flex-col">
            {stages.map((s, i) => (
              <div key={s.name ?? i} className="border-b border-border-soft py-2 text-[10px]">
                <div className="flex items-center gap-2">
                  <span className={`h-2 w-2 rounded-full ${statusDot(s.status)}`} />
                  <span className="flex-1 text-ink-2">{s.label ?? s.name}</span>
                  <span className={`font-mono ${statusCls(s.status)}`}>{s.status}</span>
                  <span className="text-ink-4 font-mono num">{s.throughput_per_min ?? 0}/min</span>
                  <span className="text-ink-4 font-mono num w-12 text-right">
                    {s.lag_ms != null ? `${s.lag_ms}ms` : "—"}
                  </span>
                  <span className="text-ink-4 font-mono num w-6 text-right">q{s.queue_depth ?? 0}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </V17Card>
    </div>
  );
}

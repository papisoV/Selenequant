"use client";

import { useState } from "react";
import useSWR from "swr";
import { RefreshCw, Database } from "lucide-react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";

// 孤儿页 hyperliquid-addresses-pipeline → /api/v1/hyperliquid/addresses/pipeline
// 返回 PipelineStatus: candidate_source, screen_cache_entries, screen_cache_age_s,
//   detail_cache_entries, detail_inflight, active_prefetch_jobs, prefetch_jobs[], limits

interface PipelineStatusData {
  candidate_source?: string;
  screen_cache_entries?: number;
  screen_cache_age_s?: number;
  detail_cache_entries?: number;
  detail_inflight?: number;
  active_prefetch_jobs?: number;
  prefetch_jobs?: unknown[];
  limits?: Record<string, unknown>;
}

async function fetcher<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" } });
    if (!res.ok) return null;
    const body = await res.json();
    if (body && typeof body === "object" && "data" in body && "meta" in body) {
      if (body.error) return null;
      return (body.data ?? null) as T;
    }
    return body as T;
  } catch {
    return null;
  }
}

function fmtAge(ageS: number | undefined): string {
  if (ageS === undefined || ageS === null) return "—";
  if (ageS < 60) return `${ageS.toFixed(0)}s`;
  const m = Math.floor(ageS / 60);
  if (m < 60) return `${m}m ${(ageS % 60).toFixed(0)}s`;
  return `${(ageS / 3600).toFixed(1)}h`;
}

export function HyperliquidAddressesPipelinePage() {
  const [refreshing, setRefreshing] = useState(false);

  const { data, mutate } = useSWR<PipelineStatusData | null>(
    "hl-pipeline",
    () => fetcher<PipelineStatusData>("/api/v1/hyperliquid/addresses/pipeline"),
    { revalidateOnFocus: false, dedupingInterval: 5000 }
  );

  const d = data ?? null;

  const handleRefresh = () => {
    setRefreshing(true);
    mutate();
    setTimeout(() => setRefreshing(false), 600);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title="HL 数据管道状态"
        right={
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
            aria-label="refresh"
          >
            <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
          </button>
        }
      >
        {d == null ? (
          <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
        ) : (
          <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
            <StatBlock label="筛选缓存" value={String(d.screen_cache_entries ?? 0)} />
            <StatBlock label="详情缓存" value={String(d.detail_cache_entries ?? 0)} cls="text-gold" />
            <StatBlock label="缓存年龄" value={fmtAge(d.screen_cache_age_s)} />
          </div>
        )}
      </V17Card>
      <V17Card title="管道明细">
        <div className="flex flex-col gap-1 text-[10px]">
          <div className="flex justify-between">
            <span className="text-ink-3">候选源</span>
            <span className="text-ink-2 font-mono">{d?.candidate_source ?? "—"}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-ink-3">详情在途</span>
            <span className="text-ink-2 font-mono">{d?.detail_inflight ?? 0}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-ink-3">活跃预取任务</span>
            <span className="text-ink-2 font-mono">{d?.active_prefetch_jobs ?? 0}</span>
          </div>
          <div className="flex justify-between border-t border-border-soft pt-1">
            <span className="text-ink-3">预取队列</span>
            <span className="text-ink-2 font-mono">{d?.prefetch_jobs?.length ?? 0} 项</span>
          </div>
          <div className="flex items-center gap-1 text-[9px] text-ink-4 pt-1">
            <Database className="h-3 w-3" /> 屏幕/详情均为 5 分钟 TTL 内存缓存
          </div>
        </div>
      </V17Card>
    </div>
  );
}

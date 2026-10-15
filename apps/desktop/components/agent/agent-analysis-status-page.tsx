"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Search, RefreshCw, Loader2 } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";

// 分析状态 — /api/v1/agent/analysis/status/{analysis_id}
// shape: { analysis_id, status, created_at, result?, error?, completed_at? }
// 动态页:analysis_id 可从 URL ?analysis_id= 取,否则用户输入

interface AnalysisStatusData {
  analysis_id?: string;
  status?: string;
  created_at?: string;
  result?: unknown;
  error?: string;
  completed_at?: string;
}

function statusCls(s?: string): string {
  if (s === "completed") return "text-success";
  if (s === "failed") return "text-destructive";
  if (s === "running" || s === "queued" || s === "pending") return "text-gold";
  return "text-ink-3";
}

export function AgentAnalysisStatusPage() {
  const params = useSearchParams();
  const urlId = params.get("analysis_id") ?? undefined;
  const [analysisId, setAnalysisId] = useState<string | undefined>(urlId);
  const [inputId, setInputId] = useState<string>(urlId ?? "");

  const { useApi } = useApiPage();
  const { data, isLoading, mutate } = useApi<AnalysisStatusData | null>(
    analysisId ? `/api/v1/agent/analysis/status/${encodeURIComponent(analysisId)}` : null,
    { refreshInterval: analysisId ? 5000 : undefined },
  );
  const [refreshing, setRefreshing] = useState(false);

  // URL 参数变化时同步
  useEffect(() => {
    if (urlId) {
      setAnalysisId(urlId);
      setInputId(urlId);
    }
  }, [urlId]);

  const d = data ?? null;

  const handleSubmit = () => {
    const id = inputId.trim();
    if (!id) return;
    setAnalysisId(id);
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  const resultText =
    d?.result != null
      ? typeof d.result === "string"
        ? d.result
        : JSON.stringify(d.result, null, 2)
      : null;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="分析状态查询">
        <div className="flex items-center gap-2">
          <input
            value={inputId}
            onChange={(e) => setInputId(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleSubmit(); }}
            placeholder="输入 analysis_id（如 a1b2c3d4）"
            className="flex-1 rounded border border-border-soft bg-surface-2 px-2 py-1.5 text-[10px] text-ink placeholder:text-ink-4 focus:outline-none focus:border-gold/40"
          />
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!inputId.trim()}
            className="flex items-center gap-1 rounded bg-gold/15 px-2 py-1.5 text-[10px] text-gold hover:bg-gold/25 disabled:opacity-40"
          >
            <Search className="h-3 w-3" /> 查询
          </button>
        </div>
        {analysisId && (
          <div className="mt-3 grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
            <StatBlock label="分析 ID" value={d?.analysis_id ?? "—"} cls="text-ink" />
            <StatBlock label="状态" value={d?.status ?? "—"} cls={statusCls(d?.status)} />
            <StatBlock label="创建时间" value={d?.created_at ? new Date(d.created_at).toLocaleString("zh-CN") : "—"} cls="text-ink-4" />
            <StatBlock label="完成时间" value={d?.completed_at ? new Date(d.completed_at).toLocaleString("zh-CN") : "—"} cls="text-ink-4" />
          </div>
        )}
      </V17Card>

      {analysisId && (
        <V17Card
          title="分析结果"
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
          {isLoading && !d ? (
            <div className="flex items-center justify-center gap-2 py-6 text-ink-4 text-[10px]">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> 查询中…
            </div>
          ) : d?.error ? (
            <div className="rounded bg-destructive/10 p-2 text-[10px] text-destructive whitespace-pre-wrap">{d.error}</div>
          ) : resultText ? (
            <pre className="max-h-96 overflow-auto rounded bg-surface-2 p-2 text-[10px] text-ink-2 whitespace-pre-wrap">
              {resultText}
            </pre>
          ) : (
            <div className="text-center text-ink-4 text-[11px] py-3">
              {d ? "无结果" : "未查询或找不到该分析任务"}
            </div>
          )}
        </V17Card>
      )}
    </div>
  );
}

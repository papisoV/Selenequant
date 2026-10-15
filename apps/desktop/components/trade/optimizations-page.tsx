"use client";

import { useState } from "react";
import useSWR from "swr";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import {
  applyOptimization,
  exportOptimization,
  deleteOptimization,
  suggestOptimization,
  type OptimizationRun,
  type ParamSuggestion,
} from "@/lib/api/optimizations";
import { toastSuccess, toastError } from "@/lib/toast";
import { OptimizationsAutoPanel } from "./optimizations-auto-panel";

async function fetcher(): Promise<OptimizationRun[]> {
  const res = await fetch("/api/v1/optimizations");
  if (!res.ok) return [];
  const body = await res.json();
  if (body && typeof body === "object" && "data" in body) return body.data ?? [];
  return Array.isArray(body) ? body : [];
}

export function OptimizationsPage() {
  const { data: rows, mutate } = useSWR<OptimizationRun[]>(
    "/api/v1/optimizations",
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 5000 }
  );
  const list = rows ?? [];
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [suggestStrategy, setSuggestStrategy] = useState<string>(list[0]?.strategy_id || "bb_v3");
  const [suggestion, setSuggestion] = useState<ParamSuggestion | null>(null);
  const [suggestBusy, setSuggestBusy] = useState(false);

  async function runAction(id: string, fn: () => Promise<unknown>, successMsg: string) {
    setBusyId(id);
    try {
      await fn();
      toastSuccess(successMsg);
      await mutate();
    } catch (e: unknown) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function runExport(id: string) {
    setBusyId(id);
    try {
      const text = await exportOptimization(id);
      const blob = new Blob([text], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${id}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function runDelete(id: string) {
    await runAction(id, () => deleteOptimization(id), `已删除 ${id}`);
    setConfirmDelete(null);
  }

  async function runSuggest() {
    const sid = suggestStrategy.trim();
    if (!sid) return;
    setSuggestBusy(true);
    try {
      const res = await suggestOptimization(sid);
      setSuggestion(res);
      toastSuccess(res.source === "llm" ? `LLM 建议已生成 (${sid})` : `默认建议已生成 (${sid})`);
    } catch (e: unknown) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setSuggestBusy(false);
    }
  }

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="优化概览">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label="总优化" value={String(list.length)} />
          <StatBlock label="完成" value={String(list.filter((r) => r.status === "completed").length)} cls="text-pos" />
          <StatBlock label="运行中" value={String(list.filter((r) => r.status === "running").length)} cls="text-gold" />
          <StatBlock label="队列" value={String(list.filter((r) => r.status === "queued").length)} />
        </div>
      </V17Card>
      <V17Card title="优化列表">
        <div className="flex flex-col">
          {list.length === 0 && (
            <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
          )}
          {list.map((r, i) => {
            const id = String(r.id ?? `row-${i}`);
            const cls = r.status === "completed" ? "text-pos" : r.status === "running" ? "text-gold" : "text-ink-3";
            const canApply = r.status === "completed";
            const isBusy = busyId === id;
            const isConfirm = confirmDelete === id;
            return (
              <div key={id} className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft">
                <span className="w-16 text-ink-4 font-mono">{r.id}</span>
                <span className="flex-1 text-ink">{r.strategy}</span>
                <span className="w-12 text-right text-ink-3 font-mono num">{r.trials}</span>
                <span className="w-14 text-right font-mono num text-gold">{(r.best_sharpe ?? 0).toFixed(2)}</span>
                <span className={`w-14 text-right ${cls}`}>{r.status}</span>
                <span className="flex gap-1 w-40 justify-end">
                  {canApply && (
                    <button
                      disabled={isBusy}
                      onClick={() => runAction(id, () => applyOptimization(id), `已应用 ${id}`)}
                      className="rounded bg-emerald-500/15 text-emerald-400 px-1.5 py-0.5 text-[9px] hover:bg-emerald-500/25 disabled:opacity-50"
                    >
                      {isBusy ? "…" : "应用"}
                    </button>
                  )}
                  <button
                    disabled={isBusy}
                    onClick={() => runExport(id)}
                    className="rounded bg-surface-3 text-ink-3 px-1.5 py-0.5 text-[9px] hover:bg-surface-4 disabled:opacity-50"
                  >
                    {isBusy ? "…" : "导出"}
                  </button>
                  {isConfirm ? (
                    <>
                      <button
                        disabled={isBusy}
                        onClick={() => runDelete(id)}
                        className="rounded bg-rose-500/25 text-rose-400 px-1.5 py-0.5 text-[9px] disabled:opacity-50"
                      >
                        确认
                      </button>
                      <button
                        onClick={() => setConfirmDelete(null)}
                        className="rounded bg-surface-3 text-ink-3 px-1.5 py-0.5 text-[9px]"
                      >
                        取消
                      </button>
                    </>
                  ) : (
                    <button
                      disabled={isBusy}
                      onClick={() => setConfirmDelete(id)}
                      className="rounded bg-surface-3 text-ink-3 px-1.5 py-0.5 text-[9px] hover:bg-rose-500/15 hover:text-rose-400 disabled:opacity-50"
                    >
                      删除
                    </button>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      </V17Card>
      <V17Card title="AI 建议参数">
        <div className="flex items-center gap-2">
          <input
            value={suggestStrategy}
            onChange={(e) => setSuggestStrategy(e.target.value)}
            placeholder="策略 ID，如 bb_v3"
            className="rounded bg-surface-2 border border-border-soft px-2 py-1 text-[11px] text-ink w-40"
          />
          <button
            disabled={suggestBusy}
            onClick={runSuggest}
            className="rounded bg-emerald-500/15 text-emerald-400 px-2 py-1 text-[10px] hover:bg-emerald-500/25 disabled:opacity-50"
          >
            {suggestBusy ? "…" : "生成建议"}
          </button>
          {suggestion && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${suggestion.source === "llm" ? "bg-emerald-500/15 text-emerald-400" : "bg-surface-3 text-ink-3"}`}>
              {suggestion.source === "llm" ? "LLM" : "默认"}
            </span>
          )}
        </div>
        {suggestion && (
          <div className="mt-2 space-y-1">
            {Object.entries(suggestion.suggestions).map(([param, values]) => (
              <div key={param} className="flex items-center gap-2 text-[10px] py-0.5 border-b border-border-soft">
                <span className="w-24 text-ink-4 font-mono">{param}</span>
                <div className="flex flex-wrap gap-1 flex-1">
                  {values.map((v, i) => (
                    <span key={i} className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-ink-2">{String(v)}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </V17Card>
      <OptimizationsAutoPanel />
    </div>
  );
}

"use client";

import { useState } from "react";
import useSWR from "swr";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import {
  createAutoOptimization, getAutoOptimization, cancelAutoOptimization,
  suggestParams, getDistributedStatus,
  type AutoOptimization,
} from "@/lib/api/optimizations-ext";
import { toastSuccess, toastError } from "@/lib/toast";
import { Loader2, Play, Square, Sparkles, Network } from "lucide-react";
import { useUiText } from "@/components/i18n/use-ui-text"

interface AutoRow extends AutoOptimization {}

async function listAutoFetcher(): Promise<AutoRow[]> {
  // 后端无 list 端点,只有 POST 创建 + GET /{id} + DELETE /{id}。
  // 前端维护本地列表(创建后追加,取消后移除)。session 级状态。
  return [];
}

export function OptimizationsAutoPanel() {
  const tr = useUiText()

  const [rows, setRows] = useState<AutoRow[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [form, setForm] = useState({
    strategy_id: "",
    interval_minutes: 60,
    max_runs: 10,
    param_space_json: '{"fast_period": [5, 20], "slow_period": [20, 60]}',
  });
  const [suggestResult, setSuggestResult] = useState<Record<string, unknown> | null>(null);
  const [suggestLoading, setSuggestLoading] = useState(false);

  // 分布式状态(只读)
  const { data: distStatus } = useSWR("/api/v1/optimizations/distributed/status", getDistributedStatus, {
    refreshInterval: 30000,
  });

  async function doCreate() {
    if (!form.strategy_id) {
      toastError(tr("strategy_id 必填"));
      return;
    }
    let paramSpace: Record<string, unknown> = {};
    try {
      paramSpace = JSON.parse(form.param_space_json || "{}");
    } catch {
      toastError(tr("param_space JSON 解析失败"));
      return;
    }
    setBusy("create");
    try {
      const r = await createAutoOptimization({
        strategy_id: form.strategy_id,
        param_space: paramSpace,
        interval_minutes: form.interval_minutes,
        max_runs: form.max_runs,
      });
      const full = await getAutoOptimization(r.id);
      setRows((prev) => [full, ...prev]);
      toastSuccess(tr("已创建自动优化 {id}", { id: r.id }));
      setCreateOpen(false);
    } catch (e: unknown) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function doCancel(id: string) {
    setBusy(id);
    try {
      await cancelAutoOptimization(id);
      setRows((prev) => prev.filter((r) => r.id !== id));
      toastSuccess(tr("已取消 {id}", { id: id }));
    } catch (e: unknown) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function doSuggest() {
    if (!form.strategy_id) {
      toastError(tr("先填 strategy_id"));
      return;
    }
    setSuggestLoading(true);
    try {
      const r = await suggestParams({ strategy_id: form.strategy_id });
      setSuggestResult(r);
    } catch (e: unknown) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setSuggestLoading(false);
    }
  }

  return (
    <V17Card title={tr("自动优化 + LLM 建议")}>
      <div className="grid grid-cols-3 gap-2 mb-2">
        <StatBlock label={tr("自动任务")} value={rows.length} cls="text-gold" />
        <StatBlock label={tr("运行中")} value={rows.filter((r) => r.status === "running").length} cls="text-pos" />
        <StatBlock
          label={tr("分布式")}
          value={distStatus ? "ON" : "—"}
          cls={distStatus ? "text-sky-400" : "text-ink-4"}
        />
      </div>

      <div className="flex gap-1 mb-2">
        <button
          onClick={() => setCreateOpen(true)}
          className="rounded bg-sky-500/15 text-sky-400 px-2 py-1 text-[10px] hover:bg-sky-500/25 flex items-center gap-1"
        >
          <Play className="h-3 w-3" />{tr("新建自动优化")}
        </button>
      </div>

      <div className="flex flex-col">
        {rows.length === 0 && (
          <div className="text-center text-ink-4 text-[11px] py-2">{tr("暂无自动优化任务")}</div>
        )}
        {rows.map((r) => (
          <div
            key={r.id}
            className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft"
          >
            <span className="w-24 text-ink-4 font-mono truncate" title={r.id}>{r.id}</span>
            <span className="flex-1 text-gold font-mono truncate">{r.strategy_id}</span>
            <span className={`w-14 text-right ${r.status === "running" ? "text-pos" : "text-ink-3"}`}>
              {r.status}
            </span>
            <span className="w-14 text-right text-ink-3 font-mono">{r.runs_completed ?? 0}/{r.config?.max_runs ?? "?"}</span>
            <button
              disabled={busy === r.id}
              onClick={() => doCancel(r.id)}
              className="rounded bg-rose-500/15 text-rose-400 px-1.5 py-0.5 text-[9px] hover:bg-rose-500/25 disabled:opacity-50 flex items-center gap-0.5"
            >
              {busy === r.id ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : <Square className="h-2.5 w-2.5" />}
              {tr("取消")}
            </button>
          </div>
        ))}
      </div>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Play className="h-4 w-4 text-sky-400" />
              {tr("新建自动优化")}
            </DialogTitle>
            <DialogDescription>
              {tr("定时跑优化,自动探索参数空间。后端 community edition 记录但不实际改策略。")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 text-[11px]">
            <div>
              <div className="text-ink-4 mb-0.5">Strategy ID</div>
              <input
                value={form.strategy_id}
                onChange={(e) => setForm({ ...form, strategy_id: e.target.value })}
                placeholder="ema_cross"
                className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 font-mono text-ink"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <div className="text-ink-4 mb-0.5">{tr("间隔(分钟)")}</div>
                <input
                  type="number"
                  min={1}
                  value={form.interval_minutes}
                  onChange={(e) => setForm({ ...form, interval_minutes: parseInt(e.target.value) || 60 })}
                  className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 font-mono text-ink"
                />
              </div>
              <div>
                <div className="text-ink-4 mb-0.5">{tr("最大运行数")}</div>
                <input
                  type="number"
                  min={1}
                  value={form.max_runs}
                  onChange={(e) => setForm({ ...form, max_runs: parseInt(e.target.value) || 10 })}
                  className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 font-mono text-ink"
                />
              </div>
            </div>
            <div>
              <div className="text-ink-4 mb-0.5">{tr("参数空间 (JSON)")}</div>
              <textarea
                value={form.param_space_json}
                onChange={(e) => setForm({ ...form, param_space_json: e.target.value })}
                rows={4}
                className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 font-mono text-[10px] text-ink"
              />
            </div>

            <div className="border-t border-border-soft pt-2 mt-2">
              <div className="flex items-center justify-between mb-1">
                <div className="text-ink-3 flex items-center gap-1">
                  <Sparkles className="h-3 w-3 text-gold" />
                  {tr("LLM 参数建议")}
                </div>
                <button
                  onClick={doSuggest}
                  disabled={suggestLoading || !form.strategy_id}
                  className="rounded bg-gold/15 text-gold px-1.5 py-0.5 text-[9px] hover:bg-gold/25 disabled:opacity-50 flex items-center gap-0.5"
                >
                  {suggestLoading && <Loader2 className="h-2.5 w-2.5 animate-spin" />}
                  {tr("生成建议")}
                </button>
              </div>
              {suggestResult && (
                <pre className="text-[9px] text-ink-3 font-mono bg-surface-2 rounded p-1.5 max-h-32 overflow-auto">
                  {JSON.stringify(suggestResult, null, 2)}
                </pre>
              )}
            </div>

            <div className="border-t border-border-soft pt-2 mt-2 flex items-center gap-1 text-[10px] text-ink-4">
              <Network className="h-3 w-3" />
              {tr("分布式状态:")} {distStatus ? JSON.stringify(distStatus).slice(0, 80) : tr("未连接")}
            </div>

            <div className="flex justify-end gap-2 pt-1">
              <button
                onClick={() => setCreateOpen(false)}
                disabled={busy === "create"}
                className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[10px] text-ink-3 hover:bg-surface-4"
              >
                {tr("取消")}
              </button>
              <button
                onClick={doCreate}
                disabled={busy === "create"}
                className="rounded border border-sky-400/40 bg-sky-500/15 px-2 py-1 text-[10px] text-sky-400 hover:bg-sky-500/25 flex items-center gap-1"
              >
                {busy === "create" && <Loader2 className="h-3 w-3 animate-spin" />}
                {tr("创建")}
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </V17Card>
  );
}

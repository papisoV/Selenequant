"use client";

// 策略工作台 — slug "strategies-strategy-id" 的实体化详情页（US-0 挂载）。
// 此前该 slug 只有「此页面需从列表进入」占位，components/strategy/ 22 组件 100% 孤儿。
// 本组件挂载 ParamPanel / BacktestResultsTabs / RunnerMonitorPanel / VersionTimeline；
// AiSidebar（1170 行）走 next/dynamic 懒加载。
// 回测:POST /backtests 返回 202+task_id（异步）,轮询 GET /backtests/{task_id} 至
// status=completed/failed。runner slot 数据来自 GET /runner/status（完整 SlotStatus 超集）,
// 信号历史用 slot.recent_signals（REST）+ useStrategyStore.signals（WS 实时）双源。

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useSearchParams, useRouter } from "next/navigation";
import { useApi } from "@/lib/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toastSuccess, toastError } from "@/lib/toast";
import { useStrategyStore } from "@/lib/store/strategy";
import { apiPut } from "@/lib/api-client";
import {
  runBacktest,
  updateStrategy,
  type BacktestData,
} from "./api-bridge";
import type { StrategyData } from "./types";
import { ParamPanel } from "./param-panel";
import { BacktestResultsTabs } from "./backtest-results-tabs";
import { RunnerMonitorPanel } from "./runner-monitor";
import { VersionTimeline } from "./version-timeline";

// AiSidebar 1170 行 + markdown 依赖链,懒加载防拖累首屏
const AiSidebar = dynamic(
  () => import("./ai-sidebar").then((m) => ({ default: m.AiSidebar })),
  { ssr: false, loading: () => <Skeleton className="h-64 w-full" /> },
);

// GET /runner/status 的 deployments[] 元素 = get_slot_status() 超集,
// 覆盖 RunnerMonitorPanel 的 SlotStatus({symbol,interval,bar_count,signals_emitted,orders_placed,position})
interface RunnerSlot {
  deployment_id: string;
  strategy_id: string;
  symbol?: string;
  interval?: string;
  bar_count?: number;
  signals_emitted?: number;
  orders_placed?: number;
  position?: string;
  /** watchdog 对账修正计数(透明性:修正发生了就要让用户看得见) */
  position_corrections?: number;
  last_correction_ts?: number;
  last_correction_delta?: number;
  recent_signals?: Array<{
    ts: number;
    side: string;
    reason: string;
    strength?: number;
    price?: number;
    symbol?: string;
  }>;
}

// 回测轮询:202+task_id → 2s 间隔 GET,progress 消费(US-105)+ 停滞超时;
// completed→result,failed→error。停滞判定:progress 值或 status 无进展超过
// 20s 判超时(大回测合法慢跑不会被 60s 硬顶误杀);无 progress 字段的旧
// 后端响应按"永远停滞"处理但沿用 60s 兜底。
function useBacktestPoll() {
  const [status, setStatus] = useState<"idle" | "running" | "done" | "failed">("idle");
  const [result, setResult] = useState<BacktestData | null>(null);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const poll = useCallback(async (taskId: string) => {
    const deadline = Date.now() + 60_000;
    const stallLimit = 20_000;
    let lastProgress = -1;
    let lastMoveAt = Date.now();
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 2000));
      try {
        const res = await fetch(`/api/v1/backtests/${taskId}`);
        if (!res.ok) continue;
        const body = await res.json();
        const d = body?.data ?? body;
        if (d?.status === "completed") {
          setResult(d as BacktestData);
          setStatus("done");
          return;
        }
        if (d?.status === "failed") {
          setErrMsg(String(d.error ?? "回测失败"));
          setStatus("failed");
          return;
        }
        if (typeof d?.progress === "number") {
          setProgress(d.progress);
          if (d.progress !== lastProgress) {
            lastProgress = d.progress;
            lastMoveAt = Date.now();
          }
        }
        if (Date.now() - lastMoveAt > stallLimit) {
          setErrMsg("回测进度停滞超过 20s，已停止等待");
          setStatus("failed");
          return;
        }
      } catch {
        // 网络抖动继续轮询
      }
    }
    setErrMsg("回测超时(60s)");
    setStatus("failed");
  }, []);

  const start = useCallback(
    async (body: Record<string, unknown>) => {
      setStatus("running");
      setResult(null);
      setErrMsg(null);
      setProgress(null);
      try {
        const r = await runBacktest(body);
        const taskId = (r.data as { task_id?: string } | null)?.task_id;
        if (!taskId) {
          setErrMsg(r.error ?? "提交失败");
          setStatus("failed");
          return;
        }
        void poll(taskId);
      } catch (e) {
        setErrMsg(e instanceof Error ? e.message : String(e));
        setStatus("failed");
      }
    },
    [poll],
  );

  return { status, result, errMsg, progress, start };
}

// 回测触发面板:标的+周期+区间,默认取策略自带 symbol/interval
function BacktestTrigger({
  strategy,
  poll,
}: {
  strategy: StrategyData;
  poll: ReturnType<typeof useBacktestPoll>;
}) {
  const [symbol, setSymbol] = useState(strategy.symbols?.[0] ?? strategy.symbol ?? "");
  const [interval_, setInterval_] = useState(strategy.interval ?? "1d");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const running = poll.status === "running";

  const run = () => {
    if (!symbol.trim()) {
      toastError("请填写标的");
      return;
    }
    void poll.start({
      strategy_id: strategy.id,
      symbol: symbol.trim().toUpperCase(),
      interval: interval_,
      start_date: startDate || undefined,
      end_date: endDate || undefined,
    });
  };

  return (
    <div className="flex flex-col gap-2 text-xs">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-muted-foreground">标的</span>
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            placeholder="BTC/USDT"
            className="h-8 w-28 rounded border border-border bg-muted/30 px-2 font-mono text-xs"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-muted-foreground">周期</span>
          <select
            value={interval_}
            onChange={(e) => setInterval_(e.target.value)}
            className="h-8 rounded border border-border bg-muted/30 px-2 font-mono text-xs"
          >
            {["1m", "5m", "15m", "30m", "1h", "4h", "1d"].map((tf) => (
              <option key={tf} value={tf}>
                {tf}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-muted-foreground">开始</span>
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="h-8 rounded border border-border bg-muted/30 px-2 text-xs"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-muted-foreground">结束</span>
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="h-8 rounded border border-border bg-muted/30 px-2 text-xs"
          />
        </label>
        <Button size="sm" onClick={run} disabled={running}>
          {running
            ? poll.progress !== null
              ? `回测中 ${poll.progress}%`
              : "回测中…"
            : "运行回测"}
        </Button>
      </div>
      {poll.status === "failed" && (
        <div className="text-[10px] text-rose-400">{poll.errMsg}</div>
      )}
    </div>
  );
}

export function StrategyWorkbench({ id }: { id: string }) {
  const router = useRouter();
  // 策略详情 + runner slots(15s 轮询) + 该策略历史回测列表
  const { data: strategy, mutate: mutateStrategy, isLoading } = useApi<StrategyData>(
    `/api/v1/strategies/${id}`,
  );
  const { data: runnerStatus } = useApi<{ running: boolean; deployments: RunnerSlot[] }>(
    "/api/v1/strategies/runner/status",
    { refreshInterval: 15_000 },
  );
  const { data: historyList } = useApi<Array<{ id: string }>>(
    `/api/v1/backtests?strategy_id=${encodeURIComponent(id)}&limit=1`,
  );
  const { data: latestHistory } = useApi<BacktestData | null>(
    historyList?.[0]?.id ? `/api/v1/backtests/${historyList[0].id}` : null,
  );

  // WS 信号流(useStrategyWs 顶层常驻) — 只读 store,按策略过滤
  const wsConnected = useStrategyStore((s) => s.connected);
  const wsSignals = useStrategyStore((s) => s.signals);

  const poll = useBacktestPoll();

  const slot = useMemo(
    () => runnerStatus?.deployments?.find((d) => d.strategy_id === id) ?? null,
    [runnerStatus, id],
  );

  // 回测展示:本次会话新触发 > 历史最近一条
  const btResult = poll.result ?? latestHistory ?? null;

  // ParamPanel 编辑缓冲:onChange 只进本地 draft,「保存更改」才 PUT(auto-version+1)
  const [draft, setDraft] = useState<Partial<StrategyData> | null>(null);
  const [saving, setSaving] = useState(false);
  const dirty = draft !== null;
  const merged: StrategyData | null = strategy
    ? { ...strategy, ...draft, id, name: strategy.name ?? id }
    : null;

  const save = useCallback(async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const updated = await updateStrategy(id, draft);
      if (updated.data) {
        toastSuccess(`已保存(v${(updated.data as StrategyData).version ?? "?"})`);
        setDraft(null);
        void mutateStrategy();
      } else {
        toastError(updated.error ?? "保存失败");
      }
    } catch (e) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [draft, id, mutateStrategy]);

  const [aiOpen, setAiOpen] = useState(false);
  // 版本保存/回滚后让 VersionTimeline 重拉
  const [versionTick, setVersionTick] = useState(0);

  if (isLoading) {
    return (
      <div className="space-y-3 p-4">
        <Skeleton className="h-8 w-1/3" />
        <div className="grid gap-3 md:grid-cols-2">
          <Skeleton className="h-48" />
          <Skeleton className="h-48" />
        </div>
      </div>
    );
  }

  if (!strategy) {
    return (
      <div className="flex h-full items-center justify-center gap-2 p-6 text-xs text-ink-3">
        未找到策略 <span className="num font-mono">{id}</span>
        <Button size="sm" variant="outline" onClick={() => router.back()}>
          返回
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      {/* 头部:名称+徽章+操作 */}
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">{strategy.name ?? id}</h2>
        <Badge variant="outline" className="text-[10px] font-mono">
          {strategy.type ?? "—"}
        </Badge>
        <Badge variant="outline" className="text-[10px] font-mono">
          {strategy.symbols?.[0] ?? strategy.symbol ?? "—"} · {strategy.interval ?? "—"}
        </Badge>
        {strategy.version != null && (
          <Badge variant="outline" className="text-[10px] font-mono">
            v{strategy.version}
          </Badge>
        )}
        {slot && (
          <Badge className="bg-emerald-500/15 text-[10px] text-emerald-400">
            运行中
          </Badge>
        )}
        <span className="ml-auto flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setAiOpen(true)}>
            AI 助手
          </Button>
          <Button size="sm" onClick={save} disabled={!dirty || saving}>
            {saving ? "保存中…" : dirty ? "保存更改" : "已保存"}
          </Button>
        </span>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        {/* 左列:参数编辑 + 版本历史 */}
        <div className="flex flex-col gap-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-xs">参数调优</CardTitle>
            </CardHeader>
            <CardContent>
              {merged && (
                <ParamPanel
                  strategy={merged}
                  onChange={(patch) => setDraft((d) => ({ ...(d ?? {}), ...patch }))}
                />
              )}
            </CardContent>
          </Card>
          <VersionTimeline
            strategyId={id}
            currentVersion={strategy.version ?? 0}
            refreshKey={versionTick}
            onRevert={async (config) => {
              try {
                await updateStrategy(id, config);
                toastSuccess("已回滚到该版本");
                setVersionTick((v) => v + 1);
                void mutateStrategy();
              } catch (e) {
                toastError(e instanceof Error ? e.message : String(e));
              }
            }}
          />
        </div>

        {/* 右列:运行监控 + 回测触发 */}
        <div className="flex flex-col gap-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-xs">运行监控</CardTitle>
            </CardHeader>
            <CardContent>
              <RunnerMonitorPanel
                strategyId={id}
                accountId={null}
                connected={wsConnected}
                signals={
                  slot?.recent_signals?.map((s) => ({
                    ts: s.ts,
                    strategy_id: id,
                    signal: { side: s.side, reason: s.reason, strength: s.strength },
                    price: s.price,
                    symbol: s.symbol,
                  })) ?? []
                }
                slotStatus={
                  slot
                    ? {
                        symbol: slot.symbol ?? "—",
                        interval: slot.interval ?? "—",
                        bar_count: slot.bar_count ?? 0,
                        signals_emitted: slot.signals_emitted ?? 0,
                        orders_placed: slot.orders_placed ?? 0,
                        position: slot.position ?? "—",
                        position_corrections: slot.position_corrections,
                        last_correction_ts: slot.last_correction_ts,
                        last_correction_delta: slot.last_correction_delta,
                      }
                    : null
                }
              />
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-xs">回测</CardTitle>
            </CardHeader>
            <CardContent>
              <BacktestTrigger strategy={merged ?? { id, name: id } as StrategyData} poll={poll} />
            </CardContent>
          </Card>
        </div>
      </div>

      {/* 回测结果 tabs 通铺 */}
      {btResult && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs">回测结果</CardTitle>
          </CardHeader>
          <CardContent>
            <BacktestResultsTabs result={btResult} strategy={merged} />
          </CardContent>
        </Card>
      )}

      {/* AI 助手弹窗 */}
      <Dialog open={aiOpen} onOpenChange={setAiOpen}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>AI 助手 — {strategy.name ?? id}</DialogTitle>
          </DialogHeader>
          <AiSidebar
            strategyId={id}
            strategyName={strategy.name ?? id}
            strategy={merged}
            symbol={strategy.symbols?.[0] ?? strategy.symbol}
            timeframe={strategy.interval}
            onStrategyChange={(patch) => setDraft((d) => ({ ...(d ?? {}), ...patch }))}
            onStrategyRefresh={() => void mutateStrategy()}
            onCollapse={() => setAiOpen(false)}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}

// 页面入口:?id= 读取照 strategy-cockpit 正典(useSearchParams + Suspense;
// PageRenderer 挂载点不在 Next 路由树内,但 Suspense 包裹仍是 CSR bailout 安全写法)
export function StrategyWorkbenchPage() {
  return (
    <Suspense fallback={<div className="h-full" />}>
      <StrategyInner />
    </Suspense>
  );
}

/** 无 id 时的选单空态 —— 列出可进入的策略，而不是让用户自己拼 URL。 */
function WorkbenchPicker() {
  const { data } = useApi<{ strategies?: { id?: string; name?: string }[] } | null>(
    "/api/v1/strategies",
  );
  const list = (data?.strategies ?? []).filter((s) => s.id);
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6">
      <p className="text-[12px] text-ink-2">选择要打开的策略</p>
      {list.length === 0 ? (
        <p className="text-[9px] text-ink-4">
          暂无策略。可到「策略工厂」（⌘3）从模板库部署一个。
        </p>
      ) : (
        <div className="flex max-h-[50vh] w-full max-w-sm flex-col gap-1 overflow-y-auto">
          {list.map((s) => (
            <a
              key={s.id}
              href={`/research/strategies-strategy-id?id=${encodeURIComponent(s.id!)}`}
              className="rounded border border-border-soft px-2 py-1 text-[10px] text-ink-2 hover:bg-muted"
            >
              {s.name ?? s.id}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

function StrategyInner() {
  const params = useSearchParams();
  const id = params.get("id");
  if (!id) return <WorkbenchPicker />;
  return <StrategyWorkbench id={id} />;
}

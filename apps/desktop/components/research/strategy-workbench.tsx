"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Check, Loader2, Play, Save, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { AiSidebar } from "@/components/strategy/ai-sidebar";
import { ParamPanel } from "@/components/strategy/param-panel";
import { BacktestConfigBar, type BacktestConfig } from "@/components/strategy/backtest-config-bar";
import { BacktestResultsTabs } from "@/components/strategy/backtest-results-tabs";
import { VersionTimeline } from "@/components/strategy/version-timeline";
import { MonitorTab } from "@/components/strategy/monitor-tab";
import type { BacktestData, StrategyData } from "@/components/strategy/types";

// 军规2/3:头部只放可计算真值;军规7:灵魂问句;军规5:空态教学。
// 数据链:裸 fetch 走 Next 代理(api-client 12s 超时/去重对慢代理致命,见 US-0 坑)。
export function StrategyWorkbench({
  id,
  onExit,
}: {
  id: string;
  onExit: () => void;
}) {
  const [strategy, setStrategy] = useState<StrategyData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  const [btConfig, setBtConfig] = useState<BacktestConfig | null>(null);
  const [btTaskId, setBtTaskId] = useState<string | null>(null);
  const [btStatus, setBtStatus] = useState<string>("");
  const [btResult, setBtResult] = useState<BacktestData | null>(null);
  const [btError, setBtError] = useState<string | null>(null);
  const [versionKey, setVersionKey] = useState(0);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // --- 加载策略(裸 fetch + 重试;每次尝试独立 30s,全局共享超时会在慢代理下连坐) ---
  useEffect(() => {
    let alive = true;
    let ctl: AbortController | null = null;
    const load = async (attempt: number): Promise<void> => {
      if (!alive) return;
      ctl = new AbortController();
      const kill = setTimeout(() => ctl?.abort(), 30_000);
      try {
        const r = await fetch(`/api/v1/strategies/${id}`, { signal: ctl.signal });
        const body = (await r.json()) as { data?: StrategyData | null };
        if (!alive) return;
        if (body?.data) {
          setStrategy(body.data);
          setLoadError(null);
          return;
        }
        throw new Error("策略详情为空");
      } catch (e: unknown) {
        if (!alive) return;
        if (attempt < 4) {
          await new Promise((res) => setTimeout(res, 1500 * (attempt + 1)));
          return load(attempt + 1);
        }
        setLoadError(e instanceof Error ? e.message : String(e));
      } finally {
        clearTimeout(kill);
      }
    };
    void load(0);
    return () => {
      alive = false;
      ctl?.abort();
    };
  }, [id]);

  // --- 保存:真 PUT /strategies/{id} partial update ---
  const handleSave = useCallback(async () => {
    if (!strategy) return;
    setSaving(true);
    setSaveMsg(null);
    try {
      const r = await fetch(`/api/v1/strategies/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: strategy.name,
          description: strategy.description,
          symbols: strategy.symbols,
          interval: strategy.interval,
          parameters: strategy.parameters,
          entry_rules: strategy.entry_rules,
          exit_rules: strategy.exit_rules,
          risk_params: strategy.risk_params,
          tags: strategy.tags,
          version_note: "工作台保存",
        }),
      });
      const body = (await r.json()) as { data?: StrategyData | null; error?: { message?: string } | null };
      if (!r.ok || body.error) throw new Error(body.error?.message ?? `HTTP ${r.status}`);
      if (body.data) {
        setStrategy((prev) => (prev ? { ...prev, ...body.data } : body.data ?? prev));
      }
      setDirty(false);
      setSaveMsg("已保存");
      setVersionKey((k) => k + 1);
    } catch (e: unknown) {
      setSaveMsg(`保存失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSaving(false);
      setTimeout(() => setSaveMsg(null), 4000);
    }
  }, [id, strategy]);

  // --- 跑回测:POST 202 → task_id → GET 轮询 ---
  const runBacktest = useCallback(async () => {
    if (!strategy || !btConfig) return;
    setBtError(null);
    setBtResult(null);
    try {
      const r = await fetch("/api/v1/backtests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategy_id: id,
          symbol: btConfig.symbol,
          interval: btConfig.period === "daily" ? "1d" : btConfig.period,
          exchange: btConfig.exchange,
          market_type: btConfig.marketType,
          start_date: btConfig.startTime,
          end_date: btConfig.endTime,
          initial_capital: btConfig.initCash,
          commission_rate: btConfig.commission / 100,
        }),
      });
      const body = (await r.json()) as { data?: { task_id?: string } | null; error?: { message?: string } | null };
      const taskId = body.data?.task_id;
      if (!r.ok || !taskId) throw new Error(body.error?.message ?? `HTTP ${r.status}`);
      setBtTaskId(taskId);
      setBtStatus("pending");
    } catch (e: unknown) {
      setBtError(e instanceof Error ? e.message : String(e));
    }
  }, [btConfig, id, strategy]);

  useEffect(() => {
    if (!btTaskId) return;
    let alive = true;
    let attempt = 0;
    pollRef.current = setInterval(() => {
      attempt += 1;
      void (async () => {
        try {
          const r = await fetch(`/api/v1/backtests/${btTaskId}`);
          const body = (await r.json()) as { data?: BacktestData & { status?: string } | null };
          if (!alive) return;
          const status = String(body.data?.status ?? "");
          setBtStatus(status);
          if (status === "completed" && body.data) {
            setBtResult(body.data);
            if (pollRef.current) clearInterval(pollRef.current);
          } else if (status === "failed") {
            setBtError("回测失败(后端任务 failed)");
            if (pollRef.current) clearInterval(pollRef.current);
          } else if (attempt > 150) {
            setBtError("回测超时(>5 分钟)");
            if (pollRef.current) clearInterval(pollRef.current);
          }
        } catch {
          // 轮询抖动容忍,等下一拍
        }
      })();
    }, 2000);
    return () => {
      alive = false;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [btTaskId]);

  // --- 参数改动 → 本地 dirty ---
  const handleStrategyChange = useCallback((updated: Partial<StrategyData>) => {
    setStrategy((prev) => (prev ? { ...prev, ...updated } : prev));
    setDirty(true);
  }, []);

  const handleRevert = useCallback(
    (config: Record<string, unknown>) => {
      setStrategy((prev) => (prev ? { ...prev, ...(config as Partial<StrategyData>) } : prev));
      setDirty(true);
    },
    []
  );

  const running = btStatus === "pending" || btStatus === "running";
  const headerMeta = useMemo(() => {
    if (!strategy) return [];
    return [
      strategy.direction ?? null,
      strategy.symbols?.length ? strategy.symbols.join(" / ") : strategy.symbol ?? null,
      strategy.interval ?? null,
      strategy.version != null ? `v${strategy.version}` : null,
    ].filter(Boolean) as string[];
  }, [strategy]);

  if (loadError) {
    return (
      <div className="h-full overflow-y-auto p-4">
        <button
          type="button"
          onClick={onExit}
          className="flex w-fit items-center gap-1.5 text-[11px] text-ink-3 transition-colors hover:text-ink"
        >
          <ArrowLeft className="h-3 w-3" /> 返回驾驶舱
        </button>
        <div className="mt-3 rounded-lg border border-neg/40 bg-surface-2 p-4 text-[12px] leading-relaxed text-ink-2">
          策略加载失败({loadError})— 该 id 可能不在 strategies 表(驾驶舱 subject 未必是 strategy 工厂对象)。
        </div>
      </div>
    );
  }

  if (!strategy) {
    return (
      <div className="h-full overflow-y-auto p-4">
        <button
          type="button"
          onClick={onExit}
          className="flex w-fit items-center gap-1.5 text-[11px] text-ink-3 transition-colors hover:text-ink"
        >
          <ArrowLeft className="h-3 w-3" /> 返回驾驶舱
        </button>
        <div className="mt-4 space-y-3">
          <Skeleton className="h-9 w-2/3" />
          <div className="grid grid-cols-3 gap-3">
            <Skeleton className="h-64" />
            <Skeleton className="h-64" />
            <Skeleton className="h-64" />
          </div>
        </div>
      </div>
    );
  }

  const symbolsForConfig = strategy.symbols?.length
    ? strategy.symbols
    : strategy.symbol
      ? [strategy.symbol]
      : ["BTC/USDT"];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* 头部:真值条 + 灵魂问句 + 动作 */}
      <div className="shrink-0 border-b border-border bg-card/60 px-4 py-2.5">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onExit}
            className="flex items-center gap-1 text-[11px] text-ink-3 transition-colors hover:text-ink"
          >
            <ArrowLeft className="h-3 w-3" />
          </button>
          <h1 className="truncate text-[15px] font-semibold">{strategy.name}</h1>
          {headerMeta.map((m) => (
            <span key={m} className="num shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-ink-2">
              {m}
            </span>
          ))}
          <div className="ml-auto flex items-center gap-2">
            {saveMsg && <span className="text-[11px] text-ink-3">{saveMsg}</span>}
            <Button size="sm" variant="outline" className="h-7 px-3 text-xs" onClick={handleSave} disabled={saving || !dirty}>
              {saving ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : dirty ? <Save className="mr-1 h-3 w-3" /> : <Check className="mr-1 h-3 w-3" />}
              {saving ? "保存中" : dirty ? "保存" : "已同步"}
            </Button>
            <Button size="sm" className="h-7 bg-emerald-600 px-3 text-xs hover:bg-emerald-700" onClick={() => void runBacktest()} disabled={running}>
              <Play className="mr-1 h-3 w-3" /> 跑回测
            </Button>
          </div>
        </div>
        <p className="mt-1.5 text-[11px] text-ink-3">这次改动让策略更好了吗?改参数 → 跑回测 → 和上一版比。</p>
      </div>

      {/* 三栏:左参数 24% / 主区 tab / 右 AI 29% */}
      <div className="grid min-h-0 flex-1 grid-cols-[24%_1fr_29%] gap-px bg-border">
        {/* 左栏:参数常驻 */}
        <div className="min-h-0 overflow-y-auto bg-background p-3">
          {strategy ? <ParamPanel strategy={strategy} onChange={handleStrategyChange} /> : null}
        </div>

        {/* 主区:回测 / 部署监控 / 版本历史 */}
        <div className="min-h-0 overflow-hidden bg-background">
          <Tabs defaultValue="backtest" className="flex h-full min-h-0 flex-col">
            <TabsList className="shrink-0 justify-start rounded-none border-b bg-transparent px-2 py-0">
              <TabsTrigger value="backtest" className="text-xs data-[state=active]:shadow-none">
                回测
              </TabsTrigger>
              <TabsTrigger value="monitor" className="text-xs data-[state=active]:shadow-none">
                部署监控
              </TabsTrigger>
              <TabsTrigger value="versions" className="text-xs data-[state=active]:shadow-none">
                版本历史
              </TabsTrigger>
            </TabsList>

            <TabsContent value="backtest" className="mt-0 min-h-0 flex-1 overflow-y-auto">
              <BacktestConfigBar
                config={btConfig ?? {
                  startTime: new Date(Date.now() - 90 * 86_400_000).toISOString().slice(0, 10),
                  endTime: new Date().toISOString().slice(0, 10),
                  symbol: symbolsForConfig[0] ?? "BTC/USDT",
                  period: strategy.interval === "1d" ? "1d" : "1h",
                  benchmark: "BTC/USDT",
                  initCash: 10000,
                  stake: 100,
                  slippage: 0.1,
                  commission: 0.05,
                  stampDuty: 0.1,
                  exchange: "binance",
                  marketType: "spot",
                }}
                onConfigChange={(c) => setBtConfig(c)}
                onRunBacktest={() => void runBacktest()}
                symbols={symbolsForConfig}
                loading={running}
              />
              {btError ? (
                <div className="m-3 flex items-start gap-2 rounded-lg border border-neg/40 bg-surface-2 p-3 text-xs text-ink-2">
                  <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-neg" />
                  <span>{btError}</span>
                </div>
              ) : null}
              {running ? (
                <div className="m-3 flex items-center gap-2 text-xs text-ink-3">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> 回测进行中(任务 {btTaskId?.slice(0, 8)}…,{btStatus})
                </div>
              ) : null}
              {btResult ? (
                <BacktestResultsTabs result={btResult} strategy={strategy} />
              ) : !running && !btError ? (
                // 军规5:空显示为空 + 教学
                <div className="flex h-64 flex-col items-center justify-center gap-2 text-center">
                  <Play className="h-5 w-5 text-ink-3" />
                  <p className="text-xs text-ink-2">还没有本版回测结果</p>
                  <p className="max-w-[280px] text-[11px] leading-relaxed text-ink-3">
                    调好左栏参数和上方区间,点「跑回测」。结果出来后这里出完整曲线和交易表,和版本历史里的旧版对比。
                  </p>
                </div>
              ) : null}
            </TabsContent>

            <TabsContent value="monitor" className="mt-0 min-h-0 flex-1 overflow-y-auto p-4">
              {/* 懒挂载:进 tab 才拉 runner 状态,不烧页面级限流预算 */}
              <MonitorTab strategyId={id} onLifecycleChange={() => setVersionKey((k) => k + 1)} />
            </TabsContent>

            <TabsContent value="versions" className="mt-0 min-h-0 flex-1 overflow-y-auto">
              <VersionTimeline
                strategyId={id}
                currentVersion={strategy.version ?? 0}
                onRevert={handleRevert}
                refreshKey={versionKey}
              />
            </TabsContent>
          </Tabs>
        </div>

        {/* 右栏:AI 侧栏(原三 tab:对话/YAML/参数) */}
        <div className="min-h-0 overflow-hidden bg-background">
          <AiSidebar
            strategyId={id}
            strategyName={strategy.name}
            strategy={strategy}
            onStrategyChange={handleStrategyChange}
            onStrategyRefresh={() => setVersionKey((k) => k + 1)}
            onCollapse={onExit}
          />
        </div>
      </div>
    </div>
  );
}

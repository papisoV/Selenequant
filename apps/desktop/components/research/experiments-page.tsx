"use client";

import { useCallback, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { forkExperiment, listExperiments, type Experiment } from "@/lib/api/experiments";
import { deployExperimentToPaper, type PaperPlan } from "@/lib/api/paper-plans";
import { GitFork, Loader2, FlaskConical, Rocket, FileSearch } from "lucide-react";

function fmtNum(v: unknown): string {
  if (typeof v === "number" && Number.isFinite(v)) return v.toLocaleString();
  return "—";
}

function fmtRate(v: unknown): string {
  if (typeof v === "number" && Number.isFinite(v)) return v.toFixed(2);
  return "—";
}

// experiments-page:研究版本控制雏形 — 所有 PA 形态实验的登记列表
// 数据来自 GET /api/v1/experiments(bare dict 列表,newest first)
export function ExperimentsPage() {
  const { useApi } = useApiPage({ distinguish: true });
  const { data, error, isLoading, mutate } = useApi<Experiment[]>("/api/v1/experiments?limit=100");
  const [forkingId, setForkingId] = useState<string | null>(null);
  const [forkError, setForkError] = useState<string | null>(null);
  const [deployingId, setDeployingId] = useState<string | null>(null);
  const [deployError, setDeployError] = useState<string | null>(null);
  const [deployedPlans, setDeployedPlans] = useState<Record<string, PaperPlan>>({});

  const onFork = useCallback(async (exp: Experiment) => {
    setForkingId(exp.id);
    setForkError(null);
    try {
      const child = await forkExperiment(exp.id, {
        name: `${exp.name} (fork)`,
      });
      void child;
      await mutate();
    } catch (err) {
      setForkError(err instanceof Error ? err.message : String(err));
    } finally {
      setForkingId(null);
    }
  }, [mutate]);

  const onDeploy = useCallback(async (exp: Experiment) => {
    setDeployingId(exp.id);
    setDeployError(null);
    try {
      const plan = await deployExperimentToPaper(exp.id);
      setDeployedPlans((prev) => ({ ...prev, [exp.id]: plan }));
    } catch (err) {
      setDeployError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeployingId(null);
    }
  }, []);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
        <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> 加载实验…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-destructive">
        实验服务不可用：{error.message}
      </div>
    );
  }

  const experiments = data ?? [];

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3">
      <div className="flex shrink-0 items-center gap-2">
        <FlaskConical className="h-4 w-4 text-primary" />
        <h2 className="text-[13px] font-semibold text-foreground">实验登记</h2>
        <Badge variant="outline" className="text-[10px]">{experiments.length}</Badge>
        {forkError && <span className="ml-auto text-[11px] text-destructive">{forkError}</span>}
        {deployError && <span className="ml-2 text-[11px] text-destructive">{deployError}</span>}
      </div>

      {experiments.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center text-center">
          <p className="text-sm font-medium text-foreground">还没有实验</p>
          <p className="mt-1 max-w-[260px] text-[12px] leading-5 text-muted-foreground">
            在"决策工作流"页用 AI 分析图表生成入场/止损/止盈后，点"保存为实验"即可登记到这里。
          </p>
        </div>
      ) : (
        <div className="grid min-h-0 flex-1 auto-rows-min gap-2 overflow-y-auto md:grid-cols-2">
          {experiments.map((exp) => (
            <ExperimentCard
              key={exp.id}
              exp={exp}
              forking={forkingId === exp.id}
              onFork={() => void onFork(exp)}
              deploying={deployingId === exp.id}
              onDeploy={() => void onDeploy(exp)}
              deployedPlan={deployedPlans[exp.id]}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ExperimentCard({ exp, forking, onFork, deploying, onDeploy, deployedPlan }: {
  exp: Experiment;
  forking: boolean;
  onFork: () => void;
  deploying: boolean;
  onDeploy: () => void;
  deployedPlan?: PaperPlan;
}) {
  const sp = exp.setup_params;
  return (
    <Card className="overflow-hidden">
      <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0 px-3 py-2.5">
        <div className="min-w-0">
          <CardTitle className="text-[13px] font-medium leading-5">{exp.name}</CardTitle>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {exp.symbol ?? "—"} · {exp.timeframe ?? "—"}
            {exp.parent_experiment && <span className="ml-2 text-violet-400">fork of {exp.parent_experiment}</span>}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button size="sm" variant="outline" className="h-6 shrink-0 gap-1 rounded-lg text-[10px]" onClick={onDeploy} disabled={deploying || !!deployedPlan}>
            {deploying ? <Loader2 className="h-3 w-3 animate-spin" /> : <Rocket className="h-3 w-3" />}
            {deployedPlan ? "已部署" : "部署到 paper"}
          </Button>
          <a
            href={`/research/experiment-evidence?id=${exp.id}`}
            className="inline-flex h-6 shrink-0 items-center justify-center gap-1 rounded-lg border border-border bg-transparent px-3 text-[10px] font-medium text-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            <FileSearch className="h-3 w-3" /> 证据
          </a>
          <Button size="sm" variant="outline" className="h-6 shrink-0 gap-1 rounded-lg text-[10px]" onClick={onFork} disabled={forking}>
            {forking ? <Loader2 className="h-3 w-3 animate-spin" /> : <GitFork className="h-3 w-3" />}
            fork
          </Button>
        </div>
      </CardHeader>
      <CardContent className="px-3 pb-2.5 pt-1">
        {deployedPlan && (
          <div className="mb-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-2 py-1.5 text-[11px] text-emerald-300">
            已登记 paper 计划 <span className="font-mono">{deployedPlan.id}</span>（status: {deployedPlan.status}）
          </div>
        )}
        {exp.hypothesis && (
          <p className="text-[11px] leading-4 text-muted-foreground">{exp.hypothesis}</p>
        )}
        {sp && (sp.entry || sp.stop_loss || sp.take_profit) && (
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] font-mono">
            <span className="text-foreground/80">入场 {sp.entry?.toLocaleString() ?? "—"}</span>
            <span className="text-[#EF5350]">SL {sp.stop_loss?.toLocaleString() ?? "—"}</span>
            <span className="text-[#10b981]">TP {sp.take_profit?.toLocaleString() ?? "—"}</span>
            {sp.direction && <span className="text-muted-foreground">{sp.direction}</span>}
          </div>
        )}
        {exp.backtest_result && (
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
            <span>样本 {fmtNum(exp.backtest_result.total_setups)}</span>
            <span>胜率 {fmtRate(exp.backtest_result.win_rate)}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

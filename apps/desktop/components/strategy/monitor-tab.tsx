"use client";

import { useState } from "react";
import { Loader2, Play, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { RunnerMonitorPanel } from "./runner-monitor";
import { useRunnerMonitor } from "./use-runner-monitor";
import { useUiText } from "@/components/i18n/use-ui-text"

// US-0c 部署监控 tab:REST 初值 + 独立 paper WS 增量(喂 RunnerMonitorPanel)。
// 独立组件 = 懒挂载:Radix Tabs 卸载非激活 content,进 tab 才发首个请求,
// 不烧工作台页面级的 120/min 限流预算(E2E 串行 4 页共享 127.0.0.1 预算,页面级并发拉会 429)。
export function MonitorTab({
  strategyId,
  onLifecycleChange,
}: {
  strategyId: string;
  onLifecycleChange?: () => void;
}) {
  const tr = useUiText()

  const monitor = useRunnerMonitor(strategyId);
  const [deployMsg, setDeployMsg] = useState<string | null>(null);

  const handleDeploy = async () => {
    setDeployMsg(null);
    const err = await monitor.deployPaper();
    setDeployMsg(err ?? null);
    if (!err) onLifecycleChange?.();
  };

  const handleStop = async () => {
    setDeployMsg(null);
    const err = await monitor.stopPaper();
    setDeployMsg(err ?? null);
    if (!err) onLifecycleChange?.();
  };

  if (monitor.loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (monitor.loadError) {
    return (
      <div className="flex flex-col items-start gap-2">
        <div className="flex items-start gap-2 rounded-lg border border-neg/40 bg-surface-2 p-3 text-xs text-ink-2">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-neg" />
          <span>{tr("部署状态加载失败({err})", { err: monitor.loadError })}</span>
        </div>
        <Button size="sm" variant="outline" className="h-7 px-3 text-xs" onClick={() => void monitor.refresh()}>
          {tr("重试")}
        </Button>
      </div>
    );
  }

  if (monitor.slot && monitor.deployment) {
    return (
      <div className="flex flex-col gap-3">
        <RunnerMonitorPanel
          strategyId={strategyId}
          accountId={monitor.deployment.accountId}
          deploymentId={monitor.deployment.deploymentId}
          connected={monitor.connected}
          signals={monitor.signals}
          slotStatus={monitor.slot}
        />
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            className="h-7 px-3 text-xs"
            onClick={() => void handleStop()}
            disabled={monitor.busy !== null}
          >
            {monitor.busy === "stop" ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
            {tr("停止部署")}
          </Button>
          {deployMsg && <span className="text-[11px] text-neg">{deployMsg}</span>}
        </div>
      </div>
    );
  }

  // 军规5:无部署 → 教学空态 + 部署入口
  return (
    <div className="flex flex-col items-center gap-3">
      <div className="flex flex-col items-center justify-center gap-2 text-center">
        <p className="text-xs text-ink-2">{tr("该策略没有运行中的部署实例")}</p>
        <p className="max-w-[320px] text-[11px] leading-relaxed text-ink-3">
          {tr("部署到 Paper 后策略持续吃实时行情、吐信号、下模拟单;这里看槽位计数和信号日志。先确认回测跑通再部署。")}
        </p>
      </div>
      <Button
        size="sm"
        className="h-7 bg-emerald-600 px-3 text-xs hover:bg-emerald-700"
        onClick={() => void handleDeploy()}
        disabled={monitor.busy !== null}
      >
        {monitor.busy === "deploy" ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Play className="mr-1 h-3 w-3" />}
        {tr("部署到 Paper")}
      </Button>
      {deployMsg && <span className="text-[11px] text-neg">{deployMsg}</span>}
    </div>
  );
}

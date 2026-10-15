"use client";

import useSWR from "swr";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { getRunnerStatus, type RunnerStatus } from "@/lib/api/strategy-runner";

export function RunnerStatusPanel() {
  const { data, isLoading } = useSWR("/api/v1/strategies/runner/status", getRunnerStatus, {
    refreshInterval: 10000,
  });
  const s: RunnerStatus | undefined = data;
  const deployments = s?.deployments ?? [];

  return (
    <V17Card title="策略 Runner 状态">
      {isLoading && !s && (
        <div className="text-[11px] text-ink-3 py-2 animate-pulse">加载中…</div>
      )}
      <div className="grid grid-cols-3 gap-2">
        <StatBlock
          label="Runner"
          value={s?.running ? "RUNNING" : "STOPPED"}
          cls={s?.running ? "text-pos" : "text-neg"}
        />
        <StatBlock label="部署数" value={s?.deployment_count ?? 0} />
        <StatBlock label="活跃 slots" value={deployments.length} cls="text-gold" />
      </div>
      {deployments.length > 0 && (
        <div className="mt-2 max-h-40 overflow-auto">
          <div className="grid grid-cols-[1fr_1fr_80px_80px] gap-1 text-[9px] text-ink-4 py-1 border-b border-border-soft font-mono">
            <span>deployment_id</span><span>strategy_id</span><span>状态</span><span>slot</span>
          </div>
          {deployments.map((d, i) => (
            <div
              key={i}
              className="grid grid-cols-[1fr_1fr_80px_80px] gap-1 text-[10px] py-0.5 border-b border-border-soft font-mono"
            >
              <span className="text-ink-3 truncate" title={String(d.deployment_id ?? "")}>
                {String(d.deployment_id ?? "—").slice(0, 16)}
              </span>
              <span className="text-gold truncate" title={String(d.strategy_id ?? "")}>
                {String(d.strategy_id ?? "—").slice(0, 16)}
              </span>
              <span className={d.status === "running" ? "text-pos" : "text-gold"}>
                {d.status ?? "—"}
              </span>
              <span className="text-ink-4">{i + 1}</span>
            </div>
          ))}
        </div>
      )}
    </V17Card>
  );
}

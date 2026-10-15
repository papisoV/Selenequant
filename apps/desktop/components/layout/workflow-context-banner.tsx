"use client";

import { useWorkflowStore } from "@/lib/store/workflow";

export function WorkflowContextBanner() {
  const template = useWorkflowStore((s) => s.template);
  const optimization = useWorkflowStore((s) => s.optimization);
  const backtest = useWorkflowStore((s) => s.backtest);
  const deployTarget = useWorkflowStore((s) => s.deployTarget);
  const reset = useWorkflowStore((s) => s.reset);

  if (!template && !optimization && !backtest) return null;

  const parts: string[] = [];
  if (backtest) {
    parts.push(`从回测中心跳转`);
    if (template) parts.push(`策略: ${template.name}`);
    if (backtest.sharpe != null) parts.push(`Sharpe: ${backtest.sharpe.toFixed(2)}`);
  } else if (template) {
    parts.push(`策略模板: ${template.name}`);
  }
  if (deployTarget) {
    parts.push(`目标: ${deployTarget === "paper" ? "模拟盘" : "实盘"}`);
  }

  return (
    <div className="mb-2 flex items-center gap-2 rounded border border-gold/30 bg-gold/5 px-3 py-1.5 text-[10px] text-gold">
      <span>{parts.join(" · ")}</span>
      <button
        onClick={reset}
        className="ml-auto rounded bg-surface-2 px-2 py-0.5 text-[9px] text-ink-3 hover:text-ink-1"
      >
        清除
      </button>
    </div>
  );
}
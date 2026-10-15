"use client";

import { EvidenceLine } from "./evidence-line";
import type { StrategyEvidence } from "@/lib/hooks/use-strategy-evidence";

export interface SavedStrategy {
  name: string;
  version: string;
  formula_readable: string;
  formula_tokens: number[];
  profiles: Array<Record<string, unknown>>;
}

interface Props {
  s: SavedStrategy;
  evidence?: StrategyEvidence | null;
  onClick: () => void;
  onDeployPaper?: () => void;
  onDeployLive?: () => void;
  deployingPaper?: boolean;
  deployingLive?: boolean;
}

export function AlphaStrategyCard({
  s,
  evidence,
  onClick,
  onDeployPaper,
  onDeployLive,
  deployingPaper,
  deployingLive,
}: Props) {
  const interval = String(s.profiles[0]?.base_interval || "15m");
  const busy = deployingPaper || deployingLive;
  return (
    <div
      key={`${s.version}-${s.name}`}
      data-testid="strat-card"
      className="rounded border border-border-soft bg-surface-2 p-2 hover:border-gold cursor-pointer"
      onClick={onClick}
    >
      <div className="flex justify-between items-start">
        <div className="min-w-0">
          <div className="text-[10px] font-medium text-ink truncate">{s.name}</div>
          <div className="text-[9px] text-gold mt-0.5 font-mono">{s.version}</div>
        </div>
        <span className="text-[9px] rounded bg-surface-3 px-1.5 py-0.5 text-ink-3">
          alpha
        </span>
      </div>
      <div className="text-[9px] text-ink-3 mt-1 font-mono truncate">
        {s.formula_readable}
      </div>
      <div className="text-[8px] text-ink-4 mt-1">
        {s.formula_tokens.length} tokens | {s.profiles.length} 配置 | {interval}
      </div>

      <EvidenceLine evidence={evidence} />
      {(onDeployPaper || onDeployLive) && (
        <div className="flex gap-1 mt-2">
          {onDeployPaper && (
            <button
              className="row-btn flex-1 rounded border border-border-soft bg-surface-3 px-2 py-1 text-[9px] text-pos disabled:opacity-50"
              onClick={(e) => { e.stopPropagation(); onDeployPaper(); }}
              disabled={busy}
            >
              {deployingPaper ? "…" : "Paper"}
            </button>
          )}
          {onDeployLive && (
            <button
              className="row-btn flex-1 rounded border border-border-soft bg-surface-3 px-2 py-1 text-[9px] text-neg disabled:opacity-50"
              onClick={(e) => { e.stopPropagation(); onDeployLive(); }}
              disabled={busy}
            >
              {deployingLive ? "…" : "Live"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

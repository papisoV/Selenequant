"use client";

import { StatBlock } from "@/components/shared/page-filter-bar";
import { EvidenceLine } from "./evidence-line";
import type { StrategyEvidence } from "@/lib/hooks/use-strategy-evidence";
import { useUiText } from "@/components/i18n/use-ui-text"

export interface StratTemplate {
  id: string;
  name: string;
  cat: string;
  sharpe: number;
  winrate: number;
  /** false = 目录里挂着但无实现:卡片禁用回测/部署,而不是让用户撞上跑不出成交的空跑。 */
  runnable?: boolean;
}

interface Props {
  t: StratTemplate;
  evidence?: StrategyEvidence | null;
  onBacktest: () => void;
  onDeployPaper: () => void;
  onDeployLive: () => void;
  onCardClick: () => void;
  deployingPaper?: boolean;
  deployingLive?: boolean;
}

export function FactoryTemplateCard({
  t,
  evidence,
  onBacktest,
  onDeployPaper,
  onDeployLive,
  onCardClick,
  deployingPaper,
  deployingLive,
}: Props) {
  const tr = useUiText()

  const sharpeCls = t.sharpe >= 1.8 ? "text-pos" : t.sharpe < 1.4 ? "text-neg" : "text-ink-2";
  const runnable = t.runnable !== false;
  return (
    <div
      data-testid="strat-card"
      aria-disabled={!runnable}
      title={runnable ? undefined : tr("该模板在目录中列出但尚无实现，无法回测或部署")}
      className={`rounded border border-border-soft bg-surface-2 p-2 hover:border-gold cursor-pointer${runnable ? "" : " opacity-60"}`}
      onClick={onCardClick}
    >
      <div className="flex justify-between items-start">
        <div>
          <div className="text-[10px] font-medium text-ink">{tr(t.name)}</div>
          <div className="text-[9px] text-gold mt-0.5 font-mono">{t.id}</div>
        </div>
        <span className="text-[9px] rounded bg-surface-3 px-1.5 py-0.5 text-ink-3">
          {tr(t.cat)}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-1 mt-2 text-[9px]">
        <StatBlock label={tr("夏普")} value={t.sharpe.toFixed(2)} cls={sharpeCls} />
        <StatBlock label={tr("胜率")} value={`${t.winrate}%`} />
      </div>

      <EvidenceLine evidence={evidence} />

      <div className="flex gap-1 mt-2">
        <button
          className="row-btn flex-1 rounded border border-border-soft bg-surface-3 px-2 py-1 text-[9px] text-gold disabled:opacity-50"
          disabled={!runnable}
          onClick={(e) => { e.stopPropagation(); onBacktest(); }}
        >
          {tr("回测")}
        </button>
        <button
          className="row-btn flex-1 rounded border border-border-soft bg-surface-3 px-2 py-1 text-[9px] text-pos disabled:opacity-50"
          onClick={(e) => { e.stopPropagation(); onDeployPaper(); }}
          disabled={!runnable || deployingPaper || deployingLive}
        >
          {deployingPaper ? "…" : "Paper"}
        </button>
        <button
          className="row-btn flex-1 rounded border border-border-soft bg-surface-3 px-2 py-1 text-[9px] text-neg disabled:opacity-50"
          onClick={(e) => { e.stopPropagation(); onDeployLive(); }}
          disabled={!runnable || deployingPaper || deployingLive}
        >
          {deployingLive ? "…" : "Live"}
        </button>
      </div>
    </div>
  );
}

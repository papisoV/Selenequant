"use client";

import { StatBlock } from "@/components/shared/page-filter-bar";
import type { TuningResultSummary } from "@/lib/tuning-bridge";
import { useUiText } from "@/components/i18n/use-ui-text"

interface Props {
  r: TuningResultSummary;
  applying: boolean;
  onClick: () => void;
}

export function TuningResultCard({ r, applying, onClick }: Props) {
  const tr = useUiText()

  const sharpeCls = r.best_sharpe >= 1.8 ? "text-pos" : r.best_sharpe < 1.4 ? "text-neg" : "text-ink-2";
  const gated = r.oos_rejected === true || r.all_rejected === true;
  return (
    <div
      key={r.file_name}
      data-testid="strat-card"
      className={`rounded border border-border-soft bg-surface-2 p-2 hover:border-gold cursor-pointer ${gated ? "opacity-50" : ""}`}
      onClick={onClick}
    >
      <div className="flex justify-between items-start">
        <div className="min-w-0">
          <div className="text-[10px] font-medium text-ink truncate">{tr(r.strategy_name)}</div>
          <div className="text-[9px] text-gold mt-0.5 font-mono">{r.strategy_id}</div>
        </div>
        {gated ? (
          <span className="text-[9px] rounded bg-neg/20 text-neg px-1.5 py-0.5">
            {r.oos_rejected ? tr("OOS衰减") : tr("trades不足")}
          </span>
        ) : (
          <span className="text-[9px] rounded bg-surface-3 px-1.5 py-0.5 text-ink-3">
            {r.interval}
          </span>
        )}
        {r.pre_noop_fix && (
          <span className="ml-1 text-[9px] rounded bg-amber-500/20 text-amber-500 px-1.5 py-0.5">
            {tr("修复前")}
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-1 mt-2 text-[9px]">
        <StatBlock label={tr("净收益夏普")} value={r.best_sharpe.toFixed(2)} cls={sharpeCls} />
        <StatBlock label={tr("胜率")} value={`${(r.win_rate * 100).toFixed(0)}%`} />
      </div>
      {r.deflated_sharpe_ratio != null && !gated && (
        <div className="text-[8px] text-ink-3 mt-0.5">
          DSR{" "}
          <span
            className={`font-mono ${r.deflated_sharpe_ratio >= 0 ? "text-pos" : "text-neg"}`}
          >
            {r.deflated_sharpe_ratio.toFixed(2)}
          </span>
        </div>
      )}
      <div className="text-[8px] text-ink-4 mt-1">
        {r.exchange}/{r.market_type} | {r.symbol} | {r.n_trials} {tr("次")}
        {r.annualization_factor != null && <span> {tr("| 年化")}{r.annualization_factor}</span>}
      </div>
      {r.oos_sharpe != null && !gated && (
        <div className="text-[8px] text-ink-3 mt-0.5">OOS {r.oos_sharpe.toFixed(2)}</div>
      )}
      <div className="text-[9px] text-ink-3 mt-1">
        {applying ? tr("应用中...") : tr("点击应用 → 工作台")}
      </div>
    </div>
  );
}

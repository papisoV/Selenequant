"use client";

import { useState } from "react";
import {
  buildDcaSchedule,
  estimateAvgCost,
  DEMO_DCA_PRICES,
  type DcaScheduleRow,
} from "@/lib/quant/dca";
import { useUiText } from "@/components/i18n/use-ui-text"

export function DcaPanel() {
  const tr = useUiText()

  const [amount, setAmount] = useState("100");
  const [periods, setPeriods] = useState("6");
  const [cycle, setCycle] = useState(tr("周"));
  const [schedule, setSchedule] = useState<DcaScheduleRow[] | null>(null);

  const run = () => {
    const amountN = Math.max(0, Number(amount) || 0);
    const periodsN = Math.min(24, Math.max(1, Math.floor(Number(periods) || 1)));
    setSchedule(buildDcaSchedule({ amountPerPeriod: amountN, periodCount: periodsN, periodLabel: cycle }, DEMO_DCA_PRICES));
  };

  const totalInvested = schedule?.at(-1)?.invested ?? 0;
  const totalUnits = schedule?.at(-1)?.totalUnits ?? 0;
  const avg = estimateAvgCost(totalInvested, totalUnits);

  return (
    <div className="dca-panel flex flex-col gap-2">
      <div className="grid grid-cols-3 gap-2">
        <label className="flex flex-col gap-1 text-[10px] text-ink-3">
          {tr("每期金额")}
          <input aria-label={tr("每期金额")} type="number" value={amount} onChange={(e) => setAmount(e.target.value)}
            className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink" />
        </label>
        <label className="flex flex-col gap-1 text-[10px] text-ink-3">
          {tr("期数")}
          <input aria-label={tr("期数")} type="number" value={periods} onChange={(e) => setPeriods(e.target.value)}
            className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink" />
        </label>
        <label className="flex flex-col gap-1 text-[10px] text-ink-3">
          {tr("周期")}
          <select aria-label={tr("周期")} value={cycle} onChange={(e) => setCycle(e.target.value)}
            className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink">
            <option value={tr("日")}>{tr("日")}</option>
            <option value={tr("周")}>{tr("周")}</option>
            <option value={tr("月")}>{tr("月")}</option>
          </select>
        </label>
      </div>
      <button type="button" onClick={run}
        className="w-fit rounded bg-gold px-2 py-1 text-[10px] font-medium text-black transition-opacity hover:opacity-90">
        {tr("生成计划表")}
      </button>

      {schedule && (
        <>
          <div className="grid grid-cols-3 gap-2 text-[10px]">
            <div className="rounded border border-border-soft px-2 py-1">
              <div className="text-ink-4">{tr("累计投入")}</div>
              <div className="font-mono text-ink">{totalInvested.toFixed(2)}</div>
            </div>
            <div className="rounded border border-border-soft px-2 py-1">
              <div className="text-ink-4">{tr("累计单位")}</div>
              <div className="font-mono text-ink">{totalUnits.toFixed(4)}</div>
            </div>
            <div className="rounded border border-border-soft px-2 py-1">
              <div className="text-ink-4">{tr("成本均价")}</div>
              <div className="font-mono text-gold">{avg.toFixed(4)}</div>
            </div>
          </div>
          <div className="overflow-auto rounded border border-border-soft">
            <table className="w-full text-[10px]">
              <thead className="bg-surface-2 text-ink-4">
                <tr>
                  <th className="px-2 py-1 text-left">{tr("期")}</th>
                  <th className="px-2 py-1 text-left">{tr("价格")}</th>
                  <th className="px-2 py-1 text-left">{tr("本期单位")}</th>
                  <th className="px-2 py-1 text-left">{tr("累计单位")}</th>
                  <th className="px-2 py-1 text-left">{tr("累计投入")}</th>
                  <th className="px-2 py-1 text-left">{tr("均价")}</th>
                </tr>
              </thead>
              <tbody>
                {schedule.map((row) => (
                  <tr key={row.period} className="dca-row border-t border-border-soft">
                    <td className="px-2 py-1">{row.period}</td>
                    <td className="px-2 py-1 font-mono">{row.price}</td>
                    <td className="px-2 py-1 font-mono">{row.units}</td>
                    <td className="px-2 py-1 font-mono">{row.totalUnits}</td>
                    <td className="px-2 py-1 font-mono">{row.invested.toFixed(2)}</td>
                    <td className="px-2 py-1 font-mono text-gold">{row.avgCost.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

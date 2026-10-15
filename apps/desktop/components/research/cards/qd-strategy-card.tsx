"use client";

import { StatBlock } from "@/components/shared/page-filter-bar";
import { getBacktestDetail, type BacktestDetail } from "@/lib/api/backtests";
import { runQdStrategy, type QDFlag, type QDStrategy } from "@/lib/api/qd-strategies";
import { EvidenceLine } from "./evidence-line";
import type { StrategyEvidence } from "@/lib/hooks/use-strategy-evidence";
import { toastError } from "@/lib/toast";
import { useState } from "react";

// A full-history run recomputes indicators on every bar (~2 min for the
// pilot's 6200 bars), so the poll window is minutes, not seconds.
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 5 * 60_000;

const FLAG_LABEL: Record<QDFlag, string> = {
  falsified: "已证伪",
  in_sample_only: "样本内",
  portfolio: "组合",
  multi_instrument: "多标的",
};

/** The three figures QD published for src48; other rows it never reported. */
const ROWS: {
  label: string;
  qdKey: string;
  seleneKey: keyof BacktestDetail;
  asPct: boolean;
}[] = [
  { label: "总收益", qdKey: "total_return", seleneKey: "total_return", asPct: true },
  { label: "最大回撤", qdKey: "max_drawdown", seleneKey: "max_drawdown", asPct: true },
  { label: "夏普", qdKey: "sharpe", seleneKey: "sharpe_ratio", asPct: false },
];

function fmt(value: number | undefined, asPct: boolean): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "—";
  return asPct ? `${value >= 0 ? "+" : ""}${(value * 100).toFixed(2)}%` : value.toFixed(2);
}

async function pollUntilSettled(taskId: string): Promise<BacktestDetail> {
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  let last: BacktestDetail = {};
  while (Date.now() < deadline) {
    last = await getBacktestDetail(taskId);
    if (last.status === "completed" || last.status === "failed") return last;
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }
  return last;
}

interface Props {
  s: QDStrategy;
  evidence?: StrategyEvidence | null;
}

export function QDStrategyCard({ s, evidence }: Props) {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BacktestDetail | null>(null);

  const qd = s.qd_baseline.metrics;

  const run = async () => {
    if (running || !s.runnable) return;
    setRunning(true);
    setResult(null);
    try {
      const taskId = await runQdStrategy(s.id);
      const settled = await pollUntilSettled(taskId);
      setResult(settled);
      if (settled.status !== "completed") {
        toastError(`${s.id} 回测未完成: ${settled.status ?? "超时"}`);
      }
    } catch (e: unknown) {
      toastError(`回测失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div data-testid="strat-card" className="rounded border border-border-soft bg-surface-2 p-2">
      <div className="flex justify-between items-start">
        <div>
          <div className="text-[10px] font-medium text-ink">{s.name}</div>
          <div className="text-[9px] text-gold mt-0.5 font-mono">{s.id}</div>
        </div>
        <div className="flex flex-wrap gap-1 justify-end">
          {s.flags.map((f) => (
            <span
              key={f}
              className="text-[9px] rounded bg-surface-3 px-1.5 py-0.5 text-neg"
            >
              {FLAG_LABEL[f] ?? f}
            </span>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-1 mt-2 text-[9px]">
        <StatBlock label="标的" value={s.symbol || "—"} />
        <StatBlock label="周期" value={s.interval} />
      </div>

      <EvidenceLine evidence={evidence} />

      <div className="mt-2 text-[9px]">
        <div className="flex justify-between text-ink-4 mb-0.5">
          <span>指标</span>
          <span>
            <span className="text-ink-3">QD 报告</span>
            <span className="mx-1">/</span>
            <span className="text-gold">selene</span>
          </span>
        </div>
        {ROWS.map((row) => (
          <div key={row.qdKey} className="flex justify-between py-0.5">
            <span className="text-ink-2">{row.label}</span>
            <span className="font-mono">
              <span className="text-ink-3">
                {fmt(qd?.[row.qdKey], row.asPct)}
              </span>
              <span className="mx-1 text-ink-4">/</span>
              <span className="text-gold">
                {fmt(result?.[row.seleneKey] as number | undefined, row.asPct)}
              </span>
            </span>
          </div>
        ))}
        {!qd && (
          <div className="text-ink-4 mt-0.5">
            QD 未提供结构化绩效({s.qd_baseline.reason})
          </div>
        )}
      </div>

      {result?.total_trades !== undefined && (
        <div className="text-[9px] text-ink-3 mt-1">
          selene 成交 {result.total_trades} 笔
        </div>
      )}

      <div className="flex gap-1 mt-2">
        <button
          className="row-btn flex-1 rounded border border-border-soft bg-surface-3 px-2 py-1 text-[9px] text-gold disabled:opacity-50"
          onClick={run}
          disabled={running || !s.runnable}
          title={s.runnable ? undefined : s.reason}
        >
          {running ? "回测中…" : "回测"}
        </button>
      </div>
      {!s.runnable && (
        <div className="text-[9px] text-neg mt-1">
          不可运行:{s.reason || "该导出无法被驱动"}
        </div>
      )}
    </div>
  );
}

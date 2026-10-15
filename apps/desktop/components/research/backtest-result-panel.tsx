"use client";

import type { BacktestResult } from "@/lib/indicators";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { computeUnderwaterFromEquity, computeReturnsFromEquity, buildHistogram } from "@/lib/chart-math";
import { DrawdownChart } from "@/components/strategy/equity-curve-chart";
import { ReturnDistributionHistogram } from "@/components/strategy/shared-charts";
import { RecordEvidenceButton } from "./record-evidence-button";
import { CreateMissionButton } from "./create-mission-button";

export function BacktestResultPanel({
  result,
  source = "live",
  context,
}: {
  result: BacktestResult;
  source?: "live" | "demo";
  /** 透明性批3-10: 调用方若有标的/策略上下文,透传给"开新实验验证"预填 */
  context?: { symbol?: string; strategyId?: string };
}) {
  const { equity_curve } = result;
  const underwater = computeUnderwaterFromEquity(equity_curve);
  const returnHistogram = buildHistogram(computeReturnsFromEquity(equity_curve));
  const W = 600;
  const H = 200;
  const P = 20;
  const min = equity_curve.length ? Math.min(...equity_curve) : 0;
  const max = equity_curve.length ? Math.max(...equity_curve) : 1;
  const range = max - min || 1;
  const pathD = equity_curve.length
    ? equity_curve
        .map((v, i) => {
          const x = P + (i / Math.max(equity_curve.length - 1, 1)) * (W - 2 * P);
          const y = H - P - ((v - min) / range) * (H - 2 * P);
          return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
        })
        .join(" ")
    : "";

  const metrics = [
    { label: "总收益", value: `${result.total_return.toFixed(2)}%`, cls: result.total_return >= 0 ? "text-pos" : "text-neg" },
    { label: "年化", value: `${result.annual_return.toFixed(2)}%`, cls: result.annual_return >= 0 ? "text-pos" : "text-neg" },
    { label: "夏普", value: result.sharpe_ratio.toFixed(2), cls: "text-ink" },
    { label: "最大回撤", value: `${result.max_drawdown.toFixed(2)}%`, cls: "text-destructive" },
    { label: "胜率", value: `${result.win_rate.toFixed(1)}%`, cls: "text-ink" },
    { label: "盈亏比", value: result.profit_factor.toFixed(2), cls: "text-ink" },
    { label: "交易数", value: String(result.total_trades), cls: "text-ink" },
  ];

  return (
    <div className="backtest-result-panel flex flex-col gap-2">
      <div className="flex items-center justify-between">
        {source === "demo" ? <span className="feed-demo-badge w-fit">演示</span> : <span />}
        <div className="flex items-center gap-1.5">
          <CreateMissionButton
            prefill={{
              title: context?.strategyId
                ? `验证 ${context.strategyId} 在 ${context?.symbol ?? "该标的"} 的表现`
                : "验证当前回测假设",
              question: context?.strategyId
                ? `${context.strategyId} 在 ${context?.symbol ?? "该标的"} 上的收益/回撤特征是否可复现、是否有真实边缘?`
                : "当前策略在当前标的上的表现是否可复现、是否有真实边缘?",
              subject_type: context?.strategyId ? "strategy" : "market",
              subject_id: context?.strategyId ?? context?.symbol ?? "BTC/USDT",
            }}
          />
          <RecordEvidenceButton
          prefill={{
            fact: `回测完成:总收益 ${result.total_return.toFixed(2)}%,夏普 ${result.sharpe_ratio.toFixed(2)},最大回撤 ${result.max_drawdown.toFixed(2)}%,交易数 ${result.total_trades}`,
            suggestedSignal:
              result.total_return > 0 && result.sharpe_ratio > 0
                ? "confirming"
                : result.total_return < 0
                  ? "weakening"
                  : "neutral",
          }}
          />
        </div>
      </div>
      <div className="grid grid-cols-4 gap-1 md:grid-cols-7">
        {metrics.map((m) => (
          <div key={m.label} className="rounded border border-border-soft px-2 py-1">
            <div className="text-[10px] text-ink-4">{m.label}</div>
            <div className={`font-mono text-[11px] ${m.cls}`}>{m.value}</div>
          </div>
        ))}
      </div>
      <div>
        <div className="mb-0.5 flex justify-between text-[10px] text-ink-4">
          <span>权益曲线</span>
          <span>
            低 {min.toFixed(2)} · 高 {max.toFixed(2)}
          </span>
        </div>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full rounded border border-border-soft"
          role="img"
          aria-label="权益曲线"
        >
          <line x1={P} y1={H - P} x2={W - P} y2={H - P} stroke="var(--color-border-soft)" strokeWidth="0.5" />
          <line x1={P} y1={P} x2={P} y2={H - P} stroke="var(--color-border-soft)" strokeWidth="0.5" />
          <path d={pathD} fill="none" stroke="var(--color-gold)" strokeWidth="1.5" />
        </svg>
      </div>
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">回撤水下曲线</CardTitle>
          </CardHeader>
          <CardContent>
            <DrawdownChart data={underwater} height={200} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">收益分布</CardTitle>
          </CardHeader>
          <CardContent>
            <ReturnDistributionHistogram bins={returnHistogram} emptyLabel="暂无收益分布数据" height={200} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

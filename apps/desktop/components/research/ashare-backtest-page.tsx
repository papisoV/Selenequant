"use client";

import { useState } from "react";
import { apiDirectRaw } from "@/lib/api-client";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";

interface BacktestMetrics {
  cumulative_return?: number;
  annual_return?: number;
  sharpe?: number;
  max_drawdown?: number;
  win_rate?: number;
  volatility?: number;
  time_in_market?: number;
  invested_win_rate?: number;
  avg_turnover?: number;
  calmar_ratio?: number;
  sortino_ratio?: number;
}

const WEIGHT_METHODS = [
  { value: "equal", label: "等权" },
  { value: "ir", label: "IR 全样本" },
  { value: "ic", label: "IC 全样本" },
  { value: "ir_walkforward", label: "IR walk-forward（无未来函数）" },
];

const FACTOR_SETS = [
  { value: "technical", label: "纯技术" },
  { value: "technical_fundamental", label: "技术+基本面" },
];

const pct = (v: number | undefined) =>
  v === undefined || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(2)}%`;

export function AshareBacktestPage() {
  const [symbols, setSymbols] = useState("600519,000858,601318");
  const [pool, setPool] = useState("");
  const [strategy, setStrategy] = useState("trend_follow");
  const [weightMethod, setWeightMethod] = useState("ir_walkforward");
  const [factorSet, setFactorSet] = useState("technical");
  const [costBps, setCostBps] = useState("30");
  const [nLong, setNLong] = useState("5");
  const [metrics, setMetrics] = useState<BacktestMetrics | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    setMetrics(null);
    try {
      const params = new URLSearchParams({
        symbols,
        strategy,
        n_long: nLong || "5",
        weight_method: weightMethod,
        factor_set: factorSet,
        cost_bps: costBps || "0",
      });
      if (pool) params.set("pool", pool);
      // backtest 成功返回裸 dict（仅错误有 envelope），apiDirectRaw 不解包
      const body = await apiDirectRaw<{ metrics: BacktestMetrics }>(
        `/api/v1/ashare/backtest?${params}`,
        { method: "POST" },
      );
      setMetrics(body.metrics ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="ashare-backtest-page flex h-full flex-col gap-3 overflow-y-auto">
      <h1 className="text-sm font-semibold text-ink">A股回测</h1>
      <p className="text-[10px] text-ink-4">
        技术因子组合 + 大盘体制过滤回测。walk-forward 权重逐日只用已实现 IC，
        无未来函数；成本按换手率扣减。
      </p>

      <V17Card title="回测设置">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-7">
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">股票池</span>
            <select
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              value={pool}
              onChange={(e) => setPool(e.target.value)}
            >
              <option value="">自定义代码</option>
              <option value="hs300">沪深300</option>
              <option value="csi500">中证500</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">股票代码（逗号分隔）</span>
            <input
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              value={symbols}
              onChange={(e) => setSymbols(e.target.value)}
              placeholder="600519,000858"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">策略</span>
            <select
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
            >
              <option value="trend_follow">趋势跟随</option>
              <option value="mean_reversion">均值回归</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">权重方法</span>
            <select
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              value={weightMethod}
              onChange={(e) => setWeightMethod(e.target.value)}
            >
              {WEIGHT_METHODS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">因子集</span>
            <select
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              value={factorSet}
              onChange={(e) => setFactorSet(e.target.value)}
            >
              {FACTOR_SETS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">单边成本（bp）</span>
            <input
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              type="number"
              min="0"
              value={costBps}
              onChange={(e) => setCostBps(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">最大持仓数</span>
            <input
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              type="number"
              min="1"
              value={nLong}
              onChange={(e) => setNLong(e.target.value)}
            />
          </label>
        </div>
        <div className="mt-2 flex items-center gap-2">
          <button
            className="rounded border border-border-soft bg-surface-2 px-3 py-1 text-[11px] text-ink disabled:opacity-50"
            disabled={running}
            onClick={run}
          >
            {running ? "回测中…" : "运行回测"}
          </button>
          {error && <span className="text-[10px] text-red-400">{error}</span>}
        </div>
      </V17Card>

      {metrics && (
        <V17Card title="回测结果">
          <div className="grid grid-cols-3 gap-2 md:grid-cols-5">
            <StatBlock
              label="累计收益"
              value={pct(metrics.cumulative_return)}
              cls={metrics.cumulative_return !== undefined && metrics.cumulative_return >= 0 ? "text-green-400" : "text-red-400"}
            />
            <StatBlock label="年化收益" value={pct(metrics.annual_return)} />
            <StatBlock
              label="Sharpe"
              value={metrics.sharpe === undefined || Number.isNaN(metrics.sharpe) ? "—" : metrics.sharpe.toFixed(2)}
            />
            <StatBlock
              label="最大回撤"
              value={pct(metrics.max_drawdown)}
              cls="text-red-400"
            />
            <StatBlock
              label="胜率"
              value={pct(metrics.win_rate)}
            />
            <StatBlock label="波动率" value={pct(metrics.volatility)} />
            <StatBlock label="在场时间" value={pct(metrics.time_in_market)} />
            <StatBlock label="在场胜率" value={pct(metrics.invested_win_rate)} />
            <StatBlock
              label="平均换手"
              value={pct(metrics.avg_turnover)}
            />
            <StatBlock label="Calmar" value={metrics.calmar_ratio === undefined || Number.isNaN(metrics.calmar_ratio) ? "—" : metrics.calmar_ratio.toFixed(2)} />
          </div>
        </V17Card>
      )}
    </div>
  );
}

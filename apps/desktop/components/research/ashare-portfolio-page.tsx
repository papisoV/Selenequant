"use client";

import { useState } from "react";
import { apiDirectRaw, ApiError } from "@/lib/api-client";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

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

interface PortfolioResult {
  portfolio_metrics: BacktestMetrics;
  benchmark_metrics: BacktestMetrics;
  excess_metrics: BacktestMetrics;
  config: Record<string, unknown>;
}

// ashare router returns bare dicts on success (envelope only on error) —
// unwrap like ashare-fundamentals-page.tsx does; envelope error.message
// (e.g. 零个可结算窗口) must reach the UI instead of "HTTP 404"
async function ashareGet<T>(path: string, init?: RequestInit): Promise<T> {
  const body = await apiDirectRaw<unknown>(path, init);
  if (
    body &&
    typeof body === "object" &&
    "data" in body &&
    "meta" in body
  ) {
    const envelope = body as { data: T | null; error?: { message?: string } };
    if (envelope.error) {
      throw new ApiError(envelope.error.message ?? "request failed", 500);
    }
    return envelope.data as T;
  }
  return body as T;
}

const METRIC_BLOCKS: Array<{
  label: string;
  pick: (m: BacktestMetrics) => { text: string; cls?: string };
}> = [
  {
    label: "累计收益",
    pick: (m) => ({
      text: pct(m.cumulative_return),
      cls:
        m.cumulative_return !== undefined && m.cumulative_return >= 0
          ? "text-green-400"
          : "text-red-400",
    }),
  },
  { label: "年化收益", pick: (m) => ({ text: pct(m.annual_return) }) },
  {
    label: "Sharpe",
    pick: (m) => ({
      text: m.sharpe === undefined || Number.isNaN(m.sharpe) ? "—" : m.sharpe.toFixed(2),
    }),
  },
  { label: "最大回撤", pick: (m) => ({ text: pct(m.max_drawdown), cls: "text-red-400" }) },
  { label: "胜率", pick: (m) => ({ text: pct(m.win_rate) }) },
  { label: "波动率", pick: (m) => ({ text: pct(m.volatility) }) },
  { label: "平均换手", pick: (m) => ({ text: pct(m.avg_turnover) }) },
  {
    label: "Calmar",
    pick: (m) => ({
      text:
        m.calmar_ratio === undefined || Number.isNaN(m.calmar_ratio)
          ? "—"
          : m.calmar_ratio.toFixed(2),
    }),
  },
];

function pct(v: number | undefined) {
  return v === undefined || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(2)}%`;
}

function MetricsGrid({ metrics }: { metrics: BacktestMetrics }) {
  return (
    <div className="grid grid-cols-3 gap-2 md:grid-cols-5">
      {METRIC_BLOCKS.map(({ label, pick }) => {
        const { text, cls } = pick(metrics);
        return <StatBlock key={label} label={label} value={text} cls={cls} />;
      })}
    </div>
  );
}

export function AsharePortfolioPage() {
  const tr = useUiText()

  const [symbols, setSymbols] = useState("600519,000858,601318");
  const [pool, setPool] = useState("");
  const [nLong, setNLong] = useState("20");
  const [rebalanceDays, setRebalanceDays] = useState("5");
  const [costBps, setCostBps] = useState("15");
  const [benchmarkIndex, setBenchmarkIndex] = useState("000300");
  const [startDate, setStartDate] = useState("");
  const [stockWeight, setStockWeight] = useState("equal");
  const [result, setResult] = useState<PortfolioResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const params = new URLSearchParams({
        symbols,
        n_long: nLong || "20",
        rebalance_days: rebalanceDays || "5",
        cost_bps: costBps || "15",
        forward_col: "fwd_5d",
        benchmark_index: benchmarkIndex || "000300",
        stock_weight: stockWeight,
      });
      if (pool) params.set("pool", pool);
      if (startDate) params.set("start_date", startDate);
      // portfolio-backtest 成功返回裸 dict（仅错误有 envelope）
      const body = await ashareGet<PortfolioResult>(
        `/api/v1/ashare/portfolio-backtest?${params}`,
        { method: "POST" },
      );
      setResult(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="ashare-portfolio-page flex h-full flex-col gap-3 overflow-y-auto">
      <h1 className="text-sm font-semibold text-ink">{tr("A股组合回测")}</h1>
      <p className="text-[10px] text-ink-4">
        {tr("组合调仓引擎（batch10）：walk-forward 权重选股，按固定周期换仓，对照基准指数给出 组合/基准/超额三组指标。区别于「A股回测」页的单策略 composite 打分权重回测。 「个股权重」决定窗口内持仓如何分配（等权/风险平价/逆波动），与「A股回测」页的 composite 权重（那是给因子加权，不是给股票加权）是两个不同的轴。")}
      </p>

      <V17Card title={tr("回测设置")}>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">{tr("股票池")}</span>
            <select
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              value={pool}
              onChange={(e) => setPool(e.target.value)}
            >
              <option value="">{tr("自定义代码")}</option>
              <option value="hs300">{tr("沪深300")}</option>
              <option value="csi500">{tr("中证500")}</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">{tr("股票代码（逗号分隔）")}</span>
            <input
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              value={symbols}
              onChange={(e) => setSymbols(e.target.value)}
              placeholder="600519,000858"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">{tr("最大持仓数")}</span>
            <input
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              type="number"
              min="1"
              value={nLong}
              onChange={(e) => setNLong(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">{tr("调仓周期（交易日）")}</span>
            <input
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              type="number"
              min="1"
              value={rebalanceDays}
              onChange={(e) => setRebalanceDays(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">{tr("单边成本（bp）")}</span>
            <input
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              type="number"
              min="0"
              value={costBps}
              onChange={(e) => setCostBps(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">{tr("基准指数")}</span>
            <input
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              value={benchmarkIndex}
              onChange={(e) => setBenchmarkIndex(e.target.value)}
              placeholder="000300"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">{tr("个股权重")}</span>
            <select
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              value={stockWeight}
              onChange={(e) => setStockWeight(e.target.value)}
            >
              <option value="equal">{tr("等权")}</option>
              <option value="slsqp">{tr("风险平价（SLSQP）")}</option>
              <option value="inv_vol">{tr("逆波动")}</option>
            </select>
            {stockWeight === "slsqp" && Number(nLong) < 17 && (
              <span className="text-[9px] text-amber-400">
                {tr("持有数 < 17 时单票上限（6%）不可行，优化器将退回等权")}
              </span>
            )}
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">{tr("起始日期（留空=全部）")}</span>
            <input
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </label>
        </div>
        <div className="mt-2 flex items-center gap-2">
          <button
            className="rounded border border-border-soft bg-surface-2 px-3 py-1 text-[11px] text-ink disabled:opacity-50"
            disabled={running}
            onClick={run}
          >
            {running ? tr("回测中…") : tr("运行组合回测")}
          </button>
          {error && <span className="text-[10px] text-red-400">{error}</span>}
        </div>
      </V17Card>

      {result && (
        <>
          <V17Card title={tr("组合指标")}>
            <MetricsGrid metrics={result.portfolio_metrics} />
          </V17Card>
          <V17Card title={tr("基准指标")}>
            <MetricsGrid metrics={result.benchmark_metrics} />
          </V17Card>
          <V17Card title={tr("超额指标")}>
            <MetricsGrid metrics={result.excess_metrics} />
          </V17Card>
        </>
      )}
    </div>
  );
}

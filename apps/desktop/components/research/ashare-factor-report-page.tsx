"use client";

import { useEffect, useState } from "react";
import { apiDirectRaw, ApiError } from "@/lib/api-client";
import { MiniLineChart } from "@/components/research/diagnostic-charts";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// ashare router returns bare dicts on success (envelope only on error)
async function ashareGet<T>(path: string, init?: RequestInit): Promise<T> {
  const body = await apiDirectRaw<unknown>(path, init);
  if (body && typeof body === "object" && "data" in body && "meta" in body) {
    const envelope = body as { data: T | null; error?: { message?: string } };
    if (envelope.error) {
      throw new ApiError(envelope.error.message ?? "request failed", 500);
    }
    return envelope.data as T;
  }
  return body as T;
}

interface IcRow {
  factor: string;
  IC_mean?: number | null;
  IC_std?: number | null;
  IR?: number | null;
  IC_pos_rate?: number | null;
  t_stat?: number | null;
  IC_mean_neutral?: number | null;
  IR_neutral?: number | null;
  t_stat_neutral?: number | null;
}

interface QuantileRow {
  quantile: number;
  mean: number;
  std?: number;
  count?: number;
}

interface FactorReport {
  forward_col: string;
  n_days: number;
  n_symbols: number;
  ic_report: IcRow[];
  quantile_returns: Record<string, QuantileRow[]>;
  factor_correlation: Record<string, Record<string, number | null>>;
  ic_series?: { dates: string[]; series: Array<{ name: string; values: (number | null)[] }> };
  ic_all_nan?: boolean;
  ic_all_nan_reason?: string;
}

function num(v: number | null | undefined, digits = 3): string {
  return v == null || Number.isNaN(v) ? "—" : v.toFixed(digits);
}

function pctStr(v: number | null | undefined): string {
  return v == null || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(1)}%`;
}

/** IC 表：原始列与中性化列并排，便于对比中性化前后。 */
function IcTable({ rows, showNeutral }: { rows: IcRow[]; showNeutral: boolean }) {
  const tr = useUiText()

  if (rows.length === 0) {
    return <p className="py-2 text-xs text-muted-foreground">{tr("无因子")}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            <th className="py-1 pr-3">{tr("因子")}</th>
            <th className="py-1 pr-3">{tr("IC均值")}</th>
            {showNeutral && <th className="py-1 pr-3">{tr("IC均值(中性)")}</th>}
            <th className="py-1 pr-3">IR</th>
            {showNeutral && <th className="py-1 pr-3">{tr("IR(中性)")}</th>}
            <th className="py-1 pr-3">{tr("IC>0占比")}</th>
            <th className="py-1">t</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.factor} className="border-b last:border-0">
              <td className="py-1 pr-3 font-mono">{r.factor}</td>
              <td className="py-1 pr-3 font-mono">{num(r.IC_mean)}</td>
              {showNeutral && (
                <td className="py-1 pr-3 font-mono">{num(r.IC_mean_neutral)}</td>
              )}
              <td className="py-1 pr-3 font-mono">{num(r.IR)}</td>
              {showNeutral && (
                <td className="py-1 pr-3 font-mono">{num(r.IR_neutral)}</td>
              )}
              <td className="py-1 pr-3 font-mono">{pctStr(r.IC_pos_rate)}</td>
              <td className="py-1 font-mono">{num(r.t_stat, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 分位多空：每个因子一组 5 分位平均前向收益。 */
function QuantileSection({
  data,
}: {
  data: Record<string, QuantileRow[]>;
}) {
  const tr = useUiText()

  const entries = Object.entries(data).filter(([, rows]) => rows.length > 0);
  if (entries.length === 0) {
    return <p className="py-2 text-xs text-muted-foreground">{tr("暂无分位数据")}</p>;
  }
  return (
    <div className="space-y-3">
      {entries.map(([factor, rows]) => (
        <div key={factor}>
          <div className="mb-1 font-mono text-xs text-muted-foreground">{factor}</div>
          <div className="flex items-end gap-1">
            {rows.map((r) => {
              const vals = rows.map((x) => x.mean);
              const maxAbs = Math.max(
                1e-9,
                ...vals.map((v) => Math.abs(v)),
              );
              const h = Math.max(2, (Math.abs(r.mean) / maxAbs) * 44);
              return (
                <div
                  key={r.quantile}
                  className="flex flex-1 flex-col items-center gap-1"
                  title={`Q${r.quantile}: ${r.mean}`}
                >
                  <div
                    className={r.mean >= 0 ? "bg-emerald-500" : "bg-red-500"}
                    style={{ height: `${h}px`, width: "100%" }}
                    data-testid={`quantile-bar-${factor}-${r.quantile}`}
                  />
                  <span className="text-[9px] text-muted-foreground">
                    Q{r.quantile}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

/** 相关热图：因子两两 Spearman 相关（后端已 round(4)）。 */
function CorrelationHeatmap({
  corr,
}: {
  corr: Record<string, Record<string, number | null>>;
}) {
  const tr = useUiText()

  const names = Object.keys(corr);
  if (names.length === 0) {
    return <p className="py-2 text-xs text-muted-foreground">{tr("暂无相关矩阵")}</p>;
  }
  const cell = (v: number | null | undefined) => {
    if (v == null || Number.isNaN(v)) return "var(--color-border)";
    const a = 0.15 + 0.75 * Math.min(1, Math.abs(v));
    return v >= 0
      ? `rgba(22, 163, 74, ${a})`
      : `rgba(220, 38, 38, ${a})`;
  };
  return (
    <div className="overflow-x-auto">
      <table className="text-xs">
        <thead>
          <tr>
            <th />
            {names.map((c) => (
              <th key={c} className="px-1 pb-1 font-mono font-normal text-muted-foreground">
                {c.length > 7 ? `${c.slice(0, 7)}…` : c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {names.map((r) => (
            <tr key={r}>
              <td className="pr-2 text-right font-mono text-muted-foreground">{r}</td>
              {names.map((c) => (
                <td
                  key={c}
                  className="h-6 w-10 border border-background text-center font-mono"
                  style={{ background: cell(corr[r]?.[c]) }}
                  data-testid={`corr-${r}-${c}`}
                  title={`${r} × ${c}: ${corr[r]?.[c]}`}
                >
                  {corr[r]?.[c] == null ? "" : corr[r][c]!.toFixed(2)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** IC 时序：每个因子的逐日 IC 折线（后端截取最近约一年）。 */
function IcSeriesPanel({
  data,
}: {
  data: FactorReport["ic_series"];
}) {
  const tr = useUiText()

  const series = (data?.series ?? []).filter(
    (s) => s.values.filter((v) => v != null).length > 0,
  );
  if (!data || data.dates.length === 0 || series.length === 0) {
    return <p className="py-2 text-xs text-muted-foreground">{tr("暂无 IC 时序数据")}</p>;
  }
  const palette = ["#1d4ed8", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2"];
  return (
    <div className="space-y-2">
      <MiniLineChart
        dates={data.dates.map((d) => d.slice(0, 10))}
        series={series.slice(0, 6).map((s, i) => ({
          name: s.name,
          values: s.values,
          color: palette[i % palette.length],
        }))}
        height={140}
      />
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[9px] text-ink-3">
        {series.slice(0, 6).map((s, i) => (
          <span key={s.name} className="flex items-center gap-1">
            <span
              className="inline-block h-2 w-2 rounded-sm"
              style={{ background: palette[i % palette.length] }}
            />
            <span className="font-mono">{s.name}</span>
          </span>
        ))}
      </div>
      {series.length > 6 && (
        <p className="text-[9px] text-ink-4">{tr("仅展示前 6 个因子，共")} {series.length} {tr("个")}</p>
      )}
    </div>
  );
}

export function AshareFactorReportPage() {
  const tr = useUiText()

  const [symbols, setSymbols] = useState("600519,000858,000001");
  const [pool, setPool] = useState("");
  const [factors, setFactors] = useState("");
  const [forwardCol, setForwardCol] = useState("fwd_5d");
  const [neutralize, setNeutralize] = useState(true);
  const [startDate, setStartDate] = useState("");
  const [report, setReport] = useState<FactorReport | null>(null);
  const [running, setRunning] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The cache key is the full parameter tuple, so a refresh must replay the
  // params of the last successful run — using current form state would evict a
  // different entry and silently leave the stale report in place.
  const [lastParams, setLastParams] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const busy = running || refreshing;

  // The first build is ~60s; without a moving clock the user stares at a dead
  // button and cannot tell "working" from "hung". The counter resets when the
  // run starts (see `run`/`refresh`), so this effect only advances it.
  useEffect(() => {
    if (!busy) return;
    const id = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [busy]);

  const buildParams = (): string => {
    const params = new URLSearchParams({
      forward_col: forwardCol || "fwd_5d",
      neutralize: String(neutralize),
    });
    if (pool) params.set("pool", pool);
    else params.set("symbols", symbols);
    if (factors) params.set("factors", factors);
    if (startDate) params.set("start_date", startDate);
    return params.toString();
  };

  const run = async () => {
    const query = buildParams();
    setRunning(true);
    setError(null);
    setReport(null);
    setElapsed(0);
    try {
      const body = await ashareGet<FactorReport>(
        `/api/v1/ashare/factor-report?${query}`,
        { method: "POST" },
      );
      setReport(body);
      setLastParams(query);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const refresh = async () => {
    if (!lastParams) return;
    setRefreshing(true);
    setError(null);
    setElapsed(0);
    try {
      const body = await ashareGet<{ report?: FactorReport }>(
        `/api/v1/ashare/factor-report/refresh?${lastParams}`,
        { method: "POST" },
      );
      setReport(body.report ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div className="ashare-factor-report-page flex h-full flex-col gap-3 overflow-y-auto">
      <h1 className="text-sm font-semibold text-ink">{tr("A股因子 IC 报告")}</h1>
      <p className="text-[10px] text-ink-4">
        {tr("IC / 分位多空 / 相关热图。中性化开启时并排给出行业中性与原始统计，便于对比。 首次构建因子面板约 1 分钟（结果进程内缓存）。")}
      </p>

      <V17Card title={tr("报告设置")}>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-6">
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
              disabled={!!pool}
              placeholder="600519,000858"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">{tr("因子（留空=全部）")}</span>
            <input
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              value={factors}
              onChange={(e) => setFactors(e.target.value)}
              placeholder="RSI_14D,MOMENTUM_1M"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[9px] text-ink-3">{tr("前向列")}</span>
            <input
              className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[11px] text-ink"
              value={forwardCol}
              onChange={(e) => setForwardCol(e.target.value)}
              placeholder="fwd_5d"
            />
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
          <label className="flex flex-col justify-end gap-1">
            <span className="flex items-center gap-1 text-[10px] text-ink-3">
              <input
                type="checkbox"
                checked={neutralize}
                onChange={(e) => setNeutralize(e.target.checked)}
              />
              {tr("行业中性与原始并排")}
            </span>
          </label>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            className="rounded border border-border-soft bg-surface-2 px-3 py-1 text-[11px] text-ink disabled:opacity-50"
            disabled={busy}
            onClick={run}
          >
            {running ? tr("构建中…") : tr("生成 IC 报告")}
          </button>
          {lastParams && (
            <button
              className="rounded border border-border-soft bg-surface-2 px-3 py-1 text-[11px] text-ink disabled:opacity-50"
              disabled={busy}
              onClick={refresh}
              data-testid="factor-report-refresh"
              title={tr("丢弃缓存结果并用同一组参数重建")}
            >
              {refreshing ? tr("重算中…") : tr("刷新缓存")}
            </button>
          )}
          {busy && (
            <span
              className="flex items-center gap-1.5 text-[10px] text-ink-3"
              role="status"
              aria-live="polite"
              data-testid="factor-report-progress"
            >
              <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-gold motion-reduce:animate-none" />
              {tr("首次构建因子面板约 1 分钟…已")} {elapsed}s
            </span>
          )}
          {error && <span className="text-[10px] text-red-400">{error}</span>}
        </div>
      </V17Card>

      {report && (
        <>
          <V17Card title={tr("概览")}>
            <div className="grid grid-cols-3 gap-2 md:grid-cols-4">
              <StatBlock label={tr("股票数")} value={String(report.n_symbols)} />
              <StatBlock label={tr("交易日")} value={String(report.n_days)} />
              <StatBlock label={tr("因子数")} value={String(report.ic_report.length)} />
              <StatBlock label={tr("前向列")} value={report.forward_col} />
            </div>
            {report.ic_all_nan && (
              <p
                className="mt-2 text-[10px] text-amber-400"
                data-testid="ic-all-nan-note"
              >
                {report.ic_all_nan_reason}
              </p>
            )}
          </V17Card>

          <V17Card title={tr("IC 时序")}>
            <IcSeriesPanel data={report.ic_series} />
          </V17Card>

          <V17Card title={tr("IC 统计")}>
            <IcTable rows={report.ic_report} showNeutral={neutralize} />
          </V17Card>

          <V17Card title={tr("分位多空（五分位平均前向收益）")}>
            <QuantileSection data={report.quantile_returns} />
          </V17Card>

          <V17Card title={tr("因子相关矩阵")}>
            <CorrelationHeatmap corr={report.factor_correlation} />
          </V17Card>
        </>
      )}
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { Activity, AlertTriangle, Calculator, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getICPanel, mineICPanel } from "@/lib/api/market-analysis";
import type { ICPanelReport } from "@/lib/api/market-analysis";
import { fmtNum, HeatmapGrid, MiniLineChart, Stat } from "./diagnostic-charts";
import { useUiText } from "@/components/i18n/use-ui-text"

/** IC 诊断面板卡（跨截面 momentum）。快照契约:GET 只读,补算/刷新走 POST mine。 */
export function ICDiagnosticCard({ symbols, interval }: { symbols: string; interval: string }) {
  const tr = useUiText()

  const [report, setReport] = useState<ICPanelReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [mining, setMining] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mineError, setMineError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getICPanel({ symbols, interval })
      .then((r) => {
        if (!cancelled) {
          setReport(r);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "IC 面板加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbols, interval, reloadKey]);

  const noSnapshot = report?.status === "no_snapshot";
  const hasData = !noSnapshot && !!report && icOf(report).dates.length > 0;

  const mine = async () => {
    setMining(true);
    setMineError(null);
    try {
      const r = await mineICPanel({ symbols, interval });
      setReport(r);
    } catch (e: unknown) {
      setMineError(e instanceof Error ? e.message : "快照计算失败");
    } finally {
      setMining(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Activity className="h-4 w-4" />{tr("IC 诊断面板 · 跨截面动量")}
          {report?.snapshot && (
            <span
              className="font-mono text-[10px] text-muted-foreground"
              title={tr("快照 {id} · {n} 行原始值", { id: report.snapshot.episode_id, n: report.snapshot.n_rows }) + (report.snapshot.target_definition ? ` · target: ${report.snapshot.target_definition}` : "")}
            >
              {tr("快照")} {report.snapshot.occurred_at.replace("T", " ").slice(0, 16)}
              {report.snapshot.target_definition && (
                <span className="ml-1 text-[9px]">· {report.snapshot.target_definition}</span>
              )}
              {report.snapshot.lineage && report.snapshot.lineage.length > 0 && (
                <span
                  className="ml-1 text-[9px]"
                  title={tr("derived_from 祖先链: {chain}", { chain: report.snapshot.lineage.map((a) => `${a.episode_id}@${a.occurred_at}`).join(" → ") })}
                >
                  {tr("· 血缘")} {report.snapshot.lineage.length} {tr("代")}
                </span>
              )}
            </span>
          )}
          <span className="ml-auto font-mono text-xs text-muted-foreground">{symbols} · {interval}</span>
          {noSnapshot ? (
            <Button variant="outline" size="sm" className="h-6 text-xs" onClick={mine} disabled={mining}>
              {mining ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <Calculator className="h-3 w-3 mr-1" />}
              {tr("立即计算")}
            </Button>
          ) : (
            <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={mine} disabled={mining || loading}>
              {mining ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <RefreshCw className="h-3 w-3 mr-1" />}
              {tr("刷新")}
            </Button>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="py-4 text-center text-xs text-muted-foreground">{tr("读取快照中…")}</div>
        ) : error ? (
          <div className="flex items-center gap-2 py-4 text-xs text-amber-600">
            <AlertTriangle className="h-4 w-4" />{tr("IC 面板加载失败：")}{tr(error)}
          </div>
        ) : mineError ? (
          <div className="flex items-center gap-2 py-4 text-xs text-amber-600">
            <AlertTriangle className="h-4 w-4" />{tr("快照计算失败：")}{tr(mineError)}
          </div>
        ) : noSnapshot ? (
          <div className="py-4 text-xs text-muted-foreground">
            <p>{tr("该参数集暂无因子评估快照。")}</p>
            <p className="mt-1">{tr("点「立即计算」实时挖掘并落库；之后面板只从此快照重算，保证同参数结果可复现。")}</p>
          </div>
        ) : !report || !hasData ? (
          <p className="text-xs text-muted-foreground">{tr("快照存在但数据不足（需至少 2 个 symbol 对齐）")}</p>
        ) : (
          <ICReportView report={report} />
        )}
      </CardContent>
    </Card>
  );
}

function icOf(r: ICPanelReport) {
  return r.rolling_ic ?? { dates: [], values: [] };
}

function ICReportView({ report }: { report: ICPanelReport }) {
  const tr = useUiText()

  const d = report.ic_distribution ?? {};
  const qq = report.qq_stats ?? {};
  const ic = report.rolling_ic ?? { dates: [], values: [] };
  const ir = report.rolling_ir ?? { dates: [], values: [] };
  const autocorr = report.autocorr ?? { dates: [], values: [] };
  // 累计 IC：非 null 累积、null 断点输出 null（纯 reduce，避免渲染期变量重赋值）
  const cumValues = ic.values.reduce<{ sum: number; values: (number | null)[] }>(
    (acc, v) => {
      if (v == null) {
        acc.values.push(null);
      } else {
        acc.sum += v;
        acc.values.push(acc.sum);
      }
      return acc;
    },
    { sum: 0, values: [] },
  ).values;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <Stat label="Mean IC" value={fmtNum(d["Mean IC"])} />
        <Stat label="IR" value={fmtNum(d["IR"])} />
        <Stat label="Std" value={fmtNum(d["Std"])} />
        <Stat label="t-stat" value={fmtNum(d["t-stat"], 2)} />
      </div>
      <div>
        <p className="mb-1 text-[11px] font-medium text-muted-foreground">{tr("IC 轨迹")}</p>
        <MiniLineChart
          dates={ic.dates}
          series={[
            { name: "IC", values: ic.values, color: "#16a34a" },
            { name: "Rolling IR", values: ir.values, color: "#0f766e", dashed: true },
          ]}
        />
      </div>
      <div>
        <p className="mb-1 text-[11px] font-medium text-muted-foreground">{tr("累计 IC")}</p>
        <MiniLineChart dates={ic.dates} series={[{ name: "Cumulative IC", values: cumValues, color: "#0f766e" }]} />
      </div>
      <div>
        <p className="mb-1 text-[11px] font-medium text-muted-foreground">{tr("IC 自相关")}</p>
        <MiniLineChart dates={autocorr.dates} series={[{ name: "Self-Correlation", values: autocorr.values, color: "#0f766e" }]} />
      </div>
      <div>
        <p className="mb-1 text-[11px] font-medium text-muted-foreground">{tr("月度 IC 热图")}</p>
        <HeatmapGrid years={report.monthly_heatmap.years} months={report.monthly_heatmap.months} z={report.monthly_heatmap.z} />
      </div>
      <div className="text-xs text-muted-foreground">
        {tr("QQ 正态诊断：r =")} {fmtNum(qq["r_value"], 2)} · D&apos;Agostino-Pearson p = {fmtNum(qq["normality_p"], 3)}
      </div>
    </div>
  );
}

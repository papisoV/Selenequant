"use client";

import React, { useState, useCallback, useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api-client";
import { parseReturnsSeries, genSampleReturns } from "@/lib/returns-parse";
import {
  TrendingUp, TrendingDown, Activity, Gauge, Sigma, ShieldAlert, Download,
  Loader2, Sparkles, FileBarChart2, Ban, BarChart3, BarChart2,
} from "lucide-react";
import {
  CurveChart, MetricCard, MonthlyReturnsHeatmap, ReturnDistributionHistogram,
} from "@/components/strategy/shared-charts";
import { DrawdownChart } from "@/components/strategy/equity-curve-chart";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { useUiText } from "@/components/i18n/use-ui-text"
import { formatPending, type PendingMessage } from "@/lib/i18n/types"

// ---------------------------------------------------------------------------
// Types (mirror services/local-api/app/api/v1/analytics.py)
// ---------------------------------------------------------------------------

interface AnalyticsData {
  columns: string[];
  index: string[];
  data: number[][];
}

interface AnalyticsResponse {
  provider: string;
  kind: string;
  title: string;
  metrics: Record<string, number | string | boolean | null>;
  data: AnalyticsData | null;
  artifacts: Record<string, unknown> | null;
  computed_at: string;
  notes: string | null;
}

type TearsheetAction = "stats" | "cumulative" | "drawdown" | "rolling" | "distribution" | "html_report";
type ResultMap = Partial<Record<TearsheetAction, AnalyticsResponse | null>>;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function toTimestamps(index: string[]): number[] {
  const base = new Date("2024-01-01").getTime();
  return index.map((s, i) => {
    const t = Date.parse(s);
    return Number.isFinite(t) ? t : base + i * 86400_000;
  });
}

function extractSeries(data: AnalyticsData | null, col: string): number[] {
  if (!data) return [];
  const idx = data.columns.indexOf(col);
  if (idx < 0) return [];
  return data.data
    .map((row) => row[idx])
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
}

function extractBinCenters(data: AnalyticsData | null): { bin_center: number; count: number }[] {
  if (!data) return [];
  const ci = data.columns.indexOf("bin_center");
  const cc = data.columns.indexOf("count");
  if (ci < 0 || cc < 0) return [];
  return data.data
    .map((row) => ({ bin_center: row[ci], count: row[cc] }))
    .filter(
      (b): b is { bin_center: number; count: number } =>
        typeof b.bin_center === "number" &&
        Number.isFinite(b.bin_center) &&
        typeof b.count === "number" &&
        Number.isFinite(b.count),
    );
}

function fmtPct(v: number | string | boolean | null): string {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(2)}%`;
}

function fmtNum(v: number | string | boolean | null, digits = 2): string {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

function exportHtml(base64: string): void {
  try {
    const html = atob(base64);
    const blob = new Blob([html], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank");
  } catch {
    // decode failed — surface inline
  }
}

const METRIC_CARDS: {
  key: string;
  label: string;
  format: (v: number | string | boolean | null) => string;
  icon: React.ComponentType<{ className?: string }>;
  color: string;
}[] = [
  { key: "sharpe", label: "夏普比率", format: (v) => fmtNum(v), icon: Gauge, color: "text-blue-400" },
  { key: "sortino", label: "Sortino", format: (v) => fmtNum(v), icon: Gauge, color: "text-cyan-400" },
  { key: "volatility", label: "年化波动", format: fmtPct, icon: Activity, color: "text-amber-400" },
  { key: "max_drawdown", label: "最大回撤", format: fmtPct, icon: TrendingDown, color: "text-red-400" },
  { key: "cagr", label: "年化收益", format: fmtPct, icon: TrendingUp, color: "text-emerald-400" },
  { key: "calmar", label: "Calmar", format: (v) => fmtNum(v), icon: Sigma, color: "text-purple-400" },
];

const ROLLING_SERIES: { key: string; label: string; color: string }[] = [
  { key: "rolling_sharpe", label: "滚动夏普", color: "#3b82f6" },
  { key: "rolling_volatility", label: "滚动波动率", color: "#f59e0b" },
  { key: "rolling_beta", label: "滚动 β", color: "#a855f7" },
];

// ---------------------------------------------------------------------------
// TearsheetPanel
// ---------------------------------------------------------------------------

export function TearsheetPanel() {
  const tr = useUiText()

  const [returnsText, setReturnsText] = useState("");
  const [benchmarkText, setBenchmarkText] = useState("");
  const [generating, setGenerating] = useState(false);
  const [parseError, setParseError] = useState<PendingMessage | null>(null);
  const [results, setResults] = useState<ResultMap>({});
  const [exportError, setExportError] = useState(false);

  const hasResults = Object.keys(results).length > 0;

  const generate = useCallback(async () => {
    setParseError(null);
    setExportError(false);
    let returnsErrored = false;
    const returnsPayload = parseReturnsSeries(returnsText, (line, raw) => {
      returnsErrored = true;
      setParseError({ source: "Returns 第 {line} 行不是数字（\"{raw}\"），已改用示例数据", params: { line, raw } });
    }) ?? genSampleReturns(200);
    const benchmarkPayload = parseReturnsSeries(benchmarkText, (line, raw) => {
      if (!returnsErrored) setParseError({ source: "Benchmark 第 {line} 行不是数字（\"{raw}\"），已忽略基准", params: { line, raw } });
    });
    const params: Record<string, unknown> = {};

    setGenerating(true);
    setResults({});
    const actions: TearsheetAction[] = [
      "stats", "cumulative", "drawdown", "rolling", "distribution", "html_report",
    ];
    const settled = await Promise.allSettled(
      actions.map((action) =>
        api<AnalyticsResponse>("/api/v1/analytics/report", {
          method: "POST",
          body: JSON.stringify({
            provider: "quantstats",
            action,
            returns: returnsPayload,
            benchmark: benchmarkPayload ?? undefined,
            params,
          }),
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const next: ResultMap = {};
    actions.forEach((action, i) => {
      const r = settled[i];
      next[action] = r.status === "fulfilled" && r.value ? r.value : null;
    });
    setResults(next);
    setGenerating(false);
  }, [returnsText, benchmarkText]);

  // --- derived series ---
  const statsResp = results.stats;
  const cumulativeResp = results.cumulative;
  const drawdownResp = results.drawdown;
  const rollingResp = results.rolling;
  const distributionResp = results.distribution;
  const htmlResp = results.html_report;

  const cumulativeIndex = cumulativeResp?.data?.index ?? [];
  const strategyCum = extractSeries(cumulativeResp?.data ?? null, "strategy_cum");
  const benchmarkCum = extractSeries(cumulativeResp?.data ?? null, "benchmark_cum");
  const hasBenchmark = benchmarkCum.length > 0;
  const cumulativeTimestamps = toTimestamps(cumulativeIndex);

  const drawdownSeries = extractSeries(drawdownResp?.data ?? null, "drawdown");
  const drawdownTimestamps = toTimestamps(drawdownResp?.data?.index ?? []);

  const monthlyStart = cumulativeIndex[0] ?? null;
  const monthlyEnd = cumulativeIndex[cumulativeIndex.length - 1] ?? null;

  const rollingData = useMemo(() => {
    const idx = rollingResp?.data?.index ?? [];
    const ts = toTimestamps(idx);
    const out: Record<string, { date: number; value: number }[]> = {};
    for (const s of ROLLING_SERIES) {
      const values = extractSeries(rollingResp?.data ?? null, s.key);
      out[s.key] = values.map((v, i) => ({ date: ts[i] ?? i, value: v }));
    }
    return out;
  }, [rollingResp]);

  const distributionBins = extractBinCenters(distributionResp?.data ?? null);

  const htmlBase64 = (htmlResp?.artifacts?.html_base64 as string | undefined) ?? "";
  const computedAt = hasResults
    ? (cumulativeResp?.computed_at ?? statsResp?.computed_at ?? "")
    : "";

  const renderSkeleton = (h = 220) => <Skeleton className={`w-full rounded-lg`} style={{ height: h }} />;

  const renderEmpty = (msg: string, h = 220) => (
    <div
      className="flex items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground"
      style={{ height: h }}
    >
      {msg}
    </div>
  );

  return (
    <div className="space-y-4">
      {/* Header */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
          <div className="flex items-center gap-2">
            <FileBarChart2 className="h-5 w-5 text-emerald-400" />
            <CardTitle className="text-base">{tr("Tearsheet 研报")}</CardTitle>
            <Badge variant="outline" className="text-xs">
              {tr("quantstats · 研报级泪图")}
            </Badge>
          </div>
          {htmlBase64 && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                try {
                  exportHtml(htmlBase64);
                } catch {
                  setExportError(true);
                }
              }}
            >
              <Download className="h-4 w-4" />
              {tr("导出 HTML 泪图")}
            </Button>
          )}
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className="space-y-1">
              <label htmlFor="tearsheet-returns" className="block text-xs text-muted-foreground">
                {tr("Returns（每行一个数值，留空使用示例数据 200d）")}
              </label>
              <textarea
                id="tearsheet-returns"
                value={returnsText}
                onChange={(e) => setReturnsText(e.target.value)}
                placeholder="0.001&#10;0.002&#10;-0.003&#10;..."
                className="flex min-h-[90px] w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="tearsheet-benchmark" className="block text-xs text-muted-foreground">
                {tr("Benchmark（可选，用于基准对比与滚动 β）")}
              </label>
              <textarea
                id="tearsheet-benchmark"
                value={benchmarkText}
                onChange={(e) => setBenchmarkText(e.target.value)}
                placeholder={tr("留空则不对比基准\n0.0005\n-0.001\n...")}
                className="flex min-h-[90px] w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </div>
          </div>
          {parseError && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-400">
              {formatPending(parseError, tr)}
            </div>
          )}
          {exportError && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-400">
              {tr("HTML 泪图解码失败")}
            </div>
          )}
          <div className="flex items-center gap-3">
            <Button onClick={() => void generate()} disabled={generating} className="gap-2">
              {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
              {tr("生成 Tearsheet")}
            </Button>
            {computedAt && (
              <span className="text-xs text-muted-foreground">
                {tr("计算于")} {new Date(computedAt).toLocaleString()}
                {hasBenchmark ? tr(" · 含基准对比") : tr(" · 无基准")}
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* 1. Metrics strip */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Gauge className="h-4 w-4 text-blue-400" />
            {tr("核心指标")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {generating ? (
            renderSkeleton(72)
          ) : !statsResp ? (
            renderEmpty(tr("暂无数据 — 点击「生成 Tearsheet」"), 72)
          ) : (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
              {METRIC_CARDS.map((m) => (
                <MetricCard
                  key={m.key}
                  label={tr(m.label)}
                  value={m.format(statsResp.metrics?.[m.key] ?? null)}
                  icon={m.icon}
                  color={m.color}
                  hint={
                    // 年化触及 1e4 封顶 -> 明示,否则用户看到的是被静默改过的数字
                    m.key === "cagr" && statsResp.metrics?.annual_return_capped
                      ? tr("已封顶(1e4)")
                      : undefined
                  }
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* 2. Cumulative returns vs benchmark */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <TrendingUp className="h-4 w-4 text-emerald-400" />
            {tr("累计收益 vs 基准")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {generating ? (
            renderSkeleton()
          ) : !cumulativeResp ? (
            renderEmpty(tr("暂无数据"))
          ) : (
            <CurveChart
              curve={strategyCum}
              baselineCurve={hasBenchmark ? benchmarkCum : undefined}
              baselineLabel="Benchmark"
              emptyLabel="—"
              height={220}
              timestamps={cumulativeTimestamps}
            />
          )}
        </CardContent>
      </Card>

      {/* 3. Drawdown underwater */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <ShieldAlert className="h-4 w-4 text-red-400" />
            {tr("回撤水下曲线")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {generating ? (
            renderSkeleton()
          ) : !drawdownResp ? (
            renderEmpty(tr("暂无数据"))
          ) : drawdownSeries.length < 2 ? (
            renderEmpty(tr("无回撤数据"))
          ) : (
            <DrawdownChart
              data={drawdownSeries}
              timestamps={drawdownTimestamps}
              height={200}
            />
          )}
        </CardContent>
      </Card>

      {/* 4. Rolling stats */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Activity className="h-4 w-4 text-amber-400" />
            {tr("滚动统计（夏普 / 波动率 / β）")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {generating ? (
            renderSkeleton(160)
          ) : !rollingResp ? (
            renderEmpty(tr("暂无数据"), 160)
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              {ROLLING_SERIES.map((s) => {
                const rows = rollingData[s.key] ?? [];
                return (
                  <div key={s.key} className="rounded-lg border border-border/70 bg-card/50 p-2">
                    <div className="px-1 pb-1 text-[11px] uppercase tracking-wide text-muted-foreground">
                      {tr(s.label)}
                    </div>
                    {rows.length < 2 ? (
                      <p className="px-1 py-6 text-center text-xs text-muted-foreground">{tr("无数据")}</p>
                    ) : (
                      <div className="h-[140px] w-full" role="img" aria-label={tr("{label} 曲线", { label: s.label })}>
                        <ResponsiveContainer width="100%" height="100%">
                          <LineChart data={rows} margin={{ top: 5, right: 8, left: 0, bottom: 5 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                            <XAxis dataKey="date" hide />
                            <YAxis tick={{ fontSize: 9 }} stroke="hsl(var(--muted-foreground))" domain={["auto", "auto"]} width={38} />
                            <Tooltip
                              contentStyle={{
                                backgroundColor: "hsl(var(--popover))",
                                border: "1px solid hsl(var(--border))",
                                borderRadius: "6px",
                                fontSize: "11px",
                              }}
                            />
                            <Line
                              type="monotone"
                              dataKey="value"
                              stroke={s.color}
                              strokeWidth={1.5}
                              dot={false}
                            />
                          </LineChart>
                        </ResponsiveContainer>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* 5. Monthly returns heatmap */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <BarChart3 className="h-4 w-4 text-sky-400" />
            {tr("月度收益")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {generating ? (
            renderSkeleton(180)
          ) : !cumulativeResp ? (
            renderEmpty(tr("暂无数据"), 180)
          ) : (
            <MonthlyReturnsHeatmap
              curve={strategyCum}
              startDate={monthlyStart}
              endDate={monthlyEnd}
              emptyLabel={tr("暂无月度收益数据")}
              positiveLabel={tr("正收益")}
              negativeLabel={tr("负收益")}
            />
          )}
        </CardContent>
      </Card>

      {/* 6. Return distribution histogram */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <BarChart2 className="h-4 w-4 text-purple-400" />
            {tr("收益分布")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {generating ? (
            renderSkeleton(240)
          ) : !distributionResp ? (
            renderEmpty(tr("暂无数据"), 240)
          ) : (
            <ReturnDistributionHistogram
              bins={distributionBins}
              emptyLabel={tr("暂无分布数据")}
              height={240}
            />
          )}
        </CardContent>
      </Card>

      {/* footer */}
      {hasResults && (
        <div className="flex items-center gap-2 px-1 text-[11px] text-muted-foreground">
          <Ban className="h-3 w-3" />
          {tr("quantstats 研报排版 — 原创实现，未复制第三方源码")}
        </div>
      )}
    </div>
  );
}

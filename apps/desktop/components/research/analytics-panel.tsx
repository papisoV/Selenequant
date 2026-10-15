"use client";

import React, { useState, useCallback, useEffect, useMemo, TextareaHTMLAttributes } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
// Use native textarea via styled wrapper — no UI primitive needed
function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const { className = "", ...rest } = props;
  return (
    <textarea
      {...rest}
      className={`flex min-h-[60px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
    />
  );
}
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import {
  BarChart3, RefreshCw, Loader2, Activity, Settings2, Hash, CheckCircle2,
  XCircle,
} from "lucide-react";
import { api } from "@/lib/api-client";
import { TearsheetPanel } from "@/components/research/tearsheet-panel";
import { parseReturnsSeries, genSampleReturns } from "@/lib/returns-parse";

// ---------------------------------------------------------------------------
// Types (mirror services/local-api/app/api/v1/analytics.py)
// ---------------------------------------------------------------------------

interface ProviderInfo {
  provider: string;
  is_running: boolean;
  healthy: boolean | null;
}

interface ActionInfo {
  action: string;
  description: string;
}

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

const PROVIDER_LABEL: Record<string, string> = {
  quantstats: "QuantStats",
  pyportfolioopt: "PyPortfolioOpt",
  skfolio: "skfolio",
  statsmodels: "StatsModels",
  ffn: "FFN",
  tsmoothie: "Tsmoothie",
  gluonts: "GluonTS",
  finrl: "FinRL",
};

const PROVIDER_COLOR: Record<string, string> = {
  quantstats: "bg-blue-500/10 text-blue-400",
  pyportfolioopt: "bg-purple-500/10 text-purple-400",
  skfolio: "bg-cyan-500/10 text-cyan-400",
  statsmodels: "bg-emerald-500/10 text-emerald-400",
  ffn: "bg-amber-500/10 text-amber-400",
  tsmoothie: "bg-teal-500/10 text-teal-400",
  gluonts: "bg-rose-500/10 text-rose-400",
  finrl: "bg-orange-500/10 text-orange-400",
};

// ---------------------------------------------------------------------------
// AnalyticsPanel
// ---------------------------------------------------------------------------

export function AnalyticsPanel() {
  const [mode, setMode] = useState<"base" | "tearsheet">("base");
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loadingProviders, setLoadingProviders] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState<string>("quantstats");

  const [actions, setActions] = useState<ActionInfo[]>([]);
  const [loadingActions, setLoadingActions] = useState(false);
  const [selectedAction, setSelectedAction] = useState<string>("stats");

  const [report, setReport] = useState<AnalyticsResponse | null>(null);
  const [loadingReport, setLoadingReport] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const [returnsText, setReturnsText] = useState<string>("");
  const [paramsText, setParamsText] = useState<string>('{"risk_free_rate": 0.0}');
  const [parseError, setParseError] = useState<string | null>(null);

  // -----------------------------------------------------------------------
  // Load providers list on mount
  // -----------------------------------------------------------------------
  const loadProviders = useCallback(async () => {
    setLoadingProviders(true);
    try {
      const data = await api<ProviderInfo[]>("/api/v1/analytics/providers");
      if (data && Array.isArray(data)) {
        setProviders(data);
        if (data.length > 0 && !data.find((p) => p.provider === selectedProvider)) {
          setSelectedProvider(data[0].provider);
        }
      }
    } finally {
      setLoadingProviders(false);
    }
  }, [selectedProvider]);

  useEffect(() => {
    void loadProviders();
  }, [loadProviders]);

  // -----------------------------------------------------------------------
  // Load actions when provider changes
  // -----------------------------------------------------------------------
  const loadActions = useCallback(async () => {
    if (!selectedProvider) return;
    setLoadingActions(true);
    setActions([]);
    try {
      const data = await api<ActionInfo[]>(
        `/api/v1/analytics/${selectedProvider}/actions`,
      );
      if (data && Array.isArray(data)) {
        setActions(data);
        if (data.length > 0 && !data.find((a) => a.action === selectedAction)) {
          setSelectedAction(data[0].action);
        }
      }
    } finally {
      setLoadingActions(false);
    }
  }, [selectedProvider, selectedAction]);

  useEffect(() => {
    void loadActions();
  }, [loadActions]);

  // -----------------------------------------------------------------------
  // Parse returns textarea (CSV: one number per line)
  // -----------------------------------------------------------------------
  const parseReturns = useCallback((): { index: string[]; columns: { ret: number[] } } | null => {
    setParseError(null);
    if (!returnsText.trim()) {
      return genSampleReturns(200);
    }
    return parseReturnsSeries(returnsText, (line, raw) => {
      setParseError(`line ${line}: "${raw}" is not a number`);
    });
  }, [returnsText]);

  // -----------------------------------------------------------------------
  // Parse params JSON
  // -----------------------------------------------------------------------
  const parseParams = useCallback((): Record<string, unknown> => {
    setParseError(null);
    if (!paramsText.trim()) return {};
    try {
      return JSON.parse(paramsText);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setParseError(`params JSON: ${msg}`);
      return {};
    }
  }, [paramsText]);

  // -----------------------------------------------------------------------
  // Compute report
  // -----------------------------------------------------------------------
  const handleCompute = useCallback(async () => {
    if (!selectedProvider || !selectedAction) return;
    const returnsPayload = parseReturns();
    if (returnsPayload === null) return;
    const params = parseParams();
    setLoadingReport(true);
    setFetchError(null);
    setReport(null);
    try {
      const data = await api<AnalyticsResponse>("/api/v1/analytics/report", {
        method: "POST",
        body: JSON.stringify({
          provider: selectedProvider,
          action: selectedAction,
          returns: returnsPayload,
          params,
        }),
        headers: { "Content-Type": "application/json" },
      });
      if (data) {
        setReport(data);
      } else {
        setFetchError("无返回数据");
      }
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingReport(false);
    }
  }, [selectedProvider, selectedAction, parseReturns, parseParams]);

  // -----------------------------------------------------------------------
  // Chart data from report.data (single-column numeric)
  // -----------------------------------------------------------------------
  const chartData = useMemo(() => {
    if (!report?.data) return [];
    const { index, columns, data } = report.data;
    const numericCol = columns.findIndex((c) => /value|ret|forecast|smoothed|rolling|drawdown/i.test(c));
    const colIdx = numericCol >= 0 ? numericCol : 0;
    const out: { date: string; value: number }[] = [];
    for (let i = 0; i < data.length; i++) {
      const row = data[i];
      const v = Number(row[colIdx]);
      if (Number.isFinite(v)) {
        out.push({ date: index[i] ?? String(i), value: v });
      }
    }
    return out.slice(-365);
  }, [report]);

  const metricsEntries = useMemo(() => {
    if (!report?.metrics) return [];
    return Object.entries(report.metrics).filter(
      ([k, v]) => v !== null && v !== undefined && k !== "error",
    );
  }, [report]);

  return (
    <div className="space-y-4 p-4">
      {/* Mode toggle */}
      <div className="flex w-fit gap-1 rounded-lg border border-border/70 bg-card/60 p-1">
        <button
          type="button"
          aria-pressed={mode === "base"}
          onClick={() => setMode("base")}
          className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
            mode === "base"
              ? "bg-primary/15 text-primary border border-primary/30"
              : "text-muted-foreground hover:bg-muted hover:text-foreground border border-transparent"
          }`}
        >
          基础分析
        </button>
        <button
          type="button"
          aria-pressed={mode === "tearsheet"}
          onClick={() => setMode("tearsheet")}
          className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
            mode === "tearsheet"
              ? "bg-primary/15 text-primary border border-primary/30"
              : "text-muted-foreground hover:bg-muted hover:text-foreground border border-transparent"
          }`}
        >
          Tearsheet 研报
        </button>
      </div>

      {mode === "tearsheet" ? (
        <TearsheetPanel />
      ) : (
      <>
      {/* Header */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
          <div className="flex items-center gap-2">
            <BarChart3 className="h-5 w-5 text-blue-400" />
            <CardTitle className="text-base">Analytics（量化分析 8 库）</CardTitle>
            <Badge variant="outline" className="text-xs">
              批次 2 · selene_space_alpha.analytics
            </Badge>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void loadProviders()}
            disabled={loadingProviders}
          >
            <RefreshCw className={`h-4 w-4 ${loadingProviders ? "animate-spin" : ""}`} />
            刷新 Providers
          </Button>
        </CardHeader>
        <CardContent>
          {loadingProviders ? (
            <Skeleton className="h-10 w-full" />
          ) : providers.length === 0 ? (
            <div className="text-sm text-muted-foreground">
              未发现已注册 provider。请检查后端 dependencies.py 是否调用 set_analytics_service。
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {providers.map((p) => (
                <div
                  key={p.provider}
                  role="button"
                  tabIndex={0}
                  aria-pressed={selectedProvider === p.provider}
                  className={`rounded-md border p-3 cursor-pointer transition-colors ${
                    selectedProvider === p.provider
                      ? "border-blue-500 bg-blue-500/5"
                      : "border-border hover:border-muted-foreground"
                  }`}
                  onClick={() => setSelectedProvider(p.provider)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelectedProvider(p.provider);
                    }
                  }}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-sm font-semibold">
                      {PROVIDER_LABEL[p.provider] ?? p.provider}
                    </span>
                    {p.healthy === true && <CheckCircle2 className="h-3 w-3 text-emerald-400" />}
                    {p.healthy === false && <XCircle className="h-3 w-3 text-red-400" />}
                  </div>
                  <Badge
                    variant="secondary"
                    className={`mt-1 ${PROVIDER_COLOR[p.provider] ?? "bg-zinc-500/10 text-zinc-400"}`}
                  >
                    {p.is_running ? "running" : "stopped"}
                  </Badge>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Action selector + compute */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Settings2 className="h-4 w-4" />
            选择 Provider / Action
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Select value={selectedProvider} onValueChange={setSelectedProvider}>
              <SelectTrigger className="w-[180px]" aria-label="选择 Provider">
                <SelectValue placeholder="Provider" />
              </SelectTrigger>
              <SelectContent>
                {providers.map((p) => (
                  <SelectItem key={p.provider} value={p.provider}>
                    {PROVIDER_LABEL[p.provider] ?? p.provider}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={selectedAction} onValueChange={setSelectedAction}>
              <SelectTrigger className="flex-1" aria-label="选择 Action">
                <SelectValue placeholder="Action" />
              </SelectTrigger>
              <SelectContent>
                {loadingActions ? (
                  <SelectItem value="_loading" disabled>加载中...</SelectItem>
                ) : (
                  actions.map((a) => (
                    <SelectItem key={a.action} value={a.action}>
                      <span className="font-mono">{a.action}</span>
                      <span className="ml-2 text-xs text-muted-foreground">
                        · {a.description}
                      </span>
                    </SelectItem>
                  ))
                )}
              </SelectContent>
            </Select>

            <Button onClick={() => void handleCompute()} disabled={loadingReport}>
              {loadingReport ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Activity className="h-4 w-4" />
              )}
              计算
            </Button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className="space-y-1">
              <label htmlFor="analytics-returns" className="block text-xs text-muted-foreground">
                Returns（每行一个数值，留空使用示例数据 200d）
              </label>
              <Textarea
                id="analytics-returns"
                value={returnsText}
                onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setReturnsText(e.target.value)}
                placeholder="0.001&#10;0.002&#10;-0.003&#10;..."
                className="font-mono text-xs h-[100px]"
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="analytics-params" className="block text-xs text-muted-foreground">Params（JSON）</label>
              <Textarea
                id="analytics-params"
                value={paramsText}
                onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setParamsText(e.target.value)}
                placeholder='{"risk_free_rate": 0.0}'
                className="font-mono text-xs h-[100px]"
              />
            </div>
          </div>

          {parseError && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-400">
              {parseError}
            </div>
          )}
          {fetchError && (
            <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-xs text-red-400">
              {fetchError}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Report */}
      {report && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="flex items-center gap-2 text-base">
                <Badge className={PROVIDER_COLOR[report.provider] ?? "bg-zinc-500/10 text-zinc-400"}>
                  {PROVIDER_LABEL[report.provider] ?? report.provider}
                </Badge>
                <span className="font-mono">{report.title}</span>
              </CardTitle>
              <div className="text-xs text-muted-foreground">
                {new Date(report.computed_at).toLocaleString()}
              </div>
            </div>
            {report.notes && (
              <div className="text-sm text-muted-foreground">{report.notes}</div>
            )}
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Metrics */}
            {metricsEntries.length > 0 && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                {metricsEntries.map(([k, v]) => (
                  <MetricBlock key={k} label={k} value={formatMetric(v)} />
                ))}
              </div>
            )}

            {/* Chart */}
            {chartData.length > 0 ? (
              <div className="h-[300px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 11 }}
                      stroke="hsl(var(--muted-foreground))"
                    />
                    <YAxis
                      tick={{ fontSize: 11 }}
                      stroke="hsl(var(--muted-foreground))"
                      domain={["auto", "auto"]}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "hsl(var(--popover))",
                        border: "1px solid hsl(var(--border))",
                        borderRadius: "6px",
                        fontSize: "12px",
                      }}
                    />
                    <Line
                      type="monotone"
                      dataKey="value"
                      stroke="#3b82f6"
                      strokeWidth={1.5}
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ) : (
              report.data && <div className="text-sm text-muted-foreground">无可用数值序列</div>
            )}

            {/* Data table (last 20 rows) */}
            {report.data && report.data.data.length > 0 && (
              <div className="rounded-md border">
                <div className="border-b px-3 py-2 text-xs text-muted-foreground">
                  最近 20 行
                </div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[160px]">index</TableHead>
                      {report.data.columns.map((c, i) => (
                        <TableHead key={i}>{c}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {report.data.data.slice(-20).reverse().map((row, i) => (
                      <TableRow key={i}>
                        <TableCell className="font-mono text-xs">
                          {report.data!.index[report.data!.data.length - 1 - i] ?? ""}
                        </TableCell>
                        {row.map((v, j) => (
                          <TableCell key={j} className="font-mono text-xs">
                            {Number.isFinite(v) ? v.toFixed(6) : String(v)}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      )}
      </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatMetric(v: number | string | boolean | null): string {
  if (typeof v === "number") {
    if (Number.isFinite(v)) {
      return Math.abs(v) >= 1 || v === 0 ? v.toFixed(4) : v.toFixed(6);
    }
    return String(v);
  }
  return String(v);
}

function MetricBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border bg-card/50 p-2">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
        <Hash className="h-3 w-3" />
        {label}
      </div>
      <div className="mt-1 font-mono text-sm font-semibold">{value}</div>
    </div>
  );
}

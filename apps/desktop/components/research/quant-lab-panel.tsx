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
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  Scatter, ScatterChart, ZAxis,
} from "recharts";
import {
  Brain, RefreshCw, Loader2, Activity, Settings2, Hash, Cpu, Gauge, TrendingUp,
} from "lucide-react";
import { api } from "@/lib/api-client";
import { useWorkflowStore } from "@/lib/store/workflow";
import { useUiText } from "@/components/i18n/use-ui-text"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ProviderInfo {
  provider: string;
  is_running: boolean;
  healthy: boolean | null;
  kind: "rl" | "portfolio";
}

interface ActionInfo {
  action: string;
  description: string;
}

interface OptimizeData {
  columns: string[];
  index: string[];
  data: number[][];
}

interface OptimizeResponse {
  provider: string;
  kind: string;
  title: string;
  metrics: Record<string, number | string | boolean | null>;
  data: OptimizeData | null;
  computed_at: string;
  notes: string | null;
}

const PROVIDER_LABEL: Record<string, string> = {
  ppo: "PPO", dqn: "DQN", a2c: "A2C", sac: "SAC", td3: "TD3",
  mean_variance: "Mean-Variance", risk_parity: "Risk Parity", enhanced_index: "Enhanced Index",
};

const KIND_COLOR: Record<string, string> = {
  rl: "bg-blue-500/10 text-blue-400",
  portfolio: "bg-purple-500/10 text-purple-400",
};

function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const { className = "", ...rest } = props;
  return (
    <textarea
      {...rest}
      className={`flex min-h-[60px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
    />
  );
}

// Deterministic PRNG for sample data
function genSamplePrices(n: number, nAssets = 3): { index: string[]; columns: Record<string, number[]> } {
  const start = new Date("2024-01-01T00:00:00Z");
  const index: string[] = [];
  for (let i = 0; i < n; i++) {
    const d = new Date(start.getTime() + i * 86400_000);
    index.push(d.toISOString().slice(0, 10));
  }
  const cols: Record<string, number[]> = {};
  const names = ["BTC", "ETH", "SOL"].slice(0, nAssets);
  let seed = 42;
  for (const name of names) {
    const arr: number[] = new Array(n);
    let price = name === "BTC" ? 100 : name === "ETH" ? 50 : 30;
    for (let i = 0; i < n; i++) {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      const u = seed / 0x7fffffff;
      const drift = name === "BTC" ? 0.001 : name === "ETH" ? 0.0012 : 0.0015;
      const vol = name === "BTC" ? 0.02 : name === "ETH" ? 0.025 : 0.03;
      price *= 1 + drift + vol * (u - 0.5);
      arr[i] = price;
    }
    cols[name] = arr;
  }
  return { index, columns: cols };
}

function genSamplePriceSeries(n: number): { index: string[]; columns: { price: number[] } } {
  const start = new Date("2024-01-01T00:00:00Z");
  const index: string[] = [];
  let price = 100;
  let seed = 42;
  const arr: number[] = new Array(n);
  for (let i = 0; i < n; i++) {
    const d = new Date(start.getTime() + i * 86400_000);
    index.push(d.toISOString().slice(0, 10));
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    const u = seed / 0x7fffffff;
    price *= 1 + 0.002 + 0.015 * (u - 0.5);
    arr[i] = price;
  }
  return { index, columns: { price: arr } };
}

export function QuantLabPanel() {
  const tr = useUiText()

  const quantLab = useWorkflowStore((s) => s.quantLab);
  const setQuantLab = useWorkflowStore((s) => s.setQuantLab);

  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loadingProviders, setLoadingProviders] = useState(false);
  const selectedProvider: string = quantLab?.selectedProvider ?? "ppo";
  const setSelectedProvider = (v: string) => setQuantLab({ selectedProvider: v });

  const [actions, setActions] = useState<ActionInfo[]>([]);
  const [loadingActions, setLoadingActions] = useState(false);
  const selectedAction: string = quantLab?.selectedAction ?? "train";
  const setSelectedAction = (v: string) => setQuantLab({ selectedAction: v });

  const report: OptimizeResponse | null = (quantLab?.report ?? null) as OptimizeResponse | null;
  const setReport = (v: OptimizeResponse | null) => setQuantLab({ report: v });
  const [loadingReport, setLoadingReport] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const pricesText: string = quantLab?.pricesText ?? "";
  const setPricesText = (v: string) => setQuantLab({ pricesText: v });
  const paramsText: string = quantLab?.paramsText ?? '{"total_timesteps": 3000, "window": 10}';
  const setParamsText = (v: string) => setQuantLab({ paramsText: v });
  const [parseError, setParseError] = useState<string | null>(null);

  const loadProviders = useCallback(async () => {
    setLoadingProviders(true);
    try {
      const data = await api<ProviderInfo[]>("/api/v1/optimize/providers");
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

  useEffect(() => { void loadProviders(); }, [loadProviders]);

  const loadActions = useCallback(async () => {
    if (!selectedProvider) return;
    setLoadingActions(true);
    setActions([]);
    try {
      const data = await api<ActionInfo[]>(`/api/v1/optimize/${selectedProvider}/actions`);
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

  useEffect(() => { void loadActions(); }, [loadActions]);

  const isRL = useMemo(() => ["ppo", "dqn", "a2c", "sac", "td3"].includes(selectedProvider), [selectedProvider]);

  const parsePrices = useCallback((): { index: string[]; columns: Record<string, number[]> } | null => {
    setParseError(null);
    if (!pricesText.trim()) {
      // Use sample data — multi-asset for portfolio, single for RL
      return isRL ? genSamplePriceSeries(200) : genSamplePrices(200, 3);
    }
    try {
      const parsed = JSON.parse(pricesText);
      if (!parsed.index || !parsed.columns) {
        throw new Error("expected {index: [...], columns: {name: [values]}}");
      }
      return parsed;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setParseError(`prices JSON: ${msg}`);
      return null;
    }
  }, [pricesText, isRL]);

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

  const handleCompute = useCallback(async () => {
    if (!selectedProvider || !selectedAction) return;
    const pricesPayload = parsePrices();
    if (pricesPayload === null) return;
    const params = parseParams();
    setLoadingReport(true);
    setFetchError(null);
    setReport(null);
    try {
      const data = await api<OptimizeResponse>("/api/v1/optimize/report", {
        method: "POST",
        body: JSON.stringify({
          provider: selectedProvider,
          action: selectedAction,
          prices: pricesPayload,
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
  }, [selectedProvider, selectedAction, parsePrices, parseParams]);

  // Chart data — for efficient_frontier, draw scatter; for predict, draw action/reward line
  const frontierData = useMemo(() => {
    if (!report?.data || selectedAction !== "efficient_frontier") return [];
    const { columns, data } = report.data;
    const volIdx = columns.indexOf("volatility");
    const retIdx = columns.indexOf("return");
    const sharpeIdx = columns.indexOf("sharpe");
    if (volIdx < 0 || retIdx < 0) return [];
    return data.map((row) => ({
      volatility: row[volIdx],
      return: row[retIdx],
      sharpe: sharpeIdx >= 0 ? row[sharpeIdx] : 0,
    }));
  }, [report, selectedAction]);

  const lineData = useMemo(() => {
    if (!report?.data) return [];
    const { index, columns, data } = report.data;
    const numericCol = columns.findIndex((c) => /weight|reward|action|forecast|smoothed|return/i.test(c));
    const colIdx = numericCol >= 0 ? numericCol : 0;
    const out: { date: string; value: number }[] = [];
    for (let i = 0; i < data.length; i++) {
      const v = Number(data[i][colIdx]);
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
      {/* Header */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
          <div className="flex items-center gap-2">
            <Brain className="h-5 w-5 text-blue-400" />
            <CardTitle className="text-base">{tr("Quant Lab（RL + 组合优化）")}</CardTitle>
            <Badge variant="outline" className="text-xs">
              {tr("批次 3 · selene_space_optimize")}
            </Badge>
          </div>
          <Button variant="ghost" size="sm" onClick={() => void loadProviders()} disabled={loadingProviders}>
            <RefreshCw className={`h-4 w-4 ${loadingProviders ? "animate-spin" : ""}`} />
            {tr("刷新 Providers")}
          </Button>
        </CardHeader>
        <CardContent>
          {loadingProviders ? (
            <Skeleton className="h-10 w-full" />
          ) : providers.length === 0 ? (
            <div className="text-sm text-muted-foreground">
              {tr("未发现已注册 provider。请检查后端 dependencies.py 是否调用 set_optimize_service。")}
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {providers.map((p) => (
                <div
                  key={p.provider}
                  className={`rounded-md border p-3 cursor-pointer transition-colors ${
                    selectedProvider === p.provider
                      ? "border-blue-500 bg-blue-500/5"
                      : "border-border hover:border-muted-foreground"
                  }`}
                  onClick={() => setSelectedProvider(p.provider)}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-sm font-semibold">
                      {PROVIDER_LABEL[p.provider] ?? p.provider}
                    </span>
                    <Badge variant="secondary" className={KIND_COLOR[p.kind] ?? "bg-zinc-500/10 text-zinc-400"}>
                      {p.kind === "rl" ? <Cpu className="h-3 w-3 mr-1" /> : <Gauge className="h-3 w-3 mr-1" />}
                      {p.kind}
                    </Badge>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {p.healthy === true ? "healthy" : p.healthy === false ? "unhealthy" : "—"}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Action selector */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Settings2 className="h-4 w-4" />
            {tr("选择 Provider / Action")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Select value={selectedProvider} onValueChange={setSelectedProvider}>
              <SelectTrigger className="w-[180px]">
                <SelectValue placeholder="Provider" />
              </SelectTrigger>
              <SelectContent>
                {providers.map((p) => (
                  <SelectItem key={p.provider} value={p.provider}>
                    {PROVIDER_LABEL[p.provider] ?? p.provider}
                    <span className="ml-2 text-xs text-muted-foreground">· {p.kind}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={selectedAction} onValueChange={setSelectedAction}>
              <SelectTrigger className="flex-1">
                <SelectValue placeholder="Action" />
              </SelectTrigger>
              <SelectContent>
                {loadingActions ? (
                  <SelectItem value="_loading" disabled>{tr("加载中...")}</SelectItem>
                ) : (
                  actions.map((a) => (
                    <SelectItem key={a.action} value={a.action}>
                      <span className="font-mono">{a.action}</span>
                      <span className="ml-2 text-xs text-muted-foreground">· {a.description}</span>
                    </SelectItem>
                  ))
                )}
              </SelectContent>
            </Select>

            <Button onClick={() => void handleCompute()} disabled={loadingReport}>
              {loadingReport ? <Loader2 className="h-4 w-4 animate-spin" /> : <Activity className="h-4 w-4" />}
              {isRL && selectedAction === "train" ? tr("训练") : tr("计算")}
            </Button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className="space-y-1">
              <div className="text-xs text-muted-foreground">
                {tr("Prices JSON（留空使用示例数据 200d）")}
              </div>
              <Textarea
                value={pricesText}
                onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setPricesText(e.target.value)}
                placeholder='{"index":["2024-01-01"],"columns":{"BTC":[100]}}'
                className="font-mono text-xs h-[100px]"
              />
            </div>
            <div className="space-y-1">
              <div className="text-xs text-muted-foreground">Params（JSON）</div>
              <Textarea
                value={paramsText}
                onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setParamsText(e.target.value)}
                placeholder='{"total_timesteps": 3000, "window": 10}'
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
              {tr(fetchError)}
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
                <Badge className={KIND_COLOR[report.kind.includes("rl") ? "rl" : "portfolio"] ?? "bg-zinc-500/10 text-zinc-400"}>
                  {PROVIDER_LABEL[report.provider] ?? report.provider}
                </Badge>
                <span className="font-mono">{report.title}</span>
              </CardTitle>
              <div className="text-xs text-muted-foreground">
                {new Date(report.computed_at).toLocaleString()}
              </div>
            </div>
            {report.notes && <div className="text-sm text-muted-foreground">{report.notes}</div>}
          </CardHeader>
          <CardContent className="space-y-4">
            {metricsEntries.length > 0 && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                {metricsEntries.map(([k, v]) => (
                  <MetricBlock key={k} label={k} value={formatMetric(v)} />
                ))}
              </div>
            )}

            {/* Frontier scatter */}
            {frontierData.length > 0 ? (
              <div className="h-[300px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <ScatterChart margin={{ top: 20, right: 20, left: 0, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                    <XAxis
                      type="number"
                      dataKey="volatility"
                      name="Volatility"
                      tick={{ fontSize: 11 }}
                      stroke="hsl(var(--muted-foreground))"
                    />
                    <YAxis
                      type="number"
                      dataKey="return"
                      name="Return"
                      tick={{ fontSize: 11 }}
                      stroke="hsl(var(--muted-foreground))"
                    />
                    <ZAxis type="number" dataKey="sharpe" name="Sharpe" />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "hsl(var(--popover))",
                        border: "1px solid hsl(var(--border))",
                        borderRadius: "6px",
                        fontSize: "12px",
                      }}
                    />
                    <Scatter data={frontierData} fill="#3b82f6" />
                  </ScatterChart>
                </ResponsiveContainer>
              </div>
            ) : lineData.length > 0 ? (
              <div className="h-[300px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={lineData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
                    <YAxis tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" domain={["auto", "auto"]} />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "hsl(var(--popover))",
                        border: "1px solid hsl(var(--border))",
                        borderRadius: "6px",
                        fontSize: "12px",
                      }}
                    />
                    <Line type="monotone" dataKey="value" stroke="#3b82f6" strokeWidth={1.5} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ) : null}

            {/* Data table */}
            {report.data && report.data.data.length > 0 && (
              <div className="rounded-md border">
                <div className="border-b px-3 py-2 text-xs text-muted-foreground">
                  {tr("最近 20 行")}
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
    </div>
  );
}

function formatMetric(v: number | string | boolean | null): string {
  if (typeof v === "boolean") return v ? "true" : "false";
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

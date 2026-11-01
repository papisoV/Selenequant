"use client";

import React, { useState, useCallback, useEffect, useMemo } from "react";
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
  Landmark, RefreshCw, Loader2, Database, Activity, Calendar, Hash,
} from "lucide-react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { api } from "@/lib/api-client";
import { useUiText } from "@/components/i18n/use-ui-text"

// ---------------------------------------------------------------------------
// Types (mirror services/local-api/app/api/v1/macrodata.py + selene_space_macrodata.source)
// ---------------------------------------------------------------------------

interface ProviderInfo {
  provider: string;
  is_running: boolean;
  series_count: number;
}

interface SeriesCatalogItem {
  series_id: string;
  kind: string;
  description: string;
}

interface SeriesObservation {
  date: string;
  value: number;
}

interface MacroSeriesResponse {
  provider: string;
  series_id: string;
  kind: string;
  description: string;
  observations: SeriesObservation[];
  fetched_at: string;
  source_url: string | null;
}

const KIND_LABEL: Record<string, string> = {
  rates: "利率/收益率",
  inflation: "通胀",
  employment: "就业",
  monetary: "货币",
  financial_stress: "金融压力",
  confidence: "信心",
  flow: "宏观流量",
  fx: "汇率",
  other: "其他",
};

const KIND_COLOR: Record<string, string> = {
  rates: "bg-blue-500/10 text-blue-400",
  inflation: "bg-amber-500/10 text-amber-400",
  employment: "bg-emerald-500/10 text-emerald-400",
  monetary: "bg-purple-500/10 text-purple-400",
  financial_stress: "bg-red-500/10 text-red-400",
  confidence: "bg-cyan-500/10 text-cyan-400",
  flow: "bg-orange-500/10 text-orange-400",
  fx: "bg-teal-500/10 text-teal-400",
  other: "bg-zinc-500/10 text-zinc-400",
};

export function MacroPanel() {
  const tr = useUiText()

  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loadingProviders, setLoadingProviders] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState<string>("fred");

  const [catalog, setCatalog] = useState<SeriesCatalogItem[]>([]);
  const [loadingCatalog, setLoadingCatalog] = useState(false);

  const [selectedSeries, setSelectedSeries] = useState<string>("DGS10");
  const [series, setSeries] = useState<MacroSeriesResponse | null>(null);
  const [loadingSeries, setLoadingSeries] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  // -----------------------------------------------------------------------
  // Load providers list on mount
  // -----------------------------------------------------------------------
  const loadProviders = useCallback(async () => {
    setLoadingProviders(true);
    try {
      const data = await api<ProviderInfo[]>("/api/v1/macrodata/providers");
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
  // Load catalog when provider changes
  // -----------------------------------------------------------------------
  const loadCatalog = useCallback(async () => {
    if (!selectedProvider) return;
    setLoadingCatalog(true);
    setCatalog([]);
    try {
      const data = await api<SeriesCatalogItem[]>(
        `/api/v1/macrodata/${selectedProvider}/series`,
      );
      if (data && Array.isArray(data)) {
        setCatalog(data);
        if (data.length > 0 && !data.find((s) => s.series_id === selectedSeries)) {
          setSelectedSeries(data[0].series_id);
        }
      }
    } finally {
      setLoadingCatalog(false);
    }
  }, [selectedProvider, selectedSeries]);

  useEffect(() => {
    void loadCatalog();
  }, [loadCatalog]);

  // -----------------------------------------------------------------------
  // Fetch series observations
  // -----------------------------------------------------------------------
  const handleFetch = useCallback(async () => {
    if (!selectedProvider || !selectedSeries) return;
    setLoadingSeries(true);
    setFetchError(null);
    setSeries(null);
    try {
      const data = await api<MacroSeriesResponse>(
        `/api/v1/macrodata/${selectedProvider}/series/${encodeURIComponent(selectedSeries)}`,
      );
      if (data) {
        setSeries(data);
      } else {
        setFetchError("无数据返回（可能未配置 API key 或上游不可达，已 fallback 到空数据）");
      }
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingSeries(false);
    }
  }, [selectedProvider, selectedSeries]);

  // Auto-fetch on series change
  useEffect(() => {
    void handleFetch();
  }, [handleFetch]);

  // -----------------------------------------------------------------------
  // Chart data
  // -----------------------------------------------------------------------
  const chartData = useMemo(() => {
    if (!series?.observations) return [];
    return series.observations
      .map((o) => ({ date: o.date, value: Number(o.value) }))
      .filter((o) => Number.isFinite(o.value))
      .slice(-180);  // last 180 points
  }, [series]);

  const stats = useMemo(() => {
    if (chartData.length === 0) return null;
    const values = chartData.map((d) => d.value);
    const latest = values[values.length - 1];
    const first = values[0];
    const min = Math.min(...values);
    const max = Math.max(...values);
    const change = first !== 0 ? ((latest - first) / first) * 100 : 0;
    return { latest, first, min, max, change, count: values.length };
  }, [chartData]);

  return (
    <div className="space-y-4 p-4">
      {/* Header */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
          <div className="flex items-center gap-2">
            <Landmark className="h-5 w-5 text-blue-400" />
            <CardTitle className="text-base">{tr("宏观数据（FRED / BIS / BLS）")}</CardTitle>
            <Badge variant="outline" className="text-xs">
              {tr("批次 1 · selene_space_macrodata")}
            </Badge>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void loadProviders()}
            disabled={loadingProviders}
          >
            <RefreshCw className={`h-4 w-4 ${loadingProviders ? "animate-spin" : ""}`} />
            {tr("刷新 Providers")}
          </Button>
        </CardHeader>
        <CardContent>
          {loadingProviders ? (
            <Skeleton className="h-10 w-full" />
          ) : providers.length === 0 ? (
            <div className="text-sm text-muted-foreground">
              {tr("未发现已注册 provider。请检查后端 dependencies.py 是否调用 set_macrodata_service。")}
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
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
                    <span className="font-mono text-sm font-semibold uppercase">
                      {p.provider}
                    </span>
                    <Badge
                      variant="secondary"
                      className={p.is_running ? "bg-emerald-500/10 text-emerald-400" : "bg-zinc-500/10 text-zinc-400"}
                    >
                      {p.is_running ? "running" : "stopped"}
                    </Badge>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {p.series_count} {tr("个 series")}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Series selector + fetch */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Database className="h-4 w-4" />
            {tr("选择 Series")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Select value={selectedProvider} onValueChange={setSelectedProvider}>
              <SelectTrigger className="w-[140px]">
                <SelectValue placeholder="Provider" />
              </SelectTrigger>
              <SelectContent>
                {providers.map((p) => (
                  <SelectItem key={p.provider} value={p.provider}>
                    {p.provider.toUpperCase()}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={selectedSeries} onValueChange={setSelectedSeries}>
              <SelectTrigger className="flex-1">
                <SelectValue placeholder="Series ID" />
              </SelectTrigger>
              <SelectContent>
                {loadingCatalog ? (
                  <SelectItem value="_loading" disabled>
                    {tr("加载中...")}
                  </SelectItem>
                ) : (
                  catalog.map((s) => (
                    <SelectItem key={s.series_id} value={s.series_id}>
                      <span className="font-mono">{s.series_id}</span>
                      <span className="ml-2 text-xs text-muted-foreground">
                        · {tr(KIND_LABEL[s.kind] ?? s.kind)} · {s.description}
                      </span>
                    </SelectItem>
                  ))
                )}
              </SelectContent>
            </Select>

            <Button onClick={() => void handleFetch()} disabled={loadingSeries}>
              {loadingSeries ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Activity className="h-4 w-4" />
              )}
              {tr("拉取")}
            </Button>
          </div>

          {fetchError && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-400">
              {tr(fetchError)}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Series result */}
      {series && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="flex items-center gap-2 text-base">
                <Badge className={KIND_COLOR[series.kind] ?? KIND_COLOR.other}>
                  {tr(KIND_LABEL[series.kind] ?? series.kind)}
                </Badge>
                <span className="font-mono">{series.series_id}</span>
              </CardTitle>
              <div className="text-xs text-muted-foreground">
                fetched: {new Date(series.fetched_at).toLocaleString()}
              </div>
            </div>
            <div className="text-sm text-muted-foreground">{series.description}</div>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Stats */}
            {stats && (
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
                <StatBlock label={tr("最新值")} value={stats.latest?.toFixed(4) ?? "—"} icon={<Hash className="h-3 w-3" />} />
                <StatBlock
                  label={tr("区间变动")}
                  value={`${(stats.change ?? 0) >= 0 ? "+" : ""}${stats.change?.toFixed(2) ?? "—"}%`}
                  tone={(stats.change ?? 0) >= 0 ? "up" : "down"}
                />
                <StatBlock label={tr("最低")} value={stats.min?.toFixed(4) ?? "—"} />
                <StatBlock label={tr("最高")} value={stats.max?.toFixed(4) ?? "—"} />
                <StatBlock label={tr("样本数")} value={String(stats.count ?? 0)} icon={<Calendar className="h-3 w-3" />} />
              </div>
            )}

            {/* Chart */}
            {chartData.length > 0 ? (
              <div className="h-[300px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 11 }}
                      stroke="var(--color-ink-3)"
                    />
                    <YAxis
                      tick={{ fontSize: 11 }}
                      stroke="var(--color-ink-3)"
                      domain={["auto", "auto"]}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "var(--color-surface-3)",
                        border: "1px solid var(--color-border)",
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
              <div className="text-sm text-muted-foreground">{tr("无可用观测值")}</div>
            )}

            {/* Observations table (last 20) */}
            {series.observations.length > 0 && (
              <div className="rounded-md border">
                <div className="border-b px-3 py-2 text-xs text-muted-foreground">
                  {tr("最近 20 条观测值")}
                </div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[200px]">{tr("日期")}</TableHead>
                      <TableHead>{tr("值")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {series.observations.slice(-20).reverse().map((o, i) => (
                      <TableRow key={`${o.date}-${i}`}>
                        <TableCell className="font-mono text-xs">{o.date}</TableCell>
                        <TableCell className="font-mono text-xs">{o.value}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}

            {series.source_url && (
              <div className="text-xs text-muted-foreground">
                {tr("源:")}{" "}
                <a
                  href={series.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-400 hover:underline"
                >
                  {series.source_url}
                </a>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function StatBlock({
  label,
  value,
  tone,
  icon,
}: {
  label: string;
  value: string;
  tone?: "up" | "down";
  icon?: React.ReactNode;
}) {
  const toneClass =
    tone === "up"
      ? "text-emerald-400"
      : tone === "down"
        ? "text-red-400"
        : "text-foreground";
  return (
    <div className="rounded-md border bg-card/50 p-2">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className={`mt-1 font-mono text-sm font-semibold ${toneClass}`}>{value}</div>
    </div>
  );
}

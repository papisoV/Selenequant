"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Loader2, RefreshCw, TrendingUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getReturnReport } from "@/lib/api/market-analysis";
import type { ReturnReport } from "@/lib/api/market-analysis";
import { fmtNum, MiniLineChart, pct, Stat } from "./diagnostic-charts";

/** 多空回报面板卡（跨截面权重漂移回测）。累计 LS 回报 / 换手 / 统计。 */
export function ReturnReportCard({ symbols, interval }: { symbols: string; interval: string }) {
  const [report, setReport] = useState<ReturnReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getReturnReport({ symbols, interval })
      .then((r) => {
        if (!cancelled) {
          setReport(r);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "回报面板加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbols, interval, reloadKey]);

  const s = report?.stats_ls ?? {};
  const gross = report?.cum_ret_ls ?? { dates: [], values: [] };
  const net = report?.cum_ret_net_ls ?? { dates: [], values: [] };
  const turn = report?.turnover_ls ?? { dates: [], values: [] };
  const avg = report?.avg_turnover_ls;
  const avgSeries =
    turn.dates.length > 0 && avg != null
      ? [{ name: "Mean Turnover", values: turn.dates.map(() => avg) as (number | null)[], color: "#b91c1c", dashed: true }]
      : [];
  const indus = report?.df_indus ?? null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <TrendingUp className="h-4 w-4" />多空回报面板 · 权重漂移回测
          <span className="ml-auto font-mono text-xs text-muted-foreground">{symbols} · {interval}</span>
          <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={() => { setLoading(true); setReloadKey((k) => k + 1); }} disabled={loading}>
            {loading ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <RefreshCw className="h-3 w-3 mr-1" />}
            刷新
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="py-4 text-center text-xs text-muted-foreground">运行权重漂移回测中…</div>
        ) : error ? (
          <div className="flex items-center gap-2 py-4 text-xs text-amber-600">
            <AlertTriangle className="h-4 w-4" />回报面板加载失败：{error}
          </div>
        ) : !report || gross.dates.length === 0 ? (
          <p className="text-xs text-muted-foreground">暂无足够回测数据（需至少 2 个 symbol 对齐）</p>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
              <Stat label="Ann. Return" value={pct(s["Ann. Return"])} />
              <Stat label="Ann. Sharpe" value={fmtNum(s["Ann. Sharpe"], 2)} />
              <Stat label="Max Drawdown" value={pct(s["Max Drawdown"])} />
              <Stat label="WinRate" value={pct(s["WinRate"])} />
            </div>
            <div>
              <p className="mb-1 text-[11px] font-medium text-muted-foreground">累计多空回报（毛 vs 净）</p>
              <MiniLineChart
                dates={gross.dates}
                series={[
                  { name: "Return Before Cost", values: gross.values, color: "#15803d", dashed: true },
                  { name: "Cumulative LS Return", values: net.values, color: "#15803d" },
                ]}
              />
            </div>
            <div>
              <p className="mb-1 text-[11px] font-medium text-muted-foreground">换手率</p>
              <MiniLineChart
                dates={turn.dates}
                series={[{ name: "LS Portfolio Turnover", values: turn.values, color: "#15803d" }, ...avgSeries]}
              />
            </div>
            {indus ? <IndustryBars industries={indus.industries} returns={indus.returns} /> : null}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function IndustryBars({ industries, returns }: { industries: string[]; returns: (number | null)[] }) {
  const maxAbs = Math.max(1e-9, ...returns.filter((v): v is number => v != null && Number.isFinite(v)).map((v) => Math.abs(v)));
  const width = (v: number | null): number => {
    if (v == null || !Number.isFinite(v)) return 0;
    return Math.max(4, (Math.abs(v) / maxAbs) * 100);
  };
  return (
    <div>
      <p className="mb-1 text-[11px] font-medium text-muted-foreground">行业归因（组均值净 LS 回报）</p>
      <div className="space-y-1">
        {industries.map((ind, i) => {
          const v = returns[i] ?? null;
          return (
            <div key={ind} className="flex items-center gap-2 text-xs">
              <span className="w-16 truncate">{ind}</span>
              <div className="h-3 flex-1 rounded bg-muted">
                <div className="h-3 rounded" style={{ width: `${width(v)}%`, background: v != null && v >= 0 ? "var(--color-pos)" : "var(--color-neg)" }} />
              </div>
              <span className="w-14 text-right font-mono">{fmtNum(v, 3)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

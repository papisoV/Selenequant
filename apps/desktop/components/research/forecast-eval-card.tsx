"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getForecastEval } from "@/lib/api/market-analysis";
import type { ForecastEvalReport, ForecastEvalRow } from "@/lib/api/market-analysis";
import { useUiText } from "@/components/i18n/use-ui-text"

const pct = (v: number | null | undefined): string => (v == null ? "N/A" : `${(v * 100).toFixed(1)}%`);

const dirGlyph = (d: ForecastEvalRow["forecast"]): string => {
  if (d === "up") return "▲";
  if (d === "down") return "▼";
  return "—";
};

/** 预测校准审计卡（B1/B2）。诚实披露历史 next_bar_forecast 命中率 vs 动量基线。 */
export function ForecastEvalCard({ symbol, interval }: { symbol: string; interval: string }) {
  const tr = useUiText()

  const [report, setReport] = useState<ForecastEvalReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getForecastEval(symbol, interval)
      .then((r) => {
        if (!cancelled) {
          setReport(r);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "校准数据加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol, interval, reloadKey]);

  const rows = report?.rows ?? [];
  const n = report?.samples_with_forecast ?? 0;
  const ups = rows.filter((r) => r.forecast === "up").length;
  const downs = rows.filter((r) => r.forecast === "down").length;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <ShieldCheck className="h-4 w-4" />{tr("预测校准 · 历史预测审计")}
          <span className="ml-auto font-mono text-xs text-muted-foreground">{symbol} · {interval}</span>
          <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={() => { setLoading(true); setReloadKey((k) => k + 1); }} disabled={loading}>
            {loading ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <RefreshCw className="h-3 w-3 mr-1" />}
            {tr("刷新")}
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="py-4 text-center text-xs text-muted-foreground">{tr("加载预测校准中…")}</div>
        ) : error ? (
          <div className="flex items-center gap-2 py-4 text-xs text-amber-600">
            <AlertTriangle className="h-4 w-4" />{tr("校准数据加载失败：")}{tr(error)}
          </div>
        ) : report && report.samples_total === 0 ? (
          <p className="text-xs text-muted-foreground">{tr("暂无历史预测样本（完成一次分析后点“刷新”可见）")}</p>
        ) : report ? (
          <div className="space-y-3">
            <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700">
              {n > 0 ? (
                <>{tr("预测为实验性 · 未充分回测。当前命中率")} <b>{pct(report.hit_rate_forecast)}</b>（{n} {tr("样本）vs 动量基线")} <b>{pct(report.hit_rate_baseline)}</b>{tr("。本面板仅为预测力审计，不构成投资建议。")}</>
              ) : (
                <>{tr("暂无含预测方向的样本，仅展示动量基线对照。预测为实验性 · 未充分回测，本面板不构成投资建议。")}</>
              )}
            </div>

            <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
              <span>{tr("预测命中率")} <b className="font-mono">{pct(report.hit_rate_forecast)}</b>（{n} {tr("样本）")}</span>
              <span>{tr("动量基线")} <b className="font-mono">{pct(report.hit_rate_baseline)}</b></span>
              <span>{tr("方向分布")} <b className="font-mono">▲ {ups} · ▼ {downs}</b></span>
            </div>

            <div className="max-h-64 space-y-1 overflow-auto">
              {rows.map((r) => (
                <div key={r.record_id} className="flex flex-wrap items-center gap-2 text-xs">
                  <Badge variant="outline" className="font-mono">{r.record_id.slice(-12)}</Badge>
                  <span>{dirGlyph(r.forecast)}</span>
                  <span className="font-mono">{r.probability != null ? `${Math.round(r.probability * 100)}%` : "—"}</span>
                  <span className="text-muted-foreground">actual {r.actual}</span>
                  {r.forecast != null ? (
                    r.forecast_hit ? (
                      <span className="font-mono" style={{ color: "var(--color-pos)" }}>✓</span>
                    ) : (
                      <span className="font-mono" style={{ color: "var(--color-neg)" }}>✗</span>
                    )
                  ) : (
                    <span className="text-muted-foreground">{tr("无预测")}</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

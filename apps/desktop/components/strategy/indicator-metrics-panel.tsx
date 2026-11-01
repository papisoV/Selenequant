"use client"

import React from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { TrendingUp, TrendingDown, Activity, BarChart3 } from "lucide-react"
import type { CustomMetricsData } from "./types"
import { useUiText } from "@/components/i18n/use-ui-text"
import { identityTranslate, type Translate } from "@/lib/i18n/types"

interface IndicatorMetricsPanelProps {
  metrics: CustomMetricsData | undefined
  className?: string
}

function formatValue(value: number | undefined, decimals: number = 2): string {
  if (value === undefined || value === null) return "—"
  return value.toFixed(decimals)
}

// label 是中文源串,渲染点再过 tr —— 模块级常量里不能调 hook。
const REGIME_CONFIG: Record<string, { color: string; icon: typeof TrendingUp; label: string }> = {
  bull: { color: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30", icon: TrendingUp, label: "牛市" },
  bear: { color: "bg-red-500/20 text-red-400 border-red-500/30", icon: TrendingDown, label: "熊市" },
  sideways: { color: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30", icon: Activity, label: "震荡" },
  transition: { color: "bg-orange-500/20 text-orange-400 border-orange-500/30", icon: BarChart3, label: "转换" },
}

function getHurstInterpretation(hurst: number | undefined, tr: Translate = identityTranslate): string {
  if (hurst === undefined) return ""
  if (hurst < 0.5) return tr("均值回归倾向")
  if (hurst > 0.5) return tr("趋势延续倾向")
  return tr("随机游走")
}

function getZscoreInterpretation(zscore: number | undefined, tr: Translate = identityTranslate): string {
  if (zscore === undefined) return ""
  if (zscore < -2) return tr("买入信号")
  if (zscore > 2) return tr("卖出信号")
  return tr("中性")
}

function getMfiInterpretation(mfi: number | undefined, tr: Translate = identityTranslate): string {
  if (mfi === undefined) return ""
  if (mfi > 80) return tr("超买")
  if (mfi < 20) return tr("超卖")
  return tr("中性")
}

function getCmfInterpretation(cmf: number | undefined, tr: Translate = identityTranslate): string {
  if (cmf === undefined) return ""
  if (cmf > 0.25) return tr("买盘强势")
  if (cmf < -0.25) return tr("卖盘强势")
  return tr("中性")
}

export function IndicatorMetricsPanel({ metrics, className }: IndicatorMetricsPanelProps) {
  const tr = useUiText()

  if (!metrics) {
    return (
      <Card className={className}>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">{tr("自定义指标")}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="py-4 text-center text-xs text-muted-foreground">
            {tr("暂无自定义指标数据")}
          </p>
        </CardContent>
      </Card>
    )
  }

  const regime = metrics.regime
  const regimeConfig = regime ? REGIME_CONFIG[regime] : null

  const statisticalIndicators = [
    { name: "Hurst", value: metrics.hurst, interpretation: getHurstInterpretation(metrics.hurst, tr) },
    { name: "Z-score", value: metrics.zscore, interpretation: getZscoreInterpretation(metrics.zscore, tr) },
    { name: "Half-life", value: metrics.half_life, interpretation: metrics.half_life ? tr("{n} bars回归", { n: metrics.half_life.toFixed(1) }) : "" },
  ]

  const volumeIndicators = [
    { name: "OBV", value: metrics.obv, interpretation: "" },
    { name: "CMF", value: metrics.cmf, interpretation: getCmfInterpretation(metrics.cmf, tr) },
    { name: "MFI", value: metrics.mfi, interpretation: getMfiInterpretation(metrics.mfi, tr) },
    { name: "Force Index", value: metrics.force_index, interpretation: "" },
  ]

  const allIndicators = [...statisticalIndicators, ...volumeIndicators].filter(i => i.value !== undefined)

  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">{tr("自定义指标")}</CardTitle>
          {regimeConfig && (
            <Badge className={regimeConfig.color}>
              <regimeConfig.icon className="h-3 w-3 mr-1" />
              {tr(regimeConfig.label)}
            </Badge>
          )}
        </div>
        <p className="text-[11px] text-muted-foreground">
          {tr("统计指标与成交量指标")}
        </p>
      </CardHeader>
      <CardContent>
        {allIndicators.length === 0 ? (
          <p className="py-4 text-center text-xs text-muted-foreground">
            {tr("暂无自定义指标数据")}
          </p>
        ) : (
          <div className="space-y-3">
            {statisticalIndicators.some(i => i.value !== undefined) && (
              <div>
                <div className="text-xs font-medium text-muted-foreground mb-2">
                  {tr("统计指标")}
                </div>
                <div className="grid grid-cols-3 gap-2">
                  {statisticalIndicators.filter(i => i.value !== undefined).map((ind) => (
                    <div key={ind.name} className="rounded-md border border-border/60 bg-background/40 px-2 py-1.5">
                      <div className="text-[10px] text-muted-foreground">{ind.name}</div>
                      <div className="text-sm font-semibold tabular-nums">
                        {formatValue(ind.value, ind.name === "OBV" ? 0 : 2)}
                      </div>
                      {ind.interpretation && (
                        <div className="text-[10px] text-muted-foreground truncate">
                          {ind.interpretation}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {volumeIndicators.some(i => i.value !== undefined) && (
              <div>
                <div className="text-xs font-medium text-muted-foreground mb-2">
                  {tr("成交量指标")}
                </div>
                <div className="grid grid-cols-4 gap-2">
                  {volumeIndicators.filter(i => i.value !== undefined).map((ind) => (
                    <div key={ind.name} className="rounded-md border border-border/60 bg-background/40 px-2 py-1.5">
                      <div className="text-[10px] text-muted-foreground">{ind.name}</div>
                      <div className="text-sm font-semibold tabular-nums">
                        {formatValue(ind.value, ind.name === "OBV" ? 0 : 2)}
                      </div>
                      {ind.interpretation && (
                        <div className="text-[10px] text-muted-foreground truncate">{ind.interpretation}</div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

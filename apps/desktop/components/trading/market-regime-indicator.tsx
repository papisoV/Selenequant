"use client"

import React, { useEffect, useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { usePolledJson } from "@/lib/hooks/use-polled-json"
import { Badge } from "@/components/ui/badge"
import { TrendingUp, TrendingDown, Activity, Zap } from "lucide-react"
import { useApi } from "@/lib/hooks/use-api"

interface MarketRegimeData {
  regime: "bull" | "bear" | "sideways" | "transition"
  annual_return: number
  volatility: number
  symbol?: string
  timestamp?: number
}

const REGIME_CONFIG = {
  bull: {
    color: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
    icon: TrendingUp,
    label: "牛市",
    description: "趋势向上，适合趋势跟踪策略",
  },
  bear: {
    color: "bg-red-500/20 text-red-400 border-red-500/30",
    icon: TrendingDown,
    label: "熊市",
    description: "趋势向下，适合防御策略",
  },
  sideways: {
    color: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
    icon: Activity,
    label: "震荡",
    description: "均值回归策略适用",
  },
  transition: {
    color: "bg-orange-500/20 text-orange-400 border-orange-500/30",
    icon: Zap,
    label: "转换期",
    description: "高波动预警，建议观望",
  },
} as const

interface MarketRegimeIndicatorProps {
  regimeData?: MarketRegimeData | null
  className?: string
  symbol?: string
}

export function MarketRegimeIndicator({ regimeData, className, symbol }: MarketRegimeIndicatorProps) {
  const { data: fetchedData, isLoading } = useApi<MarketRegimeData | null>(
    symbol ? `/api/v1/regime?symbol=${encodeURIComponent(symbol)}` : null
  )

  const data = regimeData ?? fetchedData

  if (!data) {
    return (
      <Card className={className}>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">市场状态</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-center py-4">
            <Badge className="bg-gray-500/20 text-gray-400">{isLoading ? "加载中" : "暂无数据"}</Badge>
          </div>
        </CardContent>
      </Card>
    )
  }

  const config = REGIME_CONFIG[data.regime]
  const IconComponent = config.icon

  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">市场状态</CardTitle>
          {data.symbol && (
            <span className="text-xs text-muted-foreground">{data.symbol}</span>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <Badge className={`${config.color} px-3 py-1.5`}>
          <IconComponent className="h-4 w-4 mr-1.5" />
          {config.label}
        </Badge>
        <p className="text-xs text-muted-foreground">{config.description}</p>

        <div className="grid grid-cols-2 gap-2 pt-2">
          <div className="rounded-md border border-border/60 bg-background/40 px-2 py-1.5">
            <div className="text-[10px] text-muted-foreground">年化收益</div>
            <div className={`text-sm font-semibold tabular-nums ${data.annual_return >= 0 ? "text-emerald-400" : "text-red-400"}`}>
              {(data.annual_return * 100).toFixed(1)}%
            </div>
          </div>
          <div className="rounded-md border border-border/60 bg-background/40 px-2 py-1.5">
            <div className="text-[10px] text-muted-foreground">波动率</div>
            <div className={`text-sm font-semibold tabular-nums ${data.volatility > 0.35 ? "text-orange-400" : "text-foreground"}`}>
              {(data.volatility * 100).toFixed(1)}%
            </div>
          </div>
        </div>

        {data.timestamp && (
          <div className="text-[10px] text-muted-foreground pt-1">
            更新: {new Date(data.timestamp).toLocaleString()}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export function useMarketRegime(symbol: string | undefined) {
  const url = symbol ? `/api/v1/regime?symbol=${encodeURIComponent(symbol)}` : null
  const { data, loading, error } = usePolledJson<MarketRegimeData>(url, 60000)
  return { regimeData: data, loading, error }
}

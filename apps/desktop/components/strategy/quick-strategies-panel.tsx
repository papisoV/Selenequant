"use client"

import React, { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  TrendingUp,
  TrendingDown,
  Activity,
  Zap,
  Target,
  BarChart3,
  Plus,
  Loader2,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { filterByCategory, countByCategory } from "@/lib/strategy-utils"

type StrategyCategory = "trend" | "momentum" | "reversion" | "breakout" | "volatility"

interface QuickStrategy {
  id: string
  name: string
  nameZh: string
  category: StrategyCategory
  description: string
  descriptionZh: string
  defaultParams: Record<string, number | string>
}

const QUICK_STRATEGIES: QuickStrategy[] = [
  { id: "sma_cross", name: "SMA Crossover", nameZh: "SMA均线交叉", category: "trend", description: "Classic dual moving average crossover.", descriptionZh: "经典双均线交叉策略。", defaultParams: { sma_short: 20, sma_long: 50 } },
  { id: "ema_cross", name: "EMA Crossover", nameZh: "EMA均线交叉", category: "trend", description: "Exponential moving average crossover.", descriptionZh: "指数均线交叉策略。", defaultParams: { ema_fast: 12, ema_slow: 26 } },
  { id: "adx_trend", name: "ADX Trend Follower", nameZh: "ADX趋势跟踪", category: "trend", description: "ADX-based trend following.", descriptionZh: "基于ADX的趋势跟踪策略。", defaultParams: { adx_period: 14, adx_threshold: 25 } },
  { id: "atr_trailing", name: "ATR Trailing Stop", nameZh: "ATR移动止损", category: "trend", description: "Uses ATR for dynamic trailing stop.", descriptionZh: "使用ATR动态移动止损。", defaultParams: { atr_period: 14, atr_multiplier: 3.0 } },
  { id: "kdj_trend", name: "KDJ Trend Following", nameZh: "KDJ趋势跟踪", category: "trend", description: "KDJ-based trend following.", descriptionZh: "基于KDJ的趋势跟踪策略。", defaultParams: { kdj_n: 9, kdj_m1: 3, kdj_m2: 3 } },
  { id: "macd_momentum", name: "MACD Momentum", nameZh: "MACD动量", category: "momentum", description: "MACD histogram momentum strategy.", descriptionZh: "MACD柱状图动量策略。", defaultParams: { macd_fast: 12, macd_slow: 26, macd_signal: 9 } },
  { id: "rsi_momentum", name: "RSI Momentum", nameZh: "RSI动量", category: "momentum", description: "RSI-based momentum.", descriptionZh: "RSI动量策略。", defaultParams: { rsi_period: 14, rsi_oversold: 30, rsi_overbought: 70 } },
  { id: "stochastic_momentum", name: "Stochastic Momentum", nameZh: "随机指标动量", category: "momentum", description: "Stochastic oscillator momentum.", descriptionZh: "随机指标动量策略。", defaultParams: { stoch_k: 14, stoch_d: 3, stoch_smooth: 3 } },
  { id: "kdj_momentum", name: "KDJ Momentum", nameZh: "KDJ动量", category: "momentum", description: "KDJ indicator momentum.", descriptionZh: "KDJ指标动量策略。", defaultParams: { kdj_n: 9, kdj_m1: 3, kdj_m2: 3 } },
  { id: "rsi_reversal", name: "RSI Mean Reversion", nameZh: "RSI均值回归", category: "reversion", description: "Extreme RSI levels trigger mean reversion.", descriptionZh: "RSI极端水平触发均值回归。", defaultParams: { rsi_period: 14, rsi_oversold: 30, rsi_overbought: 70 } },
  { id: "bollinger_reversal", name: "Bollinger Band Reversion", nameZh: "布林带均值回归", category: "reversion", description: "Mean reversion at Bollinger Band extremes.", descriptionZh: "价格触及布林带极端时均值回归。", defaultParams: { bb_period: 20, bb_std: 2 } },
  { id: "kdj_reversal", name: "KDJ Mean Reversion", nameZh: "KDJ均值回归", category: "reversion", description: "KDJ extreme levels trigger reversal.", descriptionZh: "KDJ极端水平触发反转。", defaultParams: { kdj_n: 9, kdj_m1: 3, kdj_m2: 3 } },
  { id: "stochastic_reversal", name: "Stochastic Reversal", nameZh: "随机指标均值回归", category: "reversion", description: "Stochastic extremes signal mean reversion.", descriptionZh: "随机指标极端值触发均值回归。", defaultParams: { stoch_k: 14, stoch_d: 3 } },
  { id: "bollinger_breakout", name: "Bollinger Band Breakout", nameZh: "布林带突破", category: "breakout", description: "Breakout when price exits Bollinger Bands.", descriptionZh: "价格突破布林带。", defaultParams: { bb_period: 20, bb_std: 2 } },
  { id: "atr_breakout", name: "ATR Breakout", nameZh: "ATR突破", category: "breakout", description: "Volatility breakout beyond ATR range.", descriptionZh: "价格突破ATR波动范围。", defaultParams: { atr_period: 14, atr_multiplier: 2.0 } },
  { id: "adx_breakout", name: "ADX Volatility Breakout", nameZh: "ADX波动突破", category: "breakout", description: "Breakout confirmed by ADX strength.", descriptionZh: "ADX强度确认的突破。", defaultParams: { adx_period: 14, adx_threshold: 20 } },
  { id: "atr_volatility", name: "ATR Volatility Strategy", nameZh: "ATR波动率策略", category: "volatility", description: "Uses ATR to measure and trade volatility.", descriptionZh: "使用ATR衡量和交易波动率。", defaultParams: { atr_period: 14, atr_multiplier: 2.5 } },
  { id: "adx_range", name: "ADX Range Strategy", nameZh: "ADX震荡策略", category: "volatility", description: "ADX-based range trading in low volatility.", descriptionZh: "低波动市场中的ADX震荡交易。", defaultParams: { adx_period: 14, adx_range_threshold: 20 } },
]

const CATEGORY_INFO: Record<StrategyCategory, { label: string; icon: React.ElementType; color: string }> = {
  trend: { label: "Trend Following", icon: TrendingUp, color: "text-emerald-400" },
  momentum: { label: "Momentum", icon: Zap, color: "text-blue-400" },
  reversion: { label: "Mean Reversion", icon: TrendingDown, color: "text-orange-400" },
  breakout: { label: "Breakout", icon: Target, color: "text-purple-400" },
  volatility: { label: "Volatility", icon: Activity, color: "text-pink-400" },
}

interface QuickStrategiesPanelProps {
  onCreateStrategy: (strategyId: string, params: Record<string, number | string>) => Promise<void>
}

export function QuickStrategiesPanel({ onCreateStrategy }: QuickStrategiesPanelProps) {
  const [selectedCategory, setSelectedCategory] = useState<StrategyCategory | "all">("all")
  const [creatingId, setCreatingId] = useState<string | null>(null)

  const filteredStrategies = filterByCategory(QUICK_STRATEGIES, selectedCategory)

  const handleCreate = async (strategy: QuickStrategy) => {
    setCreatingId(strategy.id)
    try {
      await onCreateStrategy(strategy.id, strategy.defaultParams)
    } finally {
      setCreatingId(null)
    }
  }

  const categoryCount = countByCategory(QUICK_STRATEGIES) as Record<StrategyCategory, number>

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2">
            <BarChart3 className="h-4 w-4" />
            快速策略
            <Badge variant="secondary" className="ml-1">
              {QUICK_STRATEGIES.length}
            </Badge>
          </CardTitle>
        </div>
        <p className="text-xs text-muted-foreground mt-1">
          StrategyFactory 预置策略，点击创建并自定义。
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            variant={selectedCategory === "all" ? "default" : "outline"}
            className="h-7 text-xs"
            onClick={() => setSelectedCategory("all")}
          >
            全部 ({QUICK_STRATEGIES.length})
          </Button>
          {(Object.keys(CATEGORY_INFO) as StrategyCategory[]).map((cat) => {
            const info = CATEGORY_INFO[cat]
            const Icon = info.icon
            const count = categoryCount[cat] || 0
            return (
              <Button
                key={cat}
                type="button"
                size="sm"
                variant={selectedCategory === cat ? "default" : "outline"}
                className="h-7 text-xs gap-1"
                onClick={() => setSelectedCategory(cat)}
              >
                <Icon className="h-3 w-3" />
                {info.label}
                <span className="opacity-60">({count})</span>
              </Button>
            )
          })}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {filteredStrategies.map((strategy) => {
            const info = CATEGORY_INFO[strategy.category]
            const Icon = info.icon
            const isCreating = creatingId === strategy.id

            return (
              <div
                key={strategy.id}
                className={cn(
                  "p-3 rounded-lg border border-border bg-card/50",
                  "hover:border-primary/50 hover:bg-card transition-all",
                  "flex flex-col gap-2"
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <Icon className={cn("h-4 w-4 shrink-0", info.color)} />
                    <span className="text-sm font-medium truncate">{strategy.nameZh}</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] shrink-0">
                    {info.label}
                  </Badge>
                </div>

                <p className="text-xs text-muted-foreground line-clamp-2 flex-1">
                  {strategy.descriptionZh}
                </p>

                <div className="text-[10px] text-muted-foreground font-mono bg-muted/50 px-1.5 py-0.5 rounded truncate">
                  {Object.entries(strategy.defaultParams)
                    .map(([k, v]) => `${k}=${v}`)
                    .join(", ")}
                </div>

                <Button
                  type="button"
                  size="sm"
                  className="w-full h-7 text-xs gap-1"
                  onClick={() => handleCreate(strategy)}
                  disabled={isCreating}
                >
                  {isCreating ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Plus className="h-3 w-3" />
                  )}
                  创建
                </Button>
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}

export { QUICK_STRATEGIES, CATEGORY_INFO }
export type { QuickStrategy, StrategyCategory }

"use client"

import React, { useCallback, useMemo, useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { formatDateForInput, formatDateForConfig } from "@/lib/format"
import {
  Calendar,
  DollarSign,
  TrendingUp,
  Settings,
  Play,
  Loader2,
  FlaskConical,
  BarChart3,
  Sliders,
} from "lucide-react"

export interface StrategyConfig {
  startTime: string
  endTime: string
  symbol: string
  period: string
  adjust: string
  benchmark: string
  initCash: number
  stake: number
  slippage: number
  commission: number
  stampDuty: number
  strategyId: string
  strategyParams: Record<string, number | string>
}

export interface StrategyConfigPanelProps {
  config: StrategyConfig
  onConfigChange: (config: StrategyConfig) => void
  onRunBacktest: () => void
  strategies?: { id: string; name: string; category: string }[]
  symbols?: string[]
  loading?: boolean
  disabled?: boolean
}

const ADJUST_OPTIONS = [
  { value: "qfq", label: "前复权" },
  { value: "hfq", label: "后复权" },
  { value: "none", label: "不复权" },
]

const BENCHMARK_OPTIONS = [
  { value: "BTC/USDT", label: "BTC/USDT" },
  { value: "ETH/USDT", label: "ETH/USDT" },
]

const DEFAULT_CONFIG: StrategyConfig = {
  startTime: new Date(Date.now() - 180 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10).replace(/-/g, ""),
  endTime: new Date().toISOString().slice(0, 10).replace(/-/g, ""),
  symbol: "BTC/USDT",
  period: "4h",
  adjust: "none",
  benchmark: "BTC/USDT",
  initCash: 10000,
  stake: 100,
  slippage: 0.1,
  commission: 0.05,
  stampDuty: 0,
  strategyId: "",
  strategyParams: {},
}

const INTERVAL_OPTIONS = [
  { value: "1m", label: "1m" },
  { value: "5m", label: "5m" },
  { value: "15m", label: "15m" },
  { value: "30m", label: "30m" },
  { value: "1h", label: "1H" },
  { value: "4h", label: "4H" },
  { value: "1d", label: "1D" },
  { value: "1w", label: "1W" },
]

export function StrategyConfigPanel({
  config,
  onConfigChange,
  onRunBacktest,
  strategies = [],
  symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
  loading = false,
  disabled = false,
}: StrategyConfigPanelProps) {
  const [activeSection, setActiveSection] = useState<"market" | "backtest" | "strategy">("market")

  const handleFieldChange = useCallback((field: keyof StrategyConfig, value: string | number | Record<string, number | string>) => {
    onConfigChange({ ...config, [field]: value })
  }, [config, onConfigChange])

  const mergedConfig = useMemo(() => ({ ...DEFAULT_CONFIG, ...config }), [config])

  return (
    <Card className="border-border/80 bg-card/95">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm flex items-center gap-2">
            <Settings className="h-4 w-4" />
            策略参数配置
          </CardTitle>
          <div className="flex gap-1">
            <Button
              type="button"
              size="sm"
              variant={activeSection === "market" ? "secondary" : "ghost"}
              className="h-7 px-2 text-xs"
              onClick={() => setActiveSection("market")}
            >
              <BarChart3 className="h-3 w-3 mr-1" />
              行情
            </Button>
            <Button
              type="button"
              size="sm"
              variant={activeSection === "backtest" ? "secondary" : "ghost"}
              className="h-7 px-2 text-xs"
              onClick={() => setActiveSection("backtest")}
            >
              <Sliders className="h-3 w-3 mr-1" />
              回测
            </Button>
            <Button
              type="button"
              size="sm"
              variant={activeSection === "strategy" ? "secondary" : "ghost"}
              className="h-7 px-2 text-xs"
              onClick={() => setActiveSection("strategy")}
            >
              <FlaskConical className="h-3 w-3 mr-1" />
              策略
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {activeSection === "market" && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <label className="text-xs text-muted-foreground flex items-center gap-1">
                  <Calendar className="h-3 w-3" />
                  开始日期
                </label>
                <Input
                  type="date"
                  value={formatDateForInput(mergedConfig.startTime)}
                  onChange={(e) => handleFieldChange("startTime", formatDateForConfig(e.target.value))}
                  disabled={disabled}
                  className="h-9 text-sm"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs text-muted-foreground flex items-center gap-1">
                  <Calendar className="h-3 w-3" />
                  结束日期
                </label>
                <Input
                  type="date"
                  value={formatDateForInput(mergedConfig.endTime)}
                  onChange={(e) => handleFieldChange("endTime", formatDateForConfig(e.target.value))}
                  disabled={disabled}
                  className="h-9 text-sm"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs text-muted-foreground">交易标的</label>
              <div className="flex gap-2">
                <Input
                  value={mergedConfig.symbol}
                  onChange={(e) => handleFieldChange("symbol", e.target.value)}
                  disabled={disabled}
                  placeholder="BTC/USDT"
                  className="h-9 text-sm flex-1"
                />
                <select
                  value={mergedConfig.symbol}
                  onChange={(e) => handleFieldChange("symbol", e.target.value)}
                  disabled={disabled}
                  className="h-9 rounded-md border border-border bg-card px-2 text-sm"
                >
                  {symbols.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <label className="text-xs text-muted-foreground">周期</label>
                <select
                  value={mergedConfig.period}
                  onChange={(e) => handleFieldChange("period", e.target.value)}
                  disabled={disabled}
                  className="h-9 w-full rounded-md border border-border bg-card px-2 text-sm"
                >
                  {INTERVAL_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs text-muted-foreground">复权</label>
                <select
                  value={mergedConfig.adjust}
                  onChange={(e) => handleFieldChange("adjust", e.target.value)}
                  disabled={disabled}
                  className="h-9 w-full rounded-md border border-border bg-card px-2 text-sm"
                >
                  {ADJUST_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>
        )}

        {activeSection === "backtest" && (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs text-muted-foreground flex items-center gap-1">
                <TrendingUp className="h-3 w-3" />
                回测基准
              </label>
              <select
                value={mergedConfig.benchmark}
                onChange={(e) => handleFieldChange("benchmark", e.target.value)}
                disabled={disabled}
                className="h-9 w-full rounded-md border border-border bg-card px-2 text-sm"
              >
                {BENCHMARK_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs text-muted-foreground flex items-center gap-1">
                <DollarSign className="h-3 w-3" />
                初始资金
              </label>
              <Input
                type="number"
                min={100}
                step={1000}
                value={mergedConfig.initCash}
                onChange={(e) => handleFieldChange("initCash", Number(e.target.value))}
                disabled={disabled}
                className="h-9 text-sm"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs text-muted-foreground">交易规模</label>
              <Input
                type="number"
                min={1}
                step={10}
                value={mergedConfig.stake}
                onChange={(e) => handleFieldChange("stake", Number(e.target.value))}
                disabled={disabled}
                className="h-9 text-sm"
              />
            </div>

            <div className="grid grid-cols-3 gap-2">
              <div className="space-y-1.5">
                <label className="text-xs text-muted-foreground">滑点%</label>
                <Input
                  type="number"
                  min={0}
                  max={5}
                  step={0.01}
                  value={mergedConfig.slippage}
                  onChange={(e) => handleFieldChange("slippage", Number(e.target.value))}
                  disabled={disabled}
                  className="h-9 text-sm"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs text-muted-foreground">手续费%</label>
                <Input
                  type="number"
                  min={0}
                  max={2}
                  step={0.01}
                  value={mergedConfig.commission}
                  onChange={(e) => handleFieldChange("commission", Number(e.target.value))}
                  disabled={disabled}
                  className="h-9 text-sm"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs text-muted-foreground">印花税%</label>
                <Input
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  value={mergedConfig.stampDuty}
                  onChange={(e) => handleFieldChange("stampDuty", Number(e.target.value))}
                  disabled={disabled}
                  className="h-9 text-sm"
                />
              </div>
            </div>

            <div className="rounded-lg border border-border/60 bg-muted/30 p-3">
              <div className="text-xs text-muted-foreground mb-2">配置摘要</div>
              <div className="flex flex-wrap gap-2">
                <Badge variant="outline" className="text-[10px]">
                  {mergedConfig.symbol}
                </Badge>
                <Badge variant="outline" className="text-[10px]">
                  {mergedConfig.period}
                </Badge>
                <Badge variant="outline" className="text-[10px]">
                  ¥{mergedConfig.initCash.toLocaleString()}
                </Badge>
                <Badge variant="outline" className="text-[10px]">
                  滑点 {mergedConfig.slippage}%
                </Badge>
              </div>
            </div>
          </div>
        )}

        {activeSection === "strategy" && (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs text-muted-foreground flex items-center gap-1">
                <FlaskConical className="h-3 w-3" />
                策略选择
              </label>
              <select
                value={mergedConfig.strategyId}
                onChange={(e) => handleFieldChange("strategyId", e.target.value)}
                disabled={disabled}
                className="h-9 w-full rounded-md border border-border bg-card px-2 text-sm"
              >
                <option value="">-- 选择策略 --</option>
                {strategies.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} ({s.category})
                  </option>
                ))}
              </select>
            </div>

            {mergedConfig.strategyId && (
              <div className="rounded-lg border border-border/60 bg-muted/30 p-3">
                <div className="text-xs text-muted-foreground mb-3">策略参数</div>
                <div className="grid grid-cols-2 gap-3">
                  {mergedConfig.strategyId.includes("sma") && (
                    <>
                      <div className="space-y-1.5">
                        <label className="text-[10px] text-muted-foreground">快线周期</label>
                        <Input
                          type="number"
                          value={mergedConfig.strategyParams.fastPeriod ?? 10}
                          onChange={(e) => handleFieldChange("strategyParams", { ...mergedConfig.strategyParams, fastPeriod: Number(e.target.value) })}
                          disabled={disabled}
                          className="h-8 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-[10px] text-muted-foreground">慢线周期</label>
                        <Input
                          type="number"
                          value={mergedConfig.strategyParams.slowPeriod ?? 50}
                          onChange={(e) => handleFieldChange("strategyParams", { ...mergedConfig.strategyParams, slowPeriod: Number(e.target.value) })}
                          disabled={disabled}
                          className="h-8 text-xs"
                        />
                      </div>
                    </>
                  )}
                  {mergedConfig.strategyId.includes("rsi") && (
                    <>
                      <div className="space-y-1.5">
                        <label className="text-[10px] text-muted-foreground">RSI周期</label>
                        <Input
                          type="number"
                          value={mergedConfig.strategyParams.rsiPeriod ?? 14}
                          onChange={(e) => handleFieldChange("strategyParams", { ...mergedConfig.strategyParams, rsiPeriod: Number(e.target.value) })}
                          disabled={disabled}
                          className="h-8 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-[10px] text-muted-foreground">超卖阈值</label>
                        <Input
                          type="number"
                          value={mergedConfig.strategyParams.oversold ?? 30}
                          onChange={(e) => handleFieldChange("strategyParams", { ...mergedConfig.strategyParams, oversold: Number(e.target.value) })}
                          disabled={disabled}
                          className="h-8 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-[10px] text-muted-foreground">超买阈值</label>
                        <Input
                          type="number"
                          value={mergedConfig.strategyParams.overbought ?? 70}
                          onChange={(e) => handleFieldChange("strategyParams", { ...mergedConfig.strategyParams, overbought: Number(e.target.value) })}
                          disabled={disabled}
                          className="h-8 text-xs"
                        />
                      </div>
                    </>
                  )}
                  {mergedConfig.strategyId.includes("macd") && (
                    <>
                      <div className="space-y-1.5">
                        <label className="text-[10px] text-muted-foreground">快线周期</label>
                        <Input
                          type="number"
                          value={mergedConfig.strategyParams.fastPeriod ?? 12}
                          onChange={(e) => handleFieldChange("strategyParams", { ...mergedConfig.strategyParams, fastPeriod: Number(e.target.value) })}
                          disabled={disabled}
                          className="h-8 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-[10px] text-muted-foreground">慢线周期</label>
                        <Input
                          type="number"
                          value={mergedConfig.strategyParams.slowPeriod ?? 26}
                          onChange={(e) => handleFieldChange("strategyParams", { ...mergedConfig.strategyParams, slowPeriod: Number(e.target.value) })}
                          disabled={disabled}
                          className="h-8 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-[10px] text-muted-foreground">信号线周期</label>
                        <Input
                          type="number"
                          value={mergedConfig.strategyParams.signalPeriod ?? 9}
                          onChange={(e) => handleFieldChange("strategyParams", { ...mergedConfig.strategyParams, signalPeriod: Number(e.target.value) })}
                          disabled={disabled}
                          className="h-8 text-xs"
                        />
                      </div>
                    </>
                  )}
                  {mergedConfig.strategyId.includes("boll") && (
                    <>
                      <div className="space-y-1.5">
                        <label className="text-[10px] text-muted-foreground">周期</label>
                        <Input
                          type="number"
                          value={mergedConfig.strategyParams.bollPeriod ?? 20}
                          onChange={(e) => handleFieldChange("strategyParams", { ...mergedConfig.strategyParams, bollPeriod: Number(e.target.value) })}
                          disabled={disabled}
                          className="h-8 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-[10px] text-muted-foreground">标准差倍数</label>
                        <Input
                          type="number"
                          step="0.1"
                          value={mergedConfig.strategyParams.stdDev ?? 2}
                          onChange={(e) => handleFieldChange("strategyParams", { ...mergedConfig.strategyParams, stdDev: Number(e.target.value) })}
                          disabled={disabled}
                          className="h-8 text-xs"
                        />
                      </div>
                    </>
                  )}
                  {!mergedConfig.strategyId.includes("sma") &&
                   !mergedConfig.strategyId.includes("rsi") &&
                   !mergedConfig.strategyId.includes("macd") &&
                   !mergedConfig.strategyId.includes("boll") && (
                    <div className="col-span-2 text-xs text-muted-foreground text-center py-4">
                      请选择具体策略以配置参数
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        <Button
          type="button"
          className="w-full gap-2"
          disabled={disabled || loading || !mergedConfig.strategyId}
          onClick={onRunBacktest}
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Play className="h-4 w-4" />
          )}
          {loading ? "运行中..." : "开始回测"}
        </Button>
      </CardContent>
    </Card>
  )
}

export { DEFAULT_CONFIG }

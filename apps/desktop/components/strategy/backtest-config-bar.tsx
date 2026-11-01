"use client"

import React, { useCallback, useMemo } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  Calendar,
  DollarSign,
  TrendingUp,
  Settings,
  Play,
  Loader2,
  BarChart3,
  Target,
} from "lucide-react"
import { useUiText } from "@/components/i18n/use-ui-text"

export interface BacktestConfig {
  startTime: string
  endTime: string
  symbol: string
  period: string
  benchmark: string
  initCash: number
  stake: number
  slippage: number
  commission: number
  stampDuty: number
  exchange: string
  marketType: string
}

export interface BacktestConfigBarProps {
  config: BacktestConfig
  onConfigChange: (config: BacktestConfig) => void
  onRunBacktest: () => void
  onLoadData?: () => void
  symbols?: string[]
  loading?: boolean
  disabled?: boolean
}

const PERIOD_OPTIONS = [
  { value: "1m", label: "1m" },
  { value: "5m", label: "5m" },
  { value: "15m", label: "15m" },
  { value: "30m", label: "30m" },
  { value: "1h", label: "1H" },
  { value: "4h", label: "4H" },
  { value: "1d", label: "1D" },
  { value: "1w", label: "1W" },
]

const BENCHMARK_OPTIONS = [
  { value: "BTC/USDT", label: "BTC/USDT" },
  { value: "ETH/USDT", label: "ETH/USDT" },
]

const DEFAULT_CONFIG: BacktestConfig = {
  startTime: new Date(Date.now() - 365 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
  endTime: new Date().toISOString().slice(0, 10),
  symbol: "BTC/USDT",
  period: "daily",
  benchmark: "BTC/USDT",
  initCash: 10000,
  stake: 100,
  slippage: 0.1,
  commission: 0.05,
  stampDuty: 0.1,
  exchange: "binance",
  marketType: "spot",
}

const EXCHANGE_OPTIONS = [
  { value: "binance", label: "Binance" },
  { value: "okx", label: "OKX" },
]

const MARKET_TYPE_OPTIONS = [
  { value: "spot", label: "现货" },
  { value: "futures", label: "合约" },
]

/**
 * 把用户配置叠加到默认配置上(用户值优先)。纯函数, 便于上锁与重写。
 */
export function mergeBacktestConfig(
  defaults: BacktestConfig,
  config: Partial<BacktestConfig>,
): BacktestConfig {
  return { ...defaults, ...config }
}

export function BacktestConfigBar({
  config,
  onConfigChange,
  onRunBacktest,
  onLoadData,
  symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"],
  loading = false,
  disabled = false,
}: BacktestConfigBarProps) {
  const tr = useUiText()

  const mergedConfig = useMemo(
    () => mergeBacktestConfig(DEFAULT_CONFIG, config),
    [config],
  )

  const handleFieldChange = useCallback((field: keyof BacktestConfig, value: string | number) => {
    onConfigChange({ ...mergedConfig, [field]: value })
  }, [mergedConfig, onConfigChange])

  const ConfigBox = ({
    label,
    icon,
    children,
  }: {
    label: string
    icon?: React.ReactNode
    children: React.ReactNode
  }) => (
    <div className="flex flex-col gap-1 px-2 py-1 border border-border/60 rounded-md bg-card/50 min-w-[100px]">
      <label className="text-[11px] text-muted-foreground flex items-center gap-1">
        {icon}
        {label}
      </label>
      {children}
    </div>
  )

  return (
    <div className="border-b border-border/70 bg-card/95 backdrop-blur-sm px-3 py-2">
      <div className="flex flex-wrap items-end gap-2">
        <ConfigBox label={tr("开始日期")} icon={<Calendar className="h-3 w-3" />}>
          <Input
            type="date"
            value={mergedConfig.startTime}
            onChange={(e) => handleFieldChange("startTime", e.target.value)}
            disabled={disabled}
            className="h-7 text-xs w-[130px]"
          />
        </ConfigBox>

        <ConfigBox label={tr("结束日期")} icon={<Calendar className="h-3 w-3" />}>
          <Input
            type="date"
            value={mergedConfig.endTime}
            onChange={(e) => handleFieldChange("endTime", e.target.value)}
            disabled={disabled}
            className="h-7 text-xs w-[130px]"
          />
        </ConfigBox>

        <ConfigBox label={tr("交易标的")} icon={<Target className="h-3 w-3" />}>
          <select
            value={mergedConfig.symbol}
            onChange={(e) => handleFieldChange("symbol", e.target.value)}
            disabled={disabled}
            className="h-7 rounded-md border border-border bg-background px-2 text-xs w-[120px]"
          >
            {symbols.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </ConfigBox>

        <ConfigBox label={tr("交易所")} icon={<Settings className="h-3 w-3" />}>
          <select
            value={mergedConfig.exchange}
            onChange={(e) => handleFieldChange("exchange", e.target.value)}
            disabled={disabled}
            className="h-7 rounded-md border border-border bg-background px-2 text-xs w-[90px]"
          >
            {EXCHANGE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </ConfigBox>

        <ConfigBox label={tr("市场类型")}>
          <select
            value={mergedConfig.marketType}
            onChange={(e) => handleFieldChange("marketType", e.target.value)}
            disabled={disabled}
            className="h-7 rounded-md border border-border bg-background px-2 text-xs w-[70px]"
          >
            {MARKET_TYPE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </ConfigBox>

        <ConfigBox label={tr("周期")} icon={<BarChart3 className="h-3 w-3" />}>
          <select
            value={mergedConfig.period}
            onChange={(e) => handleFieldChange("period", e.target.value)}
            disabled={disabled}
            className="h-7 rounded-md border border-border bg-background px-2 text-xs w-[80px]"
          >
            {PERIOD_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </ConfigBox>

        <ConfigBox label={tr("基准")} icon={<TrendingUp className="h-3 w-3" />}>
          <select
            value={mergedConfig.benchmark}
            onChange={(e) => handleFieldChange("benchmark", e.target.value)}
            disabled={disabled}
            className="h-7 rounded-md border border-border bg-background px-2 text-xs w-[100px]"
          >
            {BENCHMARK_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </ConfigBox>

        <ConfigBox label={tr("初始资金")} icon={<DollarSign className="h-3 w-3" />}>
          <Input
            type="number"
            min={100}
            step={1000}
            value={mergedConfig.initCash}
            onChange={(e) => handleFieldChange("initCash", Number(e.target.value))}
            disabled={disabled}
            className="h-7 text-xs w-[80px]"
          />
        </ConfigBox>

        <ConfigBox label={tr("交易规模")}>
          <Input
            type="number"
            min={1}
            step={10}
            value={mergedConfig.stake}
            onChange={(e) => handleFieldChange("stake", Number(e.target.value))}
            disabled={disabled}
            className="h-7 text-xs w-[70px]"
          />
        </ConfigBox>

        <ConfigBox label={tr("滑点%")}>
          <Input
            type="number"
            min={0}
            max={5}
            step={0.01}
            value={mergedConfig.slippage}
            onChange={(e) => handleFieldChange("slippage", Number(e.target.value))}
            disabled={disabled}
            className="h-7 text-xs w-[60px]"
          />
        </ConfigBox>

        <ConfigBox label={tr("手续费%")}>
          <Input
            type="number"
            min={0}
            max={2}
            step={0.01}
            value={mergedConfig.commission}
            onChange={(e) => handleFieldChange("commission", Number(e.target.value))}
            disabled={disabled}
            className="h-7 text-xs w-[70px]"
          />
        </ConfigBox>

        <ConfigBox label={tr("印花税%")}>
          <Input
            type="number"
            min={0}
            max={1}
            step={0.01}
            value={mergedConfig.stampDuty}
            onChange={(e) => handleFieldChange("stampDuty", Number(e.target.value))}
            disabled={disabled}
            className="h-7 text-xs w-[70px]"
          />
        </ConfigBox>

        {onLoadData && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 px-3 text-xs"
            onClick={onLoadData}
            disabled={disabled || loading}
          >
            <BarChart3 className="h-3 w-3 mr-1" />
            {tr("加载行情")}
          </Button>
        )}

        <Button
          type="button"
          size="sm"
          className="h-7 px-4 text-xs bg-emerald-600 hover:bg-emerald-700"
          onClick={onRunBacktest}
          disabled={disabled || loading}
        >
          {loading ? (
            <Loader2 className="h-3 w-3 animate-spin mr-1" />
          ) : (
            <Play className="h-3 w-3 mr-1" />
          )}
          {tr("开始回测")}
        </Button>
      </div>
    </div>
  )
}

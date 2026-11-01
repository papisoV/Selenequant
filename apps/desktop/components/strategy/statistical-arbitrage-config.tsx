"use client"

import React, { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useUiText } from "@/components/i18n/use-ui-text"

interface PairsTradingConfigProps {
  onConfigChange: (config: PairsTradingConfig) => void
  initialConfig?: Partial<PairsTradingConfig>
}

export interface PairsTradingConfig {
  symbol_a: string
  symbol_b: string
  entry_z: number
  exit_z: number
  hurst_threshold: number
  lookback: number
}

export function PairsTradingConfigPanel({ onConfigChange, initialConfig }: PairsTradingConfigProps) {
  const tr = useUiText()

  const [config, setConfig] = React.useState<PairsTradingConfig>({
    symbol_a: initialConfig?.symbol_a || "BTC/USDT",
    symbol_b: initialConfig?.symbol_b || "ETH/USDT",
    entry_z: initialConfig?.entry_z || 2.0,
    exit_z: initialConfig?.exit_z || 0.5,
    hurst_threshold: initialConfig?.hurst_threshold || 0.5,
    lookback: initialConfig?.lookback || 20,
  })

  const handleChange = (key: keyof PairsTradingConfig, value: string | number) => {
    const newConfig = { ...config, [key]: value }
    setConfig(newConfig)
    onConfigChange(newConfig)
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{tr("配对交易配置")}</CardTitle>
        <p className="text-[11px] text-muted-foreground">
          {tr("统计套利 — 两标的价差 Z-score 回归")}
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label className="text-xs">{tr("标的 A")}</Label>
            <Input
              value={config.symbol_a}
              onChange={(e) => handleChange("symbol_a", e.target.value)}
              placeholder="BTC/USDT"
              className="h-8 text-sm"
            />
          </div>
          <div>
            <Label className="text-xs">{tr("标的 B")}</Label>
            <Input
              value={config.symbol_b}
              onChange={(e) => handleChange("symbol_b", e.target.value)}
              placeholder="ETH/USDT"
              className="h-8 text-sm"
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label className="text-xs">{tr("入场 Z-score")}</Label>
            <Input
              type="number"
              step="0.1"
              value={config.entry_z}
              onChange={(e) => handleChange("entry_z", parseFloat(e.target.value) || 2.0)}
              className="h-8 text-sm"
            />
          </div>
          <div>
            <Label className="text-xs">{tr("出场 Z-score")}</Label>
            <Input
              type="number"
              step="0.1"
              value={config.exit_z}
              onChange={(e) => handleChange("exit_z", parseFloat(e.target.value) || 0.5)}
              className="h-8 text-sm"
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label className="text-xs">{tr("Hurst 阈值")}</Label>
            <Input
              type="number"
              step="0.05"
              min="0"
              max="1"
              value={config.hurst_threshold}
              onChange={(e) => handleChange("hurst_threshold", parseFloat(e.target.value) || 0.5)}
              className="h-8 text-sm"
            />
            <p className="text-[10px] text-muted-foreground mt-0.5">
              {tr("小于 0.5 表示均值回归倾向")}
            </p>
          </div>
          <div>
            <Label className="text-xs">{tr("回看周期")}</Label>
            <Input
              type="number"
              value={config.lookback}
              onChange={(e) => handleChange("lookback", parseInt(e.target.value) || 20)}
              className="h-8 text-sm"
            />
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

interface HurstReversionConfigProps {
  onConfigChange: (config: HurstReversionConfig) => void
  initialConfig?: Partial<HurstReversionConfig>
}

export interface HurstReversionConfig {
  symbol: string
  hurst_threshold: number
  rsi_entry: number
  rsi_exit: number
  lookback: number
}

export function HurstReversionConfigPanel({ onConfigChange, initialConfig }: HurstReversionConfigProps) {
  const tr = useUiText()

  const [config, setConfig] = React.useState<HurstReversionConfig>({
    symbol: initialConfig?.symbol || "BTC/USDT",
    hurst_threshold: initialConfig?.hurst_threshold || 0.45,
    rsi_entry: initialConfig?.rsi_entry || 30,
    rsi_exit: initialConfig?.rsi_exit || 70,
    lookback: initialConfig?.lookback || 20,
  })

  const handleChange = (key: keyof HurstReversionConfig, value: string | number) => {
    const newConfig = { ...config, [key]: value }
    setConfig(newConfig)
    onConfigChange(newConfig)
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{tr("Hurst 回归配置")}</CardTitle>
        <p className="text-[11px] text-muted-foreground">
          {tr("基于 Hurst 指数的均值回归策略")}
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <div>
          <Label className="text-xs">{tr("标的")}</Label>
          <Input
            value={config.symbol}
            onChange={(e) => handleChange("symbol", e.target.value)}
            placeholder="BTC/USDT"
            className="h-8 text-sm"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label className="text-xs">{tr("Hurst 阈值")}</Label>
            <Input
              type="number"
              step="0.05"
              min="0"
              max="1"
              value={config.hurst_threshold}
              onChange={(e) => handleChange("hurst_threshold", parseFloat(e.target.value) || 0.45)}
              className="h-8 text-sm"
            />
            <p className="text-[10px] text-muted-foreground mt-0.5">
              {tr("小于阈值时触发回归交易")}
            </p>
          </div>
          <div>
            <Label className="text-xs">{tr("回看周期")}</Label>
            <Input
              type="number"
              value={config.lookback}
              onChange={(e) => handleChange("lookback", parseInt(e.target.value) || 20)}
              className="h-8 text-sm"
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label className="text-xs">{tr("RSI 入场")}</Label>
            <Input
              type="number"
              min="0"
              max="50"
              value={config.rsi_entry}
              onChange={(e) => handleChange("rsi_entry", parseInt(e.target.value) || 30)}
              className="h-8 text-sm"
            />
            <p className="text-[10px] text-muted-foreground mt-0.5">
              {tr("超卖入场阈值")}
            </p>
          </div>
          <div>
            <Label className="text-xs">{tr("RSI 出场")}</Label>
            <Input
              type="number"
              min="50"
              max="100"
              value={config.rsi_exit}
              onChange={(e) => handleChange("rsi_exit", parseInt(e.target.value) || 70)}
              className="h-8 text-sm"
            />
            <p className="text-[10px] text-muted-foreground mt-0.5">
              {tr("超买卖出阈值")}
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

"use client"

import { useCallback, type ReactNode } from "react"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"
import { isRecord } from "@/lib/strategy-utils"
import type { StrategyData } from "./types"
import { deriveParamField } from "./param-schema"

const PARAM_DESCRIPTIONS_ZH: Record<string, string> = {
  sma_short: "短期SMA周期",
  sma_long: "长期SMA周期",
  ema_fast: "快线EMA周期",
  ema_slow: "慢线EMA周期",
  adx_period: "ADX计算周期",
  adx_threshold: "ADX趋势强度阈值",
  adx_range_threshold: "ADX震荡阈值",
  atr_period: "ATR计算周期",
  atr_multiplier: "ATR倍数",
  atr_len: "ATR长度",
  macd_fast: "MACD快线周期",
  macd_slow: "MACD慢线周期",
  macd_signal: "MACD信号线周期",
  rsi_period: "RSI计算周期",
  rsi_oversold: "RSI超卖阈值",
  rsi_overbought: "RSI超买阈值",
  stoch_k: "%K周期",
  stoch_d: "%D周期",
  stoch_smooth: "平滑周期",
  stoch_oversold: "超卖阈值",
  stoch_overbought: "超买阈值",
  kdj_n: "KDJ周期N",
  kdj_m1: "KDJ周期M1",
  kdj_m2: "KDJ周期M2",
  kdj_oversold: "KDJ超卖阈值",
  kdj_overbought: "KDJ超买阈值",
  bb_period: "布林带周期",
  bb_std: "布林带标准差倍数",
  ma_len: "均线周期",
  ma_type: "均线类型",
  deviation: "偏离K线数",
  hold_bars: "持仓K线数",
  breakthrough_lookback: "突破回看周期",
  stop_loss_pct: "止损百分比",
  take_profit_pct: "止盈百分比",
  enable_long: "启用做多",
  enable_short: "启用做空",
  sentiment_threshold_positive: "正面情绪阈值",
  sentiment_threshold_negative: "负面情绪阈值",
  lookback_period: "回看周期",
  aggregation_method: "聚合方法",
  max_position_pct: "最大仓位比例",
  max_open_positions: "最大持仓数量",
}

const RISK_PARAM_DESCRIPTIONS_ZH: Record<string, string> = {
  stop_loss_pct: "止损比例",
  take_profit_pct: "止盈比例",
  max_position_pct: "最大仓位比例",
  max_open_positions: "最大持仓数量",
}

export interface ParamPanelProps {
  strategy: StrategyData
  onChange: (updated: Partial<StrategyData>) => void
}

type RuleRecord = Record<string, unknown>

function formatRuleLine(rule: unknown): ReactNode {
  if (!isRecord(rule)) return <span className="text-muted-foreground">{String(rule)}</span>
  const ind = String(rule.indicator ?? "")
  const op = String(rule.operator ?? "").replace(/_/g, " ")
  const comp = rule.comparator
  const compRec = isRecord(comp) ? comp : undefined
  const compInd = compRec ? String(compRec.indicator ?? "") : ""
  return (
    <>
      {ind}{" "}
      <span className="text-primary">{op}</span>
      {compInd ? ` ${compInd}` : ""}
    </>
  )
}

interface RulesSectionProps {
  title: string
  rules: Record<string, unknown> | undefined
  dirHeadingClass: string
  emptyLabel: string
}

function RulesReadonlySection(props: RulesSectionProps) {
  const { title, rules, dirHeadingClass, emptyLabel } = props
  const entries = Object.entries(rules ?? {})
  return (
    <div className="mt-4 space-y-2">
      <h3 className="text-xs font-medium uppercase text-muted-foreground">{title}</h3>
      {entries.length === 0 ? (
        <p className="text-xs text-muted-foreground">{emptyLabel}</p>
      ) : (
        entries.map(([dir, block]) => (
          <div key={dir} className="space-y-1 rounded bg-muted/30 p-2 font-mono text-xs">
            <div className={cn("font-medium", dirHeadingClass)}>{dir}:</div>
            {Array.isArray(block) ? (
              block.map((rule, i) => (
                <div key={i} className="ml-2 text-muted-foreground">
                  {formatRuleLine(rule)}
                </div>
              ))
            ) : (
              <div className="ml-2 text-muted-foreground">{String(block)}</div>
            )}
          </div>
        ))
      )}
    </div>
  )
}

export function ParamPanel({ strategy, onChange }: ParamPanelProps) {
  const params = strategy.parameters ?? {}
  const paramEntries = Object.entries(params)
  const riskEntries = Object.entries(strategy.risk_params ?? {})

  const getParamLabel = (key: string) => PARAM_DESCRIPTIONS_ZH[key] || key
  const getRiskParamLabel = (key: string) => RISK_PARAM_DESCRIPTIONS_ZH[key] || key

  const handleParamChange = useCallback(
    (key: string, next: unknown) => {
      onChange({
        parameters: { ...params, [key]: next },
      })
    },
    [onChange, params]
  )

  const handleRiskChange = useCallback(
    (key: string, num: number) => {
      onChange({
        risk_params: { ...(strategy.risk_params ?? {}), [key]: num },
      })
    },
    [onChange, strategy.risk_params]
  )

  return (
    <div className="space-y-6">
      <div className="space-y-4">
        <h3 className="text-xs font-medium uppercase text-muted-foreground">参数</h3>
        {paramEntries.length === 0 ? (
          <p className="text-xs text-muted-foreground">无参数</p>
        ) : (
          paramEntries.map(([key, value]) => (
            <div key={key} className="space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <Label className="text-xs font-normal text-muted-foreground">{getParamLabel(key)}</Label>
                <span className="shrink-0 font-mono text-xs">{String(value)}</span>
              </div>
              {(() => {
                const field = deriveParamField(key, value)
                if (field.kind === "number") {
                  const { min, max, step } = field.bounds!
                  const num = value as number
                  return (
                    <div className="flex items-center gap-2">
                      <input
                        type="range"
                        min={min}
                        max={max}
                        step={step}
                        value={num}
                        onChange={(e) => handleParamChange(key, parseFloat(e.target.value))}
                        className="h-1.5 flex-1 accent-primary"
                      />
                      <input
                        type="number"
                        value={Number.isFinite(num) ? num : 0}
                        onChange={(e) => handleParamChange(key, parseFloat(e.target.value) || 0)}
                        className="h-7 w-16 rounded border border-border bg-muted/30 px-2 text-center font-mono text-xs"
                      />
                    </div>
                  )
                }
                if (field.kind === "enum") {
                  const str = value as string
                  return (
                    <select
                      value={str}
                      onChange={(e) => handleParamChange(key, e.target.value)}
                      className="h-7 w-full rounded border border-border bg-muted/30 px-2 font-mono text-xs"
                    >
                      {field.options!.map((opt) => (
                        <option key={opt} value={opt}>
                          {opt}
                        </option>
                      ))}
                    </select>
                  )
                }
                if (field.kind === "boolean") {
                  const bool = value as boolean
                  return (
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={bool}
                        onCheckedChange={(checked) => handleParamChange(key, checked)}
                        id={`param-${key}`}
                      />
                      <Label htmlFor={`param-${key}`} className="text-xs font-normal text-muted-foreground">
                        {String(bool)}
                      </Label>
                    </div>
                  )
                }
                if (field.kind === "string") {
                  const str = value as string
                  return (
                    <Input
                      type="text"
                      value={str}
                      onChange={(e) => handleParamChange(key, e.target.value)}
                      className="h-7 w-full font-mono text-xs"
                    />
                  )
                }
                return (
                  <Badge variant="outline" className="text-[10px] font-mono">
                    {JSON.stringify(value)}
                  </Badge>
                )
              })()}
            </div>
          ))
        )}
      </div>

      <RulesReadonlySection
        title="入场规则"
        rules={strategy.entry_rules}
        dirHeadingClass="text-emerald-400"
        emptyLabel="无入场规则"
      />

      <RulesReadonlySection
        title="出场规则"
        rules={strategy.exit_rules}
        dirHeadingClass="text-red-400"
        emptyLabel="无出场规则"
      />

      <div className="space-y-3">
        <h3 className="text-xs font-medium uppercase text-muted-foreground">风控参数</h3>
        {riskEntries.length === 0 ? (
          <p className="text-xs text-muted-foreground">无风控参数</p>
        ) : (
          riskEntries.map(([key, value]) => {
            const num =
              typeof value === "number" && Number.isFinite(value)
                ? value
                : typeof value === "string"
                  ? parseFloat(value)
                  : NaN
            const safe = Number.isFinite(num) ? num : 0
            return (
              <div key={key} className="space-y-1.5">
                <Label className="text-xs font-normal text-muted-foreground">{getRiskParamLabel(key)}</Label>
                <Input
                  type="number"
                  value={safe}
                  onChange={(e) => handleRiskChange(key, parseFloat(e.target.value) || 0)}
                  className="h-8 font-mono text-xs"
                />
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

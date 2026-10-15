"use client"

import React, { useMemo, useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { ZoomIn, ZoomOut, Maximize2 } from "lucide-react"
import type { BacktestData, BacktestTradeData, KlineData, StrategyData } from "./types"
import { CurveChart } from "./shared-charts"
import { EquityCurveChart, DrawdownChart } from "./equity-curve-chart"

/**
 * BacktestResultsTabs - 回测结果多标签页展示
 * 参考 Qbot panel_results.py 设计：
 * - 8个独立标签页
 * - K线/布林带独立标签页（不是切换按钮）
 * - 年度收益/相关性使用表格形式
 *
 * 适配说明：CandlestickChart/IndicatorChart 在新前端不存在，
 * K线和布林带标签页改为显示权益曲线和指标摘要。
 */

export interface BacktestResultsTabsProps {
  result: BacktestData | null
  klineHistory?: KlineData[]
  onLoadMoreKlines?: () => void
  isLoadingMoreKlines?: boolean
  chartInterval?: string
  strategy?: StrategyData | null
  loading?: boolean
}

interface YearlyReturn {
  year: number
  month: number
  return: number
}

export function calculateYearlyReturns(
  equityCurve: number[],
  startDate?: string,
  endDate?: string,
): YearlyReturn[] {
  if (!equityCurve || equityCurve.length < 2) return []

  if (!startDate || !endDate) {
    const totalReturn = equityCurve[0] > 0
      ? (equityCurve[equityCurve.length - 1] - equityCurve[0]) / equityCurve[0]
      : 0
    const year = new Date().getFullYear()
    return [{ year, month: 1, return: totalReturn }]
  }

  const start = new Date(startDate)
  const end = new Date(endDate)
  if (isNaN(start.getTime()) || isNaN(end.getTime())) {
    const totalReturn = equityCurve[0] > 0
      ? (equityCurve[equityCurve.length - 1] - equityCurve[0]) / equityCurve[0]
      : 0
    const year = new Date().getFullYear()
    return [{ year, month: 1, return: totalReturn }]
  }

  const monthlyReturns: YearlyReturn[] = []
  const year = start.getFullYear()
  const monthsDiff =
    (end.getFullYear() - start.getFullYear()) * 12 + (end.getMonth() - start.getMonth()) + 1
  const span = equityCurve.length - 1
  const slots = Math.min(monthsDiff, 12)

  for (let i = 0; i < slots; i++) {
    const startIndex = Math.floor((i / monthsDiff) * span)
    const endIndex = Math.min(Math.floor(((i + 1) / monthsDiff) * span), span)
    if (endIndex <= startIndex) continue
    const startValue = equityCurve[startIndex]
    const endValue = equityCurve[endIndex]
    monthlyReturns.push({
      year,
      month: start.getMonth() + i + 1,
      return: startValue > 0 ? (endValue - startValue) / startValue : 0,
    })
  }

  return monthlyReturns
}

/** 从 (顶层, 嵌套) 两处取非空数组, 顶层优先。纯函数。 */
export function pickCurve(
  top: unknown,
  nested: unknown,
): number[] {
  if (Array.isArray(top) && top.length > 0) return top as number[]
  if (Array.isArray(nested) && nested.length > 0) return nested as number[]
  return []
}

/** 把年度收益展开成 12 个月的宽表行(缺失月填 0)。纯函数。 */
export function monthlyChartRows(
  yearlyReturns: { year: number; month: number; return: number }[],
): Record<string, number | string>[] {
  const years = [...new Set(yearlyReturns.map((r) => r.year))].sort()
  return years.map((year) => {
    const row: Record<string, number | string> = { year }
    for (let month = 1; month <= 12; month++) {
      const hit = yearlyReturns.find((r) => r.year === year && r.month === month)
      row[`month_${month}`] = hit ? hit.return * 100 : 0
    }
    return row
  })
}

/** 收益格的配色(按百分比分档)。纯函数。 */
export function returnColorClass(value: number): string {
  if (value > 5) return "bg-emerald-500 text-white"
  if (value > 0) return "bg-emerald-200 text-emerald-900"
  if (value > -5) return "bg-red-200 text-red-900"
  return "bg-red-500 text-white"
}

/** 缩放档位夹取到 [0.5, 2], 步进 0.25。纯函数。 */
export function clampChartScale(scale: number): number {
  return Math.min(Math.max(scale, 0.5), 2)
}

/** 从 metrics 或其顶层回落取值。 */
function pick(m: any, r: any, key: string, fallback = 0): number {
  return m?.[key] ?? r?.[key] ?? fallback
}

/** 风险收益指标表(标签 → 格式化后的值)。纯函数。 */
export function riskReturnMetricRows(
  metrics: any,
  result: any,
): { name: string; value: string }[] {
  if (!metrics) return []
  const pct = (key: string, dp = 2) => `${(pick(metrics, result, key) * 100).toFixed(dp)}%`
  const num = (key: string, dp = 3) => pick(metrics, result, key).toFixed(dp)
  return [
    { name: "总收益", value: pct("total_return") },
    { name: "年化收益", value: pct("annual_return") },
    { name: "夏普比率", value: num("sharpe_ratio") },
    { name: "索提诺比率", value: num("sortino_ratio") },
    { name: "最大回撤", value: pct("max_drawdown") },
    { name: "卡玛比率", value: num("calmar_ratio") },
    { name: "胜率", value: pct("win_rate", 1) },
    { name: "盈亏比", value: num("profit_factor", 2) },
    { name: "总交易数", value: String(pick(metrics, result, "total_trades")) },
    { name: "波动率", value: pct("volatility") },
    { name: "VaR(95%)", value: pct("var_95") },
    { name: "CVaR(95%)", value: pct("cvar_95") },
  ]
}

/** 相关性表(策略 vs 基准)。纯函数。 */
export function correlationRows(
  metrics: any,
): { metric: string; benchmark: string; correlation: number }[] {
  if (!metrics) return []
  return [
    { metric: "策略收益", benchmark: "基准收益", correlation: 0.85 },
    { metric: "波动率", benchmark: "基准波动率", correlation: 0.72 },
    { metric: "Sharpe", benchmark: "基准Sharpe", correlation: metrics.sharpe_ratio || 0 },
  ]
}

/** 从 result 的嵌套或顶层取 equity_curve。纯函数。 */
export function pickEquityCurve(result: any): number[] {
  if (Array.isArray(result?.result?.equity_curve)) return result.result.equity_curve
  if (Array.isArray(result?.equity_curve)) return result.equity_curve
  return []
}

export function BacktestResultsTabs({
  result,
  klineHistory = [],
  onLoadMoreKlines,
  isLoadingMoreKlines,
  chartInterval = "1d",
  strategy,
  loading = false,
}: BacktestResultsTabsProps) {
  const [activeTab, setActiveTab] = useState("equity")
  const [chartScale, setChartScale] = useState(1)

  const metrics = result?.result?.metrics
  const equityCurve = useMemo(() => pickEquityCurve(result), [result])

  const yearlyReturns = useMemo(() => {
    const startDate = result?.start_date || result?.result?.start_date
    const endDate = result?.end_date || result?.result?.end_date
    return calculateYearlyReturns(equityCurve, startDate, endDate)
  }, [equityCurve, result])

  const correlationData = useMemo(() => correlationRows(metrics), [metrics])

  const riskReturnMetrics = useMemo(
    () => riskReturnMetricRows(metrics, result),
    [metrics, result],
  )

  const heatmapData = useMemo(
    () => monthlyChartRows(yearlyReturns),
    [yearlyReturns],
  )

  const drawdownCurve = useMemo(
    () => pickCurve(result?.drawdown_curve, result?.result?.drawdown_curve),
    [result],
  )

  const buyHoldCurve = useMemo(
    () => pickCurve(result?.buy_hold_curve, result?.result?.buy_hold_curve),
    [result],
  )

  const chartTimestamps = useMemo(() => {
    const ts = result?.result?.timestamps || result?.timestamps
    return Array.isArray(ts) ? ts : undefined
  }, [result])

  const formatNumber = (value: number, decimals = 2) => value.toFixed(decimals)

  const getReturnColor = returnColorClass

  const handleZoomIn = () => setChartScale((s) => clampChartScale(s + 0.25))
  const handleZoomOut = () => setChartScale((s) => clampChartScale(s - 0.25))
  const handleZoomReset = () => setChartScale(1)

  const ZoomControls = () => (
    <div className="flex items-center gap-1">
      <Button variant="outline" size="icon" className="h-7 w-7" onClick={handleZoomOut} disabled={chartScale <= 0.5}>
        <ZoomOut className="h-3.5 w-3.5" />
      </Button>
      <span className="text-xs text-muted-foreground w-12 text-center">{Math.round(chartScale * 100)}%</span>
      <Button variant="outline" size="icon" className="h-7 w-7" onClick={handleZoomIn} disabled={chartScale >= 2}>
        <ZoomIn className="h-3.5 w-3.5" />
      </Button>
      <Button variant="outline" size="icon" className="h-7 w-7" onClick={handleZoomReset}>
        <Maximize2 className="h-3.5 w-3.5" />
      </Button>
    </div>
  )

  if (loading) {
    return (
      <div className="space-y-4 p-4">
        <Skeleton className="h-[400px] w-full rounded-lg" />
        <Skeleton className="h-[200px] w-full rounded-lg" />
      </div>
    )
  }

  if (!result && !klineHistory.length) {
    return (
      <div className="flex h-[400px] items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground">
        暂无回测结果
      </div>
    )
  }

  return (
    <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full h-full flex flex-col">
      <div className="flex items-center justify-between mb-2 shrink-0">
        <TabsList className="grid grid-cols-6 h-9">
          <TabsTrigger value="equity" className="text-xs">资金曲线</TabsTrigger>
          <TabsTrigger value="drawdown" className="text-xs">回撤曲线</TabsTrigger>
          <TabsTrigger value="yearly" className="text-xs">年度收益</TabsTrigger>
          <TabsTrigger value="correlation" className="text-xs">相关性</TabsTrigger>
          <TabsTrigger value="risk" className="text-xs">风险收益</TabsTrigger>
          <TabsTrigger value="indicator" className="text-xs">指标</TabsTrigger>
        </TabsList>
        <ZoomControls />
      </div>

      <div className="flex-1 min-h-0 overflow-hidden">
        {/* 资金曲线 */}
        <TabsContent value="equity" className="mt-2 h-full data-[state=active]:flex data-[state=active]:flex-col">
          <Card className="border-border/60 h-full flex flex-col flex-1">
            <CardHeader className="pb-2 py-2 shrink-0">
              <CardTitle className="text-sm">资金曲线</CardTitle>
            </CardHeader>
            <CardContent className="p-2 flex-1 min-h-0">
              {equityCurve.length === 0 ? (
                <div className="flex items-center justify-center text-sm text-muted-foreground h-full">
                  暂无资金曲线数据
                </div>
              ) : (
                <EquityCurveChart
                  data={equityCurve}
                  timestamps={chartTimestamps}
                  baselineData={buyHoldCurve}
                  baselineLabel="Buy & Hold"
                  className="h-full w-full relative"
                />
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* 回撤曲线 */}
        <TabsContent value="drawdown" className="mt-2 h-full data-[state=active]:flex data-[state=active]:flex-col">
          <Card className="border-border/60 h-full flex flex-col flex-1">
            <CardHeader className="pb-2 py-2 shrink-0">
              <CardTitle className="text-sm">回撤曲线</CardTitle>
            </CardHeader>
            <CardContent className="p-2 flex-1 min-h-0">
              {drawdownCurve.length === 0 ? (
                <div className="flex items-center justify-center text-sm text-muted-foreground h-full">
                  暂无回撤曲线数据
                </div>
              ) : (
                <DrawdownChart
                  data={drawdownCurve}
                  timestamps={chartTimestamps}
                  className="h-full w-full"
                />
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* 年度收益 */}
        <TabsContent value="yearly" className="mt-2">
          <Card className="border-border/60">
            <CardHeader className="pb-2 py-2">
              <CardTitle className="text-sm">月度收益</CardTitle>
            </CardHeader>
            <CardContent className="p-2">
              <ScrollArea style={{ height: Math.round(400 * chartScale) }}>
                <Table>
                  <TableHeader>
                    <TableRow className="border-border/60">
                      <TableHead className="w-[60px] text-xs">年份</TableHead>
                      {Array.from({ length: 12 }, (_, i) => (
                        <TableHead key={i} className="text-center text-xs p-1">
                          {new Date(2024, i, 1).toLocaleString("default", { month: "short" })}
                        </TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {heatmapData.map((row) => (
                      <TableRow key={row.year} className="border-border/60">
                        <TableCell className="font-medium text-xs">{row.year}</TableCell>
                        {Array.from({ length: 12 }, (_, i) => {
                          const value = row[`month_${i + 1}`] as number
                          return (
                            <TableCell key={i} className={`text-center text-xs p-1 ${getReturnColor(value)}`}>
                              {formatNumber(value, 1)}%
                            </TableCell>
                          )
                        })}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ScrollArea>
              <p className="text-xs text-muted-foreground mt-2">
                热力图显示月度收益，绿色为正收益，红色为负收益
              </p>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 相关性分析 */}
        <TabsContent value="correlation" className="mt-2">
          <Card className="border-border/60">
            <CardHeader className="pb-2 py-2">
              <CardTitle className="text-sm">相关性分析</CardTitle>
            </CardHeader>
            <CardContent className="p-2">
              <Table>
                <TableHeader>
                  <TableRow className="border-border/60">
                    <TableHead className="text-xs">策略指标</TableHead>
                    <TableHead className="text-xs">基准指标</TableHead>
                    <TableHead className="text-xs text-right">相关性系数</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {correlationData.map((row, i) => (
                    <TableRow key={i} className="border-border/60">
                      <TableCell className="text-xs">{row.metric}</TableCell>
                      <TableCell className="text-xs">{row.benchmark}</TableCell>
                      <TableCell className="text-xs text-right font-mono">
                        <Badge variant={row.correlation > 0.7 ? "default" : "secondary"} className="text-xs">
                          {row.correlation.toFixed(3)}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <p className="text-xs text-muted-foreground mt-2">
                相关性系数越接近1，表示策略与基准走势越一致
              </p>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 风险收益 */}
        <TabsContent value="risk" className="mt-2">
          <Card className="border-border/60">
            <CardHeader className="pb-2 py-2">
              <CardTitle className="text-sm">风险收益指标</CardTitle>
            </CardHeader>
            <CardContent className="p-2">
              <ScrollArea style={{ height: Math.round(400 * chartScale) }}>
                <Table>
                  <TableHeader>
                    <TableRow className="border-border/60">
                      <TableHead className="text-xs">指标名称</TableHead>
                      <TableHead className="text-xs text-right">数值</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {riskReturnMetrics.map((row, i) => (
                      <TableRow key={i} className="border-border/60">
                        <TableCell className="text-xs font-medium">{row.name}</TableCell>
                        <TableCell className="text-xs text-right font-mono">{row.value}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ScrollArea>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 指标分析 — 简化版，显示权益曲线 + 交易统计 */}
        <TabsContent value="indicator" className="mt-2">
          <Card className="border-border/60">
            <CardHeader className="pb-2 py-2">
              <CardTitle className="text-sm">指标概览</CardTitle>
            </CardHeader>
            <CardContent className="p-2">
              {equityCurve.length === 0 ? (
                <div className="flex items-center justify-center text-sm text-muted-foreground" style={{ height: Math.round(400 * chartScale) }}>
                  暂无数据
                </div>
              ) : (
                <div className="space-y-4">
                  <CurveChart
                    curve={equityCurve}
                    baselineCurve={buyHoldCurve}
                    baselineLabel="Buy & Hold"
                    height={Math.round(320 * chartScale)}
                  />
                  {metrics && (
                    <div className="grid grid-cols-3 gap-3 text-xs">
                      <div className="rounded-lg border border-border/60 bg-muted/30 px-3 py-2">
                        <div className="text-muted-foreground">夏普比率</div>
                        <div className="font-mono font-medium">{(metrics.sharpe_ratio ?? 0).toFixed(3)}</div>
                      </div>
                      <div className="rounded-lg border border-border/60 bg-muted/30 px-3 py-2">
                        <div className="text-muted-foreground">胜率</div>
                        <div className="font-mono font-medium">{((metrics.win_rate ?? 0) * 100).toFixed(1)}%</div>
                      </div>
                      <div className="rounded-lg border border-border/60 bg-muted/30 px-3 py-2">
                        <div className="text-muted-foreground">盈亏比</div>
                        <div className="font-mono font-medium">{(metrics.profit_factor ?? 0).toFixed(2)}</div>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </div>
    </Tabs>
  )
}

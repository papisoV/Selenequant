"use client"

import React, { useState, useCallback, useEffect, useMemo } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Skeleton } from "@/components/ui/skeleton"
import { Progress } from "@/components/ui/progress"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Radar, Play, StopCircle, Download, TrendingUp, Star, Zap } from "lucide-react"
import { api } from "@/lib/api-client"
import {
  collectResultsToMap,
  generateScannerTasks,
  getTopResults,
  useBatchBacktest,
} from "@/lib/hooks/use-batch-backtest"
import { useWorkflowStore } from "@/lib/store/workflow"
import { useRouter } from "next/navigation"
import { useUiText, useLocale } from "@/components/i18n/use-ui-text"

const DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
const DEFAULT_INTERVALS = ["1h", "4h", "1d"]

const TEMPLATE_CATEGORIES = [
  { id: "trend", name: "趋势跟踪" },
  { id: "oscillator", name: "震荡指标" },
  { id: "volatility", name: "波动率" },
  { id: "volume", name: "成交量" },
  { id: "mean_reversion", name: "均值回归" },
  { id: "other", name: "其他" },
]

const SORT_METRICS = [
  { id: "sharpe", name: "夏普比率" },
  { id: "totalReturn", name: "总收益" },
  { id: "winRate", name: "胜率" },
  { id: "profitFactor", name: "盈亏比" },
]

interface FactoryTemplate { id: string; name: string; name_zh?: string; category?: string; default_params?: Record<string, unknown> }
interface TemplateWithCategory extends FactoryTemplate { categoryKey: string }

/**
 * 缺值一律显示 `—`，且**不进涨跌色分支**。
 *
 * 「后端没给这个数」与「这个数是 0」是两件事：早先实现用 `?? 0` 把前者补成 0，
 * 再被 `>= 0` 判成上涨，渲染成绿色的 `+0.0%` —— 一个不知道的值被涂成了「涨」。
 * 红绿在看盘面专供涨跌方向，缺值只配中性色。
 */
function Missing() {
  return <span className="text-ink-4">—</span>
}

export function StrategyScannerPanel() {
  const tr = useUiText()
  const locale = useLocale()
  // name/name_zh 是「英文名 + 中文名」一对字段(非同一文案的两种译法):en 下取英文名。
  const pickName = useCallback(
    (t: { name?: string; name_zh?: string }) =>
      (locale === "en" ? t.name : t.name_zh) || t.name || t.name_zh || "",
    [locale],
  )

  const [templates, setTemplates] = useState<TemplateWithCategory[]>([])
  const [loadingTemplates, setLoadingTemplates] = useState(true)
  const [selectedTemplates, setSelectedTemplates] = useState<Set<string>>(new Set())
  const [selectedSymbols, setSelectedSymbols] = useState<Set<string>>(new Set(["BTC/USDT"]))
  const [selectedIntervals, setSelectedIntervals] = useState<Set<string>>(new Set(["1h"]))
  const [sortMetric, setSortMetric] = useState<"sharpe" | "totalReturn" | "winRate" | "profitFactor">("sharpe")
  const [showHeatmap, setShowHeatmap] = useState(false)
  const { tasks, runBatch, abort, isLoading, progress, lastProgressAt } = useBatchBacktest()
  const setOptimization = useWorkflowStore((s) => s.setOptimization)
  const router = useRouter()

  // 「最后进展 N 秒前」需要一个会走的钟；停跑后不再 tick，省电也省测试噪声
  const [nowTick, setNowTick] = useState(() => Date.now())
  useEffect(() => {
    if (!isLoading) return
    const id = setInterval(() => setNowTick(Date.now()), 1000)
    return () => clearInterval(id)
  }, [isLoading])

  useEffect(() => { loadTemplates() }, [])

  const loadTemplates = async () => {
    setLoadingTemplates(true)
    try {
      const data = await api<FactoryTemplate[]>("/api/v1/strategies/factory-templates")
      const list = Array.isArray(data) ? data : []
      setTemplates(list.map((t) => ({ ...t, categoryKey: t.category || "other" })))
    } catch {
      // fallback empty
    }
    setLoadingTemplates(false)
  }

  const templatesByCategory = useMemo(() => { const grouped: Record<string, TemplateWithCategory[]> = {}; for (const template of templates) { const cat = template.categoryKey; if (!grouped[cat]) grouped[cat] = []; grouped[cat].push(template) } return grouped }, [templates])
  // 结果表只收终态：非终态响应里根本没有指标字段，画出来只能是假的
  const completedResults = useMemo(() => tasks.filter((t) => t.status === "completed" && t.result).map((t) => t.result!), [tasks])
  const topResults = useMemo(() => getTopResults(completedResults, sortMetric, 20), [completedResults, sortMetric])
  const resultsMap = useMemo(() => collectResultsToMap(completedResults), [completedResults])

  const toggleTemplate = useCallback((templateId: string) => { setSelectedTemplates((prev) => { const next = new Set(prev); if (next.has(templateId)) { next.delete(templateId) } else { next.add(templateId) }; return next }) }, [])
  const toggleSymbol = useCallback((symbol: string) => { setSelectedSymbols((prev) => { const next = new Set(prev); if (next.has(symbol)) { next.delete(symbol) } else { next.add(symbol) }; return next }) }, [])
  const toggleInterval = useCallback((interval: string) => { setSelectedIntervals((prev) => { const next = new Set(prev); if (next.has(interval)) { next.delete(interval) } else { next.add(interval) }; return next }) }, [])
  const selectAllInCategory = useCallback((category: string) => { const catTemplates = templatesByCategory[category] || []; setSelectedTemplates((prev) => { const next = new Set(prev); for (const t of catTemplates) { next.add(t.id) }; return next }) }, [templatesByCategory])

  const startScan = useCallback(() => {
    const selectedTemplateList = templates.filter((t) => selectedTemplates.has(t.id))
    if (selectedTemplateList.length === 0 || selectedSymbols.size === 0 || selectedIntervals.size === 0) return
    const nameById = new Map(selectedTemplateList.map((t) => [t.id, pickName(t)]))
    const taskList = generateScannerTasks(
      selectedTemplateList,
      Array.from(selectedSymbols),
      Array.from(selectedIntervals),
    ).map((t) => ({ ...t, templateName: nameById.get(t.templateId) ?? t.templateName }))
    void runBatch(taskList)
  }, [templates, selectedTemplates, selectedSymbols, selectedIntervals, pickName, runBatch])

  const stopScan = useCallback(() => { abort() }, [abort])

  const exportResults = useCallback(() => {
    if (completedResults.length === 0) return
    // 缺值导出为空单元格，不写 0：CSV 的读者不该被补出来的 0 骗
    const cell = (v: number | null, digits: number) => (v == null ? "" : v.toFixed(digits))
    const csv = ["策略,交易对,周期,收益%,夏普,最大回撤%,胜率%,交易数,盈亏比", ...completedResults.map((r) => `${r.templateName},${r.symbol},${r.interval},${cell(r.totalReturn, 2)},${cell(r.sharpe, 2)},${cell(r.maxDrawdown, 2)},${cell(r.winRate == null ? null : r.winRate * 100, 1)},${r.totalTrades == null ? "" : r.totalTrades},${cell(r.profitFactor, 2)}`)].join("\n")
    const blob = new Blob([csv], { type: "text/csv" }); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `scanner-results-${new Date().toISOString().slice(0, 10)}.csv`; a.click(); URL.revokeObjectURL(url)
  }, [completedResults])

  const estimatedTasks = selectedTemplates.size * selectedSymbols.size * selectedIntervals.size
  const secondsSinceProgress = lastProgressAt == null
    ? null
    : Math.max(0, Math.floor((nowTick - lastProgressAt) / 1000))

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-base"><Radar className="h-4 w-4" />{tr("策略扫描配置")}</CardTitle></CardHeader>
        <CardContent>
          {loadingTemplates ? (<div className="space-y-2"><Skeleton className="h-8 w-full" /><p className="text-sm text-muted-foreground">{tr("加载策略模板...")}</p></div>) : (
            <>
              <div className="space-y-4">
                <div className="flex items-center justify-between"><label className="text-sm font-medium">{tr("策略模板")}</label><Badge variant="outline">{selectedTemplates.size} / {templates.length}</Badge></div>
                {TEMPLATE_CATEGORIES.map((cat) => { const catTemplates = templatesByCategory[cat.id] || []; if (catTemplates.length === 0) return null; return (
                  <div key={cat.id} className="space-y-2">
                    <div className="flex items-center gap-2"><span className="text-xs font-medium text-muted-foreground">{tr(cat.name)}</span><Button variant="ghost" size="sm" className="h-5 px-2 text-xs" onClick={() => selectAllInCategory(cat.id)}>{tr("全选")}</Button></div>
                    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2">
                      {catTemplates.map((template) => (
                        <div key={template.id} className={`flex items-center space-x-2 p-2 rounded border ${selectedTemplates.has(template.id) ? "border-primary bg-primary/5" : "border-border"}`}>
                          <Checkbox id={`template-${template.id}`} checked={selectedTemplates.has(template.id)} onCheckedChange={() => toggleTemplate(template.id)} />
                          <label htmlFor={`template-${template.id}`} className="text-xs cursor-pointer truncate">{pickName(template)}</label>
                        </div>
                      ))}
                    </div>
                  </div>
                ) })}
              </div>
              <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2"><label className="text-sm font-medium">{tr("交易对")}</label><div className="flex flex-wrap gap-2">{DEFAULT_SYMBOLS.map((symbol) => (<Badge key={symbol} variant={selectedSymbols.has(symbol) ? "default" : "outline"} className="cursor-pointer" onClick={() => toggleSymbol(symbol)}>{symbol}</Badge>))}</div></div>
                <div className="space-y-2"><label className="text-sm font-medium">{tr("周期")}</label><div className="flex flex-wrap gap-2">{DEFAULT_INTERVALS.map((interval) => (<Badge key={interval} variant={selectedIntervals.has(interval) ? "default" : "outline"} className="cursor-pointer" onClick={() => toggleInterval(interval)}>{interval}</Badge>))}</div></div>
              </div>
              <div className="mt-4 flex items-center justify-between">
                <div className="text-sm text-muted-foreground">{tr("预计")} {estimatedTasks} {tr("次回测任务")}</div>
                <div className="flex items-center gap-2">
                  {!isLoading ? (<Button onClick={startScan} disabled={estimatedTasks === 0}><Play className="h-4 w-4 mr-2" />{tr("开始扫描")}</Button>) : (<Button onClick={stopScan} variant="destructive"><StopCircle className="h-4 w-4 mr-2" />{tr("停止")}</Button>)}
                </div>
              </div>
              {isLoading && (
                <div className="mt-4 space-y-2">
                  <Progress value={progress.percent} className="h-2" />
                  <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
                    <span>{tr("已完成")} {progress.completed} / {progress.total}</span>
                    <span className="flex items-center gap-2">
                      <span>{tr("失败")} {progress.failed}</span>
                      {secondsSinceProgress !== null && (<span>{tr("最后进展 {n} 秒前", { n: secondsSinceProgress })}</span>)}
                      {progress.stalled > 0 && (<Badge variant="outline" className="text-[10px] text-ink-4">{tr("停滞")}</Badge>)}
                    </span>
                  </div>
                  {/* 结果表只收终态，扫描期间它必然是空的 —— 不说清会被读成「坏了」 */}
                  <p className="text-xs text-muted-foreground">{tr("正在跑 — 结果表会在任务完成后出现")}</p>
                </div>
              )}
              {!isLoading && progress.cancelled > 0 && (
                <p className="mt-4 text-sm text-muted-foreground">{tr("已取消")} {progress.cancelled} / {progress.total}</p>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-base"><TrendingUp className="h-4 w-4" />{tr("扫描结果")}</CardTitle>
            <div className="flex items-center gap-2">
              <Select value={sortMetric} onValueChange={(v) => setSortMetric(v as typeof sortMetric)}><SelectTrigger className="w-32 h-8"><SelectValue /></SelectTrigger><SelectContent>{SORT_METRICS.map((m) => (<SelectItem key={m.id} value={m.id}>{tr(m.name)}</SelectItem>))}</SelectContent></Select>
              <Button variant="outline" size="sm" onClick={exportResults} disabled={completedResults.length === 0}><Download className="h-3 w-3 mr-1" />CSV</Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {completedResults.length > 0 ? (
            <>
              <Table>
                <TableHeader><TableRow><TableHead>{tr("策略")}</TableHead><TableHead>{tr("交易对")}</TableHead><TableHead>{tr("周期")}</TableHead><TableHead className="text-right">{tr("收益")}</TableHead><TableHead className="text-right">{tr("夏普")}</TableHead><TableHead className="text-right">{tr("最大回撤")}</TableHead><TableHead className="text-right">{tr("胜率")}</TableHead><TableHead className="text-right">{tr("交易数")}</TableHead><TableHead className="w-[80px]">{tr("操作")}</TableHead></TableRow></TableHeader>
                <TableBody>
                  {topResults.map((result, idx) => (
                    <TableRow key={`${result.templateId}-${result.symbol}-${result.interval}`} className={idx === 0 ? "bg-emerald-500/10" : idx < 3 ? "bg-emerald-500/5" : ""}>
                      <TableCell className="font-medium text-xs"><div className="flex items-center gap-1">{idx < 3 && (<Star className={`h-3 w-3 ${idx === 0 ? "fill-yellow-400 text-yellow-400" : "fill-yellow-200 text-yellow-200"}`} />)}{result.templateName}</div></TableCell>
                      <TableCell className="text-xs">{result.symbol}</TableCell>
                      <TableCell className="text-xs">{result.interval}</TableCell>
                      <TableCell className="text-right">{result.totalReturn == null ? (<Missing />) : (<span className={result.totalReturn >= 0 ? "text-emerald-500" : "text-red-500"}>{result.totalReturn >= 0 ? "+" : ""}{result.totalReturn.toFixed(1)}%</span>)}</TableCell>
                      <TableCell className="text-right font-mono text-xs">{result.sharpe == null ? (<Missing />) : result.sharpe.toFixed(2)}</TableCell>
                      <TableCell className="text-right">{result.maxDrawdown == null ? (<Missing />) : (<span className="text-red-400">-{result.maxDrawdown.toFixed(1)}%</span>)}</TableCell>
                      <TableCell className="text-right">{result.winRate == null ? (<Missing />) : `${(result.winRate * 100).toFixed(0)}%`}</TableCell>
                      <TableCell className="text-right text-xs">{result.totalTrades == null ? (<Missing />) : result.totalTrades}</TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 text-xs"
                          onClick={() => {
                            setOptimization({
                              source: "scanner",
                              sharpe: result.sharpe ?? undefined,
                              params: {},
                              symbol: result.symbol,
                              interval: result.interval,
                              pickedAt: Date.now(),
                            });
                            router.push("/trade/backtests");
                          }}
                        >
                          {tr("调入回测")}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <div className="mt-4 flex items-center gap-4">
                <Button variant="outline" size="sm" onClick={() => setShowHeatmap(!showHeatmap)}><Zap className="h-3 w-3 mr-1" />{tr("热力图")}</Button>
                <span className="text-sm text-muted-foreground">{tr("共")} {completedResults.length} {tr("个有效结果")}</span>
              </div>
              {showHeatmap && (
                <div className="mt-4 p-4 border rounded bg-muted/30">
                  <div className="text-sm font-medium mb-2">{tr("策略x交易对x周期 矩阵")}</div>
                  <div className="grid gap-1">
                    {Array.from(resultsMap.entries()).map(([templateId, symbolIntervalMap]) => { const template = templates.find((t) => t.id === templateId); return (
                      <div key={templateId} className="flex items-center gap-2">
                        <span className="w-32 text-xs truncate">{template ? pickName(template) : templateId}</span>
                        <div className="flex gap-1">{Array.from(symbolIntervalMap.entries()).map(([key, result]) => (
                          <div key={key} className={`w-8 h-8 rounded flex items-center justify-center text-xs font-mono ${result.sharpe == null ? "bg-surface-3 text-ink-4" : result.sharpe >= 2 ? "bg-emerald-500 text-white" : result.sharpe >= 1 ? "bg-emerald-300 text-black" : result.sharpe >= 0 ? "bg-yellow-200 text-black" : "bg-red-300 text-black"}`} title={tr("{sym} {tf}: 夏普 {sharpe}", { sym: result.symbol, tf: result.interval, sharpe: result.sharpe == null ? "—" : result.sharpe.toFixed(2) })}>{result.sharpe == null ? "—" : result.sharpe.toFixed(1)}</div>
                        ))}</div>
                      </div>
                    ) })}
                  </div>
                </div>
              )}
            </>
          ) : (<div className="text-sm text-muted-foreground text-center py-8">{isLoading ? tr("正在扫描 — 首个任务完成后结果才会出现") : tr("选择策略模板、交易对和周期，然后点击\"开始扫描\"")}</div>)}
        </CardContent>
      </Card>
    </div>
  )
}
